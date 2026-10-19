"""Daily collection: run every search group of the plan on Apify under the budget caps and add new postings to the
pool (stage `new`, waiting for review).

Window: from max(now - max_lookback_days, min(now - lookback_hours, end of the last complete window - 1h)) to now,
so a missed day is caught up automatically; the very first run looks back first_lookback_hours.

Paid safety:
  - every group's input is checked against the actor's input schema (free) before anything is paid for;
  - each run is capped: maxTotalChargeUsd = min(the group's budget_usd, what today's cap has left);
  - a POST is never retried: a run whose start is uncertain stays LAUNCHING and is adopted later by its start time,
    or marked not started after 30 minutes (reconcile);
  - data/PAUSE (`jobpilot pause`) refuses every paid launch.
One manifest per local day: data/runs/<YYYY-MM-DD>/manifest.json (window, inputs, run ids, costs, row counts).
"""
import gzip
import json
import math
import re
import time
from datetime import timedelta, timezone

from . import budget, config, util
from .apify import Apify, ApifyError, TERMINAL, item_price, load_token, run_cost

DONE = TERMINAL | {"SKIPPED", "NOT_STARTED"}
RECONCILE_MINUTES = 3
ABANDON_MINUTES = 30


# ---------------------------------------------------------------- normalization

def posting_id(item):
    for key in ("linkedin_id", "jobId"):
        value = str(item.get(key) or "")
        if re.fullmatch(r"\d{5,}", value):
            return value
    m = re.search(r"/jobs/view/(?:[^/?]*-)?(\d{5,})(?:[/?]|$)", str(item.get("url") or item.get("link") or ""))
    return m.group(1) if m else None


def _salary(item):
    lo, hi, val = item.get("ai_salary_min_value"), item.get("ai_salary_max_value"), item.get("ai_salary_value")
    unit, cur = item.get("ai_salary_unit_text") or "YEAR", item.get("ai_salary_currency") or ""
    text = ""
    if lo or hi or val:
        fmt = lambda v: ("{:,.0f}".format(v) if isinstance(v, (int, float)) else str(v))  # noqa: E731
        span = "%s-%s" % (fmt(lo), fmt(hi)) if lo and hi else fmt(hi or lo or val)
        text = "%s %s / %s" % (cur, span, unit.lower())
    elif item.get("salary"):
        raw = item["salary"]
        text = raw if isinstance(raw, str) else json.dumps(raw)[:120]
    top = hi or val or lo
    annual = util.annual_usd(top, unit) if top and (cur in ("", "USD")) else None
    return text.strip(), annual


def _join(value):
    if isinstance(value, list):
        return " | ".join(str(v) for v in value if v)
    return str(value or "")


def normalize(item, group, track):
    """(row, jd text) from one actor item. Raises ValueError for an item without a LinkedIn job id."""
    pid = posting_id(item)
    if not pid:
        raise ValueError("item without a LinkedIn job id")
    jd = (item.get("description_text") or "").strip()
    if not jd and item.get("description_html"):
        jd = re.sub(r"<[^>]+>", " ", item["description_html"])
    salary_text, salary_max = _salary(item)
    posted = util.parse_time(item.get("date_posted"))
    recruiter = None
    if item.get("recruiter_name") or item.get("recruiter_url"):
        recruiter = {"name": item.get("recruiter_name") or "", "title": item.get("recruiter_title") or "",
                     "url": item.get("recruiter_url") or ""}
    row = {
        "id": pid,
        "source": "linkedin",
        "title": (item.get("title") or "").strip(),
        "company": (item.get("organization") or "").strip(),
        "company_slug": item.get("org_linkedin_slug") or "",
        "company_url": item.get("organization_url") or "",
        "company_size": item.get("org_linkedin_size") or "",
        "industry": item.get("org_linkedin_industry") or "",
        "agency": bool(item.get("org_linkedin_recruitment_agency_derived")),
        "location": _join(item.get("locations_derived")),
        "work_arrangement": item.get("ai_work_arrangement") or "",
        "employment_type": _join(item.get("ai_employment_type") or item.get("employment_type")),
        "seniority": item.get("seniority") or "",
        "experience_level": item.get("ai_experience_level") or "",
        "salary": salary_text,
        "salary_max_usd": salary_max,
        "visa_sponsorship": item.get("ai_visa_sponsorship"),
        "key_skills": list(item.get("ai_key_skills") or [])[:20],
        "taxonomies": list(item.get("ai_taxonomies_a") or []),
        "summary": util.clip(item.get("ai_core_responsibilities") or "", 600),
        "posted_at": posted.astimezone(timezone.utc).isoformat(timespec="seconds") if posted else "",
        "valid_through": item.get("date_valid_through") or "",
        "url": "https://www.linkedin.com/jobs/view/%s/" % pid,
        "apply_url": item.get("external_apply_url") or item.get("url") or "",
        "direct_apply": bool(item.get("direct_apply")),
        "recruiter": recruiter,
        "groups": [group],
        "hint_tracks": [track] if track else [],
        "jd_sha": util.sha256_text(jd),
        "jd_chars": len(jd),
    }
    return row, jd


