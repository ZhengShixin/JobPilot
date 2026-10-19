"""The job pool: reviewed postings that cleared the bar, their statuses, and the queue you apply from.

Queue order: track preference first (priority 1 before 2), then regular roles before low-pay practice roles, then each
company's best posting before its second best (so one firm never fills the top of the list), then score, then the
newest posting. Status changes are also written to data/pool-events.jsonl, so nothing a user decided is ever lost.

Practice roles: a posting whose advertised annual maximum lies between candidate.min_salary_usd (the floor; below it
the coarse filter drops the posting) and candidate.target_salary_usd is admitted like any other, labeled "practice"
(worth applying to for interview practice) and queued after the regular roles of its track. A posting without an
advertised salary is a regular role.
"""
from . import config, util

MARKS = ("ready", "skipped", "hold", "applied")


def practice_note(row, cfg):
    """'' for a regular role, or why it is a low-pay practice role (its advertised ceiling under the target)."""
    cand = (cfg or {}).get("candidate") or {}
    target = float(cand.get("target_salary_usd") or 0)
    top = row.get("salary_max_usd")
    if target and isinstance(top, (int, float)) and 0 < top < target:
        return "advertised maximum $%s, under your $%s target: a practice role" % ("{:,.0f}".format(top),
                                                                                   "{:,.0f}".format(target))
    return ""


def is_practice(row, cfg):
    return bool(practice_note(row, cfg))


def queue(ws, limit=None, track=None):
    cfg = ws.cfg
    rows = ws.db.find("status='ready'")
    if track:
        rows = [r for r in rows if r.get("track") == track]
    stale_days = float(cfg["pool"].get("stale_after_days") or 45)
    now = ws.now()
    fresh = []
    for r in rows:
        age = util.days_between(r.get("posted_at") or r.get("first_seen"), now)
        if age is not None and age > stale_days:
            continue
        fresh.append(r)
    fresh.sort(key=lambda r: (-(r.get("score") or 0), -(util.parse_time(r.get("posted_at")) or now).timestamp()))
    seen, first, rest = set(), [], []
    for r in fresh:
        key = (r.get("track"), r.get("company_key") or util.company_key(r.get("company")))
        (rest if key in seen else first).append(r)
        seen.add(key)
    tiers = {t: config.tier(cfg, t) for t in config.track_ids(cfg)}
    ordered = first + rest
    # stable: keeps "best per company first" inside a tier; practice roles after the regular ones of their track
    ordered.sort(key=lambda r: (tiers.get(r.get("track"), 99), is_practice(r, cfg)))
    return ordered[:limit] if limit else ordered


def mark(ws, pid, status, note="", app_id=None, by="user"):
    if status not in MARKS:
        raise util.JobPilotError("status must be one of %s" % ", ".join(MARKS))
    row = ws.db.get(pid)
    if not row:
        raise util.JobPilotError("no posting %s" % pid)
    old = row.get("status")
    row["status"] = status
    row["status_note"] = note or ""
    if app_id:
        row["app_id"] = app_id
    ws.db.put(row)
    util.jsonl_append(ws.pool_events_path, {"id": pid, "from": old, "to": status, "note": note, "app_id": app_id,
                                            "by": by})
    return row


def stats(ws):
    db = ws.db
    out = {"by_status": db.counts("status"), "by_stage": db.counts("stage"),
           "ready_by_track": db.counts("track", "status='ready'"), "total": db.count()}
    today = ws.today()
    new_today = db.find("first_seen >= ?", (today,))
    out["new_today"] = len(new_today)
    out["admitted_today"] = sum(1 for r in new_today if r.get("status") in ("ready", "applied", "skipped", "hold"))
    return out


def top(ws, track, since=None, n=3):
    rows = [r for r in ws.db.find("status='ready' AND track=?", (track,))
            if not since or (r.get("first_seen") or "") >= since]
    rows.sort(key=lambda r: -(r.get("score") or 0))
    return rows[:n]


def public(row, full=False, cfg=None):
    """A posting as the dashboard and `--json` output show it (no raw review text); with the config, also whether it is
    a low-pay practice role."""
    keys = ("id", "title", "company", "location", "work_arrangement", "employment_type", "seniority", "salary",
            "posted_at", "first_seen", "url", "apply_url", "status", "status_note", "stage", "track", "score",
            "app_id", "company_slug", "industry", "agency", "experience_level", "source")
    out = {k: row.get(k) for k in keys if row.get(k) not in (None, "")}
    rv = row.get("review") or {}
    if rv:
        out["category"] = rv.get("category")
        out["reason"] = rv.get("reason")
        out["gaps"] = rv.get("gaps") or []
        out["components"] = rv.get("components")
        out["cap"] = rv.get("cap")
        if full:
            for k in ("reasons", "jd_quotes", "profile_quotes", "evidence", "flags", "cap_reason", "end_employer",
                      "careers_url", "intermediary", "model", "reviewed_at", "reused_from"):
                if rv.get(k) not in (None, "", [], {}):
                    out[k] = rv[k]
    note = practice_note(row, cfg) if cfg else ""
    if note:
        out["salary_band"], out["practice_note"] = "practice", note
    if row.get("triage"):
        out["triage"] = row["triage"]
    if full and row.get("recruiter"):
        out["recruiter"] = row["recruiter"]
    if full and row.get("summary"):
        out["summary"] = row["summary"]
    if full and row.get("key_skills"):
        out["key_skills"] = row["key_skills"]
    return out