def jd_path(ws, pid):
    return ws.jds / ("%s.txt" % util.slug(str(pid), 80))


def read_jd(ws, pid):
    try:
        return jd_path(ws, pid).read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""


def ingest(ws, items, group, track, stamp=None):
    """Add actor items to the pool. Returns {'received', 'new', 'updated', 'invalid'}."""
    db = ws.db
    stamp = stamp or util.stamp(ws.now())
    stats = {"received": len(items), "new": 0, "updated": 0, "invalid": 0}
    rows = []
    for item in items:
        try:
            row, jd = normalize(item, group, track)
        except ValueError:
            stats["invalid"] += 1
            continue
        old = db.get(row["id"])
        if old:
            old["last_seen"] = stamp
            old["groups"] = sorted(set(old.get("groups") or []) | {group})
            old["hint_tracks"] = sorted(set(old.get("hint_tracks") or []) | set(row["hint_tracks"]))
            if jd and row["jd_sha"] != old.get("jd_sha"):
                old["jd_changed_at"] = stamp
            rows.append(old)
            stats["updated"] += 1
            continue
        if jd:
            util.atomic_write_text(jd_path(ws, row["id"]), jd + "\n")
        row.update(first_seen=stamp, last_seen=stamp, stage="new", status="pending")
        rows.append(row)
        stats["new"] += 1
    db.put_many(rows)
    return stats


def add_manual(ws, *, title, company, jd, url="", location="", track=""):
    """A posting found outside the search (pasted or from a link): it joins the pool and the review like any other."""
    pid = "m" + util.sha256_text("%s|%s|%s" % (util.company_key(company), util.norm_title(title), url or jd[:2000]))[:11]
    stamp = util.stamp(ws.now())
    row = ws.db.get(pid) or {
        "id": pid, "source": "manual", "title": title.strip(), "company": company.strip(), "location": location,
        "url": url, "apply_url": url, "groups": ["manual"], "hint_tracks": [track] if track else [],
        "first_seen": stamp, "stage": "new", "status": "pending", "posted_at": "",
    }
    row.update(last_seen=stamp, jd_sha=util.sha256_text(jd.strip()), jd_chars=len(jd.strip()))
    util.atomic_write_text(jd_path(ws, pid), jd.strip() + "\n")
    ws.db.put(row)
    return row


# ---------------------------------------------------------------- plan and window

def plan(cfg):
    """[{name, track, budget, filters}] in run order."""
    out = []
    for name, g in config.groups(cfg):
        if g.get("enabled", True) is False:
            continue
        out.append({"name": name, "track": g.get("track"), "budget": float(g.get("budget_usd") or 0),
                    "filters": config.group_filters(cfg, g)})
    return out


def manifests(ws):
    out = []
    for p in sorted(ws.runs.glob("*/manifest.json")):
        m = util.read_json(p)
        if m:
            out.append((p, m))
    return out


def last_complete_end(ws):
    ends = [util.parse_time(m.get("window_end")) for _, m in manifests(ws) if m.get("complete")]
    ends = [e for e in ends if e]
    return max(ends) if ends else None


def window(ws, end):
    s = ws.cfg["search"]
    utc_end = end.astimezone(timezone.utc)
    last = last_complete_end(ws)
    if last is None:
        start = utc_end - timedelta(hours=float(s.get("first_lookback_hours") or 72))
    else:
        start = min(utc_end - timedelta(hours=float(s.get("lookback_hours") or 36)), last - timedelta(hours=1))
    floor = utc_end - timedelta(days=float(s.get("max_lookback_days") or 7)) + timedelta(hours=1)
    return max(start, floor), utc_end


def actor_input(cfg, group, start):
    s = cfg["search"]
    inp = dict(group["filters"])
    inp.setdefault("locationSearch", list(cfg["candidate"].get("locations") or []) or None)
    if not inp.get("locationSearch"):
        inp.pop("locationSearch", None)
    if cfg["candidate"].get("work_arrangements") and "aiWorkArrangementFilter" not in inp:
        inp["aiWorkArrangementFilter"] = list(cfg["candidate"]["work_arrangements"])
    inp.update(timeRange=s.get("time_range") or "7d", limit=int(s.get("limit") or 1000), descriptionType="text",
               datePostedAfter=start.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"))
    return inp


def schema_problems(client, actor, inputs):
    """{group: [input keys the actor's schema does not know]}; a fetch failure raises (nothing is paid yet)."""
    props = client.input_schema(actor)
    if not props:
        raise ApifyError("could not read the input schema of %s" % actor)
    bad = {}
    for name, inp in inputs.items():
        unknown = sorted(k for k in inp if k not in props)
        if unknown:
            bad[name] = unknown
    return bad


# ---------------------------------------------------------------- collection

def _manifest_path(ws, mid):
    return ws.runs / mid / "manifest.json"


def _save(ws, m):
    util.atomic_write_json(_manifest_path(ws, m["id"]), m)


def open_manifest(ws, again=False):
    """Today's manifest (created with a fresh window when missing; `again` starts an extra one)."""
    today = ws.today()
    mid = today
    if again:
        n = 2
        while _manifest_path(ws, mid).exists():
            mid = "%s-%d" % (today, n)
            n += 1
    m = util.read_json(_manifest_path(ws, mid))
    if m:
        return m
    start, end = window(ws, ws.now())
    m = {"id": mid, "day": today, "created_at": util.stamp(ws.now()), "window_start": start.isoformat(),
         "window_end": end.isoformat(), "actor": ws.cfg["search"]["actor"], "groups": {}, "complete": False}
    _save(ws, m)
    return m


def paused(ws):
    p = ws.data / "PAUSE"
    return ("paused by `jobpilot pause` (%s)" % p.read_text().strip()[:120]) if p.exists() else None


def client_for(ws):
    return Apify(load_token(ws), guard=lambda: paused(ws))


def reconcile(ws, m, client):
    """Adopt LAUNCHING groups (a POST whose answer never arrived) by start time, or mark them not started."""
    pending = {n: g for n, g in m["groups"].items() if g.get("status") == "LAUNCHING"}
    if not pending:
        return 0
    claimed = {g.get("run_id") for _, mm in manifests(ws) for g in mm.get("groups", {}).values() if g.get("run_id")}
    runs = client.recent_runs(m["actor"], limit=50)
    changed = 0
    for name, g in pending.items():
        t0 = util.parse_time(g.get("started_at"))
        near = [r for r in runs if r.get("id") not in claimed and t0 and util.parse_time(r.get("startedAt"))
                and abs((util.parse_time(r["startedAt"]) - t0).total_seconds()) <= RECONCILE_MINUTES * 60]
        if len(near) == 1:
            g.update(status=near[0].get("status") or "RUNNING", run_id=near[0]["id"],
                     dataset_id=near[0].get("defaultDatasetId"), note="adopted after an uncertain launch")
            claimed.add(near[0]["id"])
            changed += 1
        elif not near and t0 and (ws.now() - t0).total_seconds() > ABANDON_MINUTES * 60:
            g.update(status="NOT_STARTED", note="no run appeared within %d minutes of the launch" % ABANDON_MINUTES)
            budget.release(ws, g["slot"])
            changed += 1
    if changed:
        _save(ws, m)
    return changed


def collect(ws, client=None, *, dry_run=False, only=None, again=False, max_seconds=1800, poll_seconds=10,
            log=print):
    """Run today's collection (or continue it). Returns a summary dict."""
    cfg = ws.cfg
    errors = [msg for level, msg in config.check(cfg) if level == "error"]
    if errors:
        raise util.JobPilotError("fix jobpilot.toml first:\n  - " + "\n  - ".join(errors))
    groups = [g for g in plan(cfg) if not only or g["name"] in only]
    if not groups:
        raise util.JobPilotError("no search groups to run")
    if dry_run:
        start, end = window(ws, ws.now())
        inputs = {g["name"]: actor_input(cfg, g, start) for g in groups}
        out = {"dry_run": True, "window_start": start.isoformat(), "window_end": end.isoformat(),
               "available_usd": budget.available(ws), "groups": [
                   {"name": g["name"], "track": g["track"], "cap": g["budget"], "input": inputs[g["name"]]}
                   for g in groups]}
        if client is not None:
            out["schema_problems"] = schema_problems(client, cfg["search"]["actor"], inputs)
        return out
    with ws.lock("collect", wait=False):
        reason = paused(ws)
        if reason:
            raise util.JobPilotError(reason)
        client = client or client_for(ws)
        m = open_manifest(ws, again=again)
        start = util.parse_time(m["window_start"])
        inputs = {g["name"]: actor_input(cfg, g, start) for g in groups}
        bad = schema_problems(client, m["actor"], inputs)
        if bad:
            raise util.JobPilotError("the actor's input schema does not know these keys (nothing was paid): " +
                                     "; ".join("%s: %s" % (k, ", ".join(v)) for k, v in bad.items()))
        reconcile(ws, m, client)
        deadline = time.monotonic() + max_seconds
        limit = max(1, int(cfg["search"].get("max_concurrent_runs") or 3))
        blocked = False
        open_runs = lambda: [n for n, g in m["groups"].items() if g.get("run_id") and not g.get("collected")]  # noqa: E731
        while True:
            if any(g.get("status") == "LAUNCHING" for g in m["groups"].values()):
                blocked = True  # an uncertain launch must be reconciled before anything else is paid for
            running = open_runs()
            todo = [g for g in groups if g["name"] not in m["groups"]]
            while todo and not blocked and len(running) < limit and time.monotonic() < deadline:
                g = todo.pop(0)
                state = _launch(ws, m, client, g, inputs[g["name"]], log)
                if state == "uncertain":
                    blocked = True
                elif state == "running":
                    running.append(g["name"])
            for name in running:
                _poll(ws, m, client, name, log)
            running = open_runs()
            todo = [g for g in groups if g["name"] not in m["groups"]]
            if not running and (not todo or blocked):
                if blocked and todo:
                    log("an earlier launch is still uncertain; run `jobpilot collect` again in a few minutes")
                break
            if time.monotonic() >= deadline:
                log("time limit reached; `jobpilot collect` again later finishes the open runs")
                break
            time.sleep(poll_seconds)
        planned = {g["name"] for g in plan(cfg)}
        # A complete window moves the next window's start forward; a skipped or failed group keeps it back, so the
        # next run looks back far enough to catch up (up to max_lookback_days).
        m["complete"] = planned.issubset(m["groups"]) and all(
            m["groups"][n].get("status") == "SUCCEEDED" and m["groups"][n].get("collected")
            and not m["groups"][n].get("truncated") for n in planned)
        m["finished_at"] = util.stamp(ws.now())
        _save(ws, m)
    return summarize(ws, m)


def _launch(ws, m, client, g, inp, log):
    name = g["name"]
    cap = math.floor(min(g["budget"], budget.available(ws)) * 100 + 1e-9) / 100  # never round above what is left
    entry = {"track": g["track"], "cap": cap, "input": inp}
    if cap < 0.02:
        entry.update(status="SKIPPED", note="budget: nothing left today (cap $%.2f/day)" % budget.today_cap(ws))
        m["groups"][name] = entry
        _save(ws, m)
        log("%-24s skipped (budget)" % name)
        return "skipped"
    slot = "search:%s:%s" % (m["id"], name)
    budget.reserve(ws, slot, cap, kind="search", note=name)
    entry.update(status="LAUNCHING", slot=slot, started_at=util.stamp(ws.now()))
    m["groups"][name] = entry
    _save(ws, m)
    s = ws.cfg["search"]
    try:
        run = client.start(m["actor"], inp, max_charge_usd=cap, timeout_s=int(s.get("run_timeout_seconds") or 900),
                           memory_mb=int(s.get("memory_mb") or 1024), label=name)
    except ApifyError as exc:
        if exc.definitive:
            budget.release(ws, slot)
            entry.update(status="NOT_STARTED", note=str(exc)[:300])
            _save(ws, m)
            log("%-24s not started: %s" % (name, exc))
            return "refused"
        entry["note"] = "launch outcome unknown: %s" % str(exc)[:200]
        _save(ws, m)
        log("%-24s launch outcome unknown; it will be reconciled on the next run" % name)
        return "uncertain"
    entry.update(status=run.get("status") or "READY", run_id=run["id"], dataset_id=run.get("defaultDatasetId"))
    _save(ws, m)
    log("%-24s started (cap $%.2f)" % (name, cap))
    return "running"


def _poll(ws, m, client, name, log):
    entry = m["groups"][name]
    run = client.run(entry["run_id"])
    entry["status"] = run.get("status") or entry["status"]
    entry["dataset_id"] = run.get("defaultDatasetId") or entry.get("dataset_id")
    if entry["status"] not in TERMINAL:
        _save(ws, m)
        return
    items = client.dataset(entry["dataset_id"]) if entry.get("dataset_id") else []
    cost = run_cost(run)
    price = item_price(run)
    if price and items:
        cost = max(cost, round(len(items) * price, 4))
    cost = min(cost, entry["cap"]) if entry.get("cap") else cost
    raw = ws.runs / m["id"] / ("%s.json.gz" % util.slug(name))
    with gzip.open(raw, "wt", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False)
    stats = ingest(ws, items, name, entry.get("track"))
    if len(items) >= int(entry["input"].get("limit") or 1000):
        entry["truncated"] = "returned rows reached the limit; narrow the group or raise search.limit"
    elif price and entry.get("cap") and len(items) * price >= entry["cap"] - 0.01:
        entry["truncated"] = "the run's charge reached its cap; raise budget_usd for this group if rows are missing"
    entry.update(cost=cost, rows=len(items), new=stats["new"], finished_at=util.stamp(ws.now()), collected=True)
    budget.settle(ws, entry["slot"], cost, run_id=entry["run_id"])
    _save(ws, m)
    log("%-24s %s: %d rows, %d new, $%.3f%s" % (name, entry["status"], len(items), stats["new"], cost,
                                              " (%s)" % entry["truncated"] if entry.get("truncated") else ""))


def summarize(ws, m):
    groups = m.get("groups", {})
    by_track = {}
    for g in groups.values():
        t = by_track.setdefault(g.get("track") or "?", {"rows": 0, "new": 0, "cost": 0.0})
        t["rows"] += g.get("rows") or 0
        t["new"] += g.get("new") or 0
        t["cost"] = round(t["cost"] + (g.get("cost") or 0), 4)
    out = {"id": m["id"], "window_start": m["window_start"], "window_end": m["window_end"],
           "complete": m.get("complete"), "rows": sum(g.get("rows") or 0 for g in groups.values()),
           "new": sum(g.get("new") or 0 for g in groups.values()),
           "cost": round(sum(g.get("cost") or 0 for g in groups.values()), 4), "by_track": by_track,
           "skipped": [n for n, g in groups.items() if g.get("status") in ("SKIPPED", "NOT_STARTED")],
           "failed": [n for n, g in groups.items() if g.get("status") in ("FAILED", "ABORTED", "TIMED-OUT")],
           "truncated": [n for n, g in groups.items() if g.get("truncated")]}
    ws.event("collect", "collected %d postings (%d new) for $%.2f" % (out["rows"], out["new"], out["cost"]),
             manifest=m["id"], new=out["new"], cost=out["cost"])
    return out
