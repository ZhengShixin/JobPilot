"""JobPilot dashboard: one local web page over the workspace (standard library http.server, no dependencies).

Security model:
  - binds 127.0.0.1 only, and answers only requests whose Host is 127.0.0.1 or localhost (a DNS-rebinding guard);
  - a random key in data/dashboard.token (mode 600). `jobpilot dashboard` opens http://127.0.0.1:<port>/?key=<key>,
    which sets an HttpOnly, SameSite=Strict session cookie (an HMAC of the key, so a login survives restarts) and
    redirects to a clean URL. Every /api request needs that cookie; deleting the token file logs every browser out;
  - every POST needs a JSON body (at most 1 MB), a same-origin Origin header and the X-JobPilot-CSRF header that
    /api/session hands to the page;
  - a Content-Security-Policy with no external sources at all: inline script and style run only with the nonce
    of that response;
  - the writes are posting marks, application events, recruiter mail marked answered or recorded as an interview
    invitation, and background tasks (a daily run, a tailoring run, a referral search); each is audited in
    data/dashboard-actions.jsonl and shows up in the activity feed.

    serve(ws, port=None, open_browser=True)    run until Ctrl-C
    export_static(ws, out_path)                one self-contained HTML file with every read view embedded (demos)
    /api/flow.svg?theme=light|dark&width=N&lang=en|zh    the application flow (flow.py) as an SVG
    python3 -m jobpilot.dashboard.server --export FILE --workspace DIR
"""
import hashlib
import hmac
import json
import os
import re
import secrets
import sys
import threading
import time
import webbrowser
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .. import __version__, brief, budget, config, ics, ledger, pool, search, util
from . import flow, tasks

HERE = Path(__file__).resolve().parent
MAX_BODY = 1 << 20
FLOW_WIDTHS = (600, 1160)       # the static export's pre-rendered flow widths (narrow, wide)
ADMITTED = ("ready", "hold", "applied", "skipped")
STATUS_GROUPS = {
    "ready": ("ready",), "saved": ("hold",), "applied": ("applied",), "skipped": ("skipped",),
    "below": ("below_threshold",), "excluded": ("excluded",), "filtered": ("dropped",), "pending": ("pending",),
    "stale": ("stale",), "admitted": ADMITTED, "all": None,
}
PRACTICE_WORDS = "low pay practice 低薪 练手"     # the pool search finds practice roles by these
COLUMNS = {"planned": "saved", "applied": "applied", "oa": "assessment", "interview": "interview", "offer": "offer",
           "rejected": "closed", "withdrawn": "closed", "closed": "closed"}
NET_EVENTS = ("connect_sent", "email_sent", "accepted", "message_sent", "replied", "chat_scheduled", "chat_done",
              "referred", "declined", "no_response", "dropped", "undo")
FAVICON = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><rect width="32" height="32" rx="8" '
           'fill="#256abf"/><path d="M7 17.5 25 8l-5.2 17-4.6-6.1L7 17.5Z" fill="#fff"/><path d="m15.2 18.9 4.6 6.1 '
           '1.1-9.4-5.7 3.3Z" fill="#cde2fb"/></svg>')


class ApiError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


# ---------------------------------------------------------------- optional modules

def _mod(name):
    try:
        return __import__("jobpilot." + name, fromlist=[name])
    except ImportError:
        return None


def _call(name, func, *args, default=None):
    mod = _mod(name)
    fn = getattr(mod, func, None) if mod else None
    if fn is None:
        return default
    try:
        return fn(*args)
    except util.JobPilotError:
        return default
    except Exception:  # noqa: BLE001 - an optional module must never take the dashboard down
        return default


# ---------------------------------------------------------------- helpers

def _key_path(ws):
    return ws.data / "dashboard.token"


def load_key(ws):
    path = _key_path(ws)
    try:
        key = path.read_text().strip()
        if len(key) >= 24:
            return key
    except FileNotFoundError:
        pass
    ws.data.mkdir(parents=True, exist_ok=True)
    key = secrets.token_urlsafe(24)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(key + "\n")
    os.chmod(path, 0o600)
    return key


def _mac(key, label):
    return hmac.new(key.encode(), label.encode(), hashlib.sha256).hexdigest()


def is_demo(ws):
    return (ws.data / "DEMO").exists()


def _int(value, default, lo=None, hi=None):
    try:
        v = int(value)
    except (TypeError, ValueError):
        return default
    if lo is not None:
        v = max(lo, v)
    if hi is not None:
        v = min(hi, v)
    return v


def _float(value, default=None):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _text(body, key, n=300, required=False):
    value = body.get(key)
    if value is None or value == "":
        if required:
            raise ApiError(400, "missing %s" % key)
        return ""
    if not isinstance(value, (str, int, float)):
        raise ApiError(400, "%s must be text" % key)
    return str(value).strip()[:n]


def _lang(q, cfg):
    return q.get("lang") if q.get("lang") in flow.LABELS else config.language(cfg)


def _url(value):
    value = (value or "").strip()
    return value if re.match(r"^https?://", value, re.I) else ""


def _age_days(ws, value):
    d = util.days_between(value, ws.now())
    return None if d is None else round(d, 2)


def _row(ws, r, full=False):
    out = pool.public(r, full=full, cfg=ws.cfg)
    out["age_days"] = _age_days(ws, r.get("posted_at") or r.get("first_seen"))
    return out


def _tracks(cfg):
    return [{"id": t["id"], "name": t.get("name") or t["id"], "priority": t.get("priority", 1), "slot": i + 1,
             "min_score": config.min_score(cfg, t["id"])} for i, t in enumerate(config.tracks(cfg))]


def _tailored(ws):
    folders = _call("tailor", "folders", ws)
    if folders is not None:
        return folders
    out = []
    if not ws.tailored.exists():
        return out
    for rep in sorted(ws.tailored.glob("*/report.json"), reverse=True):
        data = util.read_json(rep) or {}
        data.setdefault("folder", ws.rel(rep.parent))
        out.append(data)
    return out


def _network_files(ws):
    people = util.read_json(ws.network / "people.json", {}) or {}
    companies = util.read_json(ws.network / "companies.json", {}) or {}
    return people, companies


def _people(ws):
    rows = _call("network", "people", ws)
    if rows is None:
        people, _ = _network_files(ws)
        rows = list(people.values())
    return rows or []


def _companies(ws):
    rows = _call("network", "companies", ws)
    if rows is None:
        _, companies = _network_files(ws)
        rows = [dict(v, key=v.get("key") or k) for k, v in companies.items()]
    return rows or []


def _person(ws, pid):
    p = _call("network", "person", ws, pid)
    if p is None:
        people, _ = _network_files(ws)
        p = people.get(pid)
        if p is not None:
            p = dict(p)
            p["card"] = util.read_json(ws.network / "cards" / ("%s.json" % util.slug(pid, 40)))
    return p


def _app_lite(ws, st, after_days):
    nxt = None
    for d in st.get("deadlines") or []:
        if not d.get("done"):
            nxt = {"kind": "oa", "at": d.get("due")}
            break
    for iv in st.get("interviews") or []:
        if not iv.get("done"):
            t = util.parse_time(iv.get("time"))
            if t and t > ws.now() - timedelta(hours=2):
                if nxt is None or (util.parse_time(nxt["at"]) or t) > t:
                    nxt = {"kind": "interview", "at": iv.get("time")}
                break
    if nxt is None and st.get("stage") == "applied" and not st.get("closed"):
        age = util.days_between(st.get("last_date") or st.get("applied_date"), ws.now())
        if age is not None and age >= after_days:
            nxt = {"kind": "followup", "days": int(age)}
    keep = ("id", "company", "role", "track", "stage", "closed", "applied", "responded", "applied_date", "last_date",
            "created", "url", "posting_id", "referrer", "location", "oa_rounds", "interview_rounds")
    out = {k: st.get(k) for k in keep}
    out["column"] = COLUMNS.get(st.get("stage"), "saved")
    out["next"] = nxt
    out["timeline"] = st.get("timeline") or []
    out["notes"] = st.get("notes") or []
    out["deadlines"] = st.get("deadlines") or []
    out["interviews"] = st.get("interviews") or []
    return out


def _manifest_summary(m):
    groups = m.get("groups") or {}
    return {"id": m.get("id"), "window_start": m.get("window_start"), "window_end": m.get("window_end"),
            "created_at": m.get("created_at"), "finished_at": m.get("finished_at"), "complete": bool(m.get("complete")),
            "rows": sum(g.get("rows") or 0 for g in groups.values()),
            "new": sum(g.get("new") or 0 for g in groups.values()),
            "cost": round(sum(g.get("cost") or 0 for g in groups.values()), 4),
            "skipped": [n for n, g in groups.items() if g.get("status") in ("SKIPPED", "NOT_STARTED")],
            "failed": [n for n, g in groups.items() if g.get("status") in ("FAILED", "ABORTED", "TIMED-OUT")],
            "truncated": [n for n, g in groups.items() if g.get("truncated")]}


def _reason(r):
    if r.get("triage"):
        return "filter", "%s: %s" % (r["triage"].get("rule"), r["triage"].get("detail"))
    rv = r.get("review") or {}
    if r.get("status") == "below_threshold":
        return "below", r.get("status_note") or "score below the track's minimum"
    return "excluded", r.get("status_note") or rv.get("reason") or ""


def _mail_link(ws, message_id):
    """{"mail": Gmail link} for a message id, or {"mail_hidden": True} in the demo (its mail is fictional)."""
    if is_demo(ws):
        return {"mail": None, "mail_hidden": bool(message_id)}
    mod = _mod("inbox")
    return {"mail": mod.gmail_link(message_id) if mod and hasattr(mod, "gmail_link") else None}


def _waiting(ws):
    rows = _call("inbox", "waiting", ws, default=[]) or []
    for r in rows:
        r.update(_mail_link(ws, r.get("message_id")))
    return rows


def _due(ws):
    """ledger.due plus, for an interview still waiting for a time, the newest email summary and when you replied."""
    due = ledger.due(ws)
    for u in due.get("unscheduled") or []:
        u.update(_call("inbox", "mail_state", ws, u["app_id"], default={}) or {})
    return due


# ---------------------------------------------------------------- read views

class Views:
    def __init__(self, ws, static=False):
        self.ws = ws
        self.static = static
        self._cache = {}

    def cached(self, key, seconds, fn):
        hit = self._cache.get(key)
        if hit and time.monotonic() - hit[0] < seconds:
            return hit[1]
        value = fn()
        self._cache[key] = (time.monotonic(), value)
        return value

    # -- session and summary
    def session(self, q, csrf=""):
        cfg = self.ws.cfg
        ref = cfg.get("referrals") or {}
        plan_name = ref.get("linkedin_plan") or "free"
        name = (cfg.get("candidate") or {}).get("name") or ""
        return {"csrf": csrf, "language": config.language(cfg), "workspace": self.ws.root.name, "version": __version__,
                "candidate": name, "first_name": name.split()[0] if name.strip() else "",
                "tracks": _tracks(cfg), "timezone": str(self.ws.tz), "today": self.ws.today(),
                "now": util.stamp(self.ws.now()), "demo": is_demo(self.ws), "static": self.static,
                "referrals": {"plan": plan_name,
                              "note_limit": int(ref.get("note_limit_premium" if plan_name == "premium"
                                                        else "note_limit_free") or 200),
                              "message_limit": int(ref.get("message_limit") or 700),
                              "email_limits": {"email_subject": int(ref.get("email_subject_limit") or 80),
                                               "email_body": int(ref.get("email_body_limit") or 1500),
                                               "email_followup": int(ref.get("email_followup_limit") or 600)},
                              "profiles_per_company": int(ref.get("profiles_per_company") or 30)},
                "features": {"network": _mod("network") is not None or (self.ws.network / "people.json").exists(),
                             "tailor": _mod("tailor") is not None, "inbox": _mod("inbox") is not None,
                             "schedule": _mod("schedule") is not None},
                "followup_days": int((cfg.get("followup") or {}).get("after_days") or 10)}

    def summary(self, q):
        ws, cfg = self.ws, self.ws.cfg
        db = ws.db
        today = ws.today()
        tracks = _tracks(cfg)
        queue_rows = pool.queue(ws)
        since7 = (ws.now() - timedelta(days=6)).date().isoformat()
        recent = db.find("first_seen >= ?", (since7,))
        days = [(ws.now() - timedelta(days=i)).date().isoformat() for i in range(6, -1, -1)]
        series = {t["id"]: [0] * 7 for t in tracks}
        new_today = {t["id"]: 0 for t in tracks}
        seen_today = 0
        for r in recent:
            day = (r.get("first_seen") or "")[:10]
            if day == today:
                seen_today += 1
            if r.get("status") in ADMITTED and r.get("track") in series and day in days:
                series[r["track"]][days.index(day)] += 1
                if day == today:
                    new_today[r["track"]] += 1
        apps = [s for s in ledger.states(ws).values() if not s["voided"]]
        week_ago = (ws.now() - timedelta(days=7)).date().isoformat()
        fun = ledger.funnel(ws)
        responded = sum(1 for s in apps if s["applied"] and s["responded"])
        due = _due(ws)
        by_track = []
        for t in tracks:
            by_track.append({**t, "new_today": new_today[t["id"]],
                             "ready": sum(1 for r in queue_rows if r.get("track") == t["id"]),
                             "top": [_row(ws, r) for r in queue_rows if r.get("track") == t["id"]][:5]})
        mans = search.manifests(ws)
        last = _manifest_summary(mans[-1][1]) if mans else None
        rep = brief.latest(ws)
        if rep:
            rep = {k: rep.get(k) for k in ("date", "tracks", "problems", "queue", "funnel", "budget")}
        sched = self.cached("schedule", 60, lambda: _call("schedule", "status", ws))
        money = budget.summary(ws)
        events = sorted(util.jsonl_read(ws.events_path), key=lambda e: e.get("ts") or "")
        last_daily = next((e for e in reversed(events) if e.get("kind") == "daily"), None)
        return {
            "today": today, "days": days, "series": series,
            "kpis": {"new_today": sum(new_today.values()), "seen_today": seen_today, "ready": len(queue_rows),
                     "applied_week": sum(1 for s in apps if (s.get("applied_date") or "") >= week_ago and s["applied"]),
                     "applied_total": fun["applied"], "responded": responded,
                     "response_rate": round(responded / fun["applied"], 3) if fun["applied"] else None,
                     "interviews": fun["interview"], "offers": fun["offer"],
                     "upcoming_interviews": len([i for i in due["interviews"] if not i.get("past")]),
                     "deadlines": len(due["deadlines"]), "followups": len(due["followups"])},
            "funnel": fun, "by_track": by_track, "due": due, "mail": _waiting(ws), "budget": money,
            "last_collection": last,
            "report": rep, "schedule": sched, "paused": (ws.data / "PAUSE").exists(),
            "tasks": [] if self.static else tasks.running(ws),
            "last_daily": {"at": last_daily.get("ts"), "text": last_daily.get("text")} if last_daily else None,
            "pool": {"total": db.count(), "by_status": db.counts("status")},
        }

    # -- pool
    def queue(self, q):
        rows = pool.queue(self.ws, limit=_int(q.get("n"), 50, 1, 500), track=q.get("track") or None)
        return [_row(self.ws, r) for r in rows]

    def pool(self, q):
        ws = self.ws
        group = q.get("status") or "ready"
        statuses = STATUS_GROUPS.get(group, ("ready",))
        where, params = "1=1", []
        if statuses:
            where = "status IN (%s)" % ",".join("?" * len(statuses))
            params = list(statuses)
        rows = ws.db.find(where, params)
        counts = ws.db.counts("status")
        track = q.get("track") or ""
        text = (q.get("q") or "").strip().casefold()
        min_score = _float(q.get("min"))
        track_counts = {}
        out = []
        for r in rows:
            words = "%s %s %s" % (r.get("title"), r.get("company"), r.get("location"))
            if pool.is_practice(r, ws.cfg):
                words += " " + PRACTICE_WORDS
            if text and text not in words.casefold():
                continue
            if min_score is not None and (r.get("score") or 0) < min_score:
                continue
            track_counts[r.get("track") or ""] = track_counts.get(r.get("track") or "", 0) + 1
            if track and r.get("track") != track:
                continue
            out.append(r)
        sort = q.get("sort") or "score"
        desc = (q.get("order") or "desc") != "asc"
        keyf = {"score": lambda r: (r.get("score") or 0, r.get("posted_at") or r.get("first_seen") or ""),
                "posted": lambda r: r.get("posted_at") or r.get("first_seen") or "",
                "company": lambda r: (r.get("company") or "").casefold(),
                "title": lambda r: (r.get("title") or "").casefold()}.get(sort)
        if keyf:
            out.sort(key=keyf, reverse=desc)
        offset = _int(q.get("offset"), 0, 0)
        limit = _int(q.get("limit"), 50, 1, 500)
        return {"total": len(out), "offset": offset, "rows": [_row(ws, r) for r in out[offset:offset + limit]],
                "counts": counts, "track_counts": track_counts, "group": group}

    def pool_all(self):
        """Every posting (static export: the page filters them itself)."""
        return [_row(self.ws, r) for r in self.ws.db.find("1=1", order="first_seen DESC", limit=5000)]

    def posting(self, q):
        ws = self.ws
        pid = q.get("id") or ""
        row = ws.db.get(pid)
        if not row:
            raise ApiError(404, "no posting %s" % pid)
        data = _row(ws, row, full=True)
        data["jd"] = search.read_jd(ws, row["id"])
        apps = [s for s in ledger.find(ws, posting_id=row["id"]) if not s["voided"]]
        after = int((ws.cfg.get("followup") or {}).get("after_days") or 10)
        data["application"] = _app_lite(ws, apps[0], after) if apps else None
        data["tailored"] = [f for f in _tailored(ws) if str(f.get("posting_id") or "") == row["id"]]
        ck = row.get("company_key") or util.company_key(row.get("company"))
        data["referrals"] = sum(1 for p in _people(ws) if p.get("company_key") == ck and p.get("triage") != "skip")
        return data

    # -- applications
    def apps(self, q):
        ws = self.ws
        after = int((ws.cfg.get("followup") or {}).get("after_days") or 10)
        sts = [s for s in ledger.states(ws).values() if not s["voided"]]
        sts.sort(key=lambda s: (s.get("last_date") or s.get("created") or "", s["id"]), reverse=True)
        waiting = {r["app_id"] for r in _waiting(ws)}
        apps = [_app_lite(ws, s, after) for s in sts]
        for a in apps:
            a["mail_waiting"] = a["id"] in waiting
        return {"apps": apps, "funnel": ledger.funnel(ws), "inbox": self.inbox(q),
                "flow": flow.compute(ws, _lang(q, ws.cfg))}

    def flow_svg(self, q):
        theme = q.get("theme") if q.get("theme") in flow.THEMES else "light"
        lang = _lang(q, self.ws.cfg)
        return flow.render_svg(flow.compute(self.ws, lang), theme=theme, width=_int(q.get("width"), 960, 320, 2000),
                               lang=lang)

    def app(self, q):
        ws = self.ws
        st = ledger.state(ws, q.get("id") or "")
        if not st or st["voided"]:
            raise ApiError(404, "no application %s" % q.get("id"))
        after = int((ws.cfg.get("followup") or {}).get("after_days") or 10)
        data = _app_lite(ws, st, after)
        row = ws.db.get(st["posting_id"]) if st.get("posting_id") else None
        data["posting"] = _row(ws, row) if row else None
        data["tailored"] = [f for f in _tailored(ws) if st.get("posting_id")
                            and str(f.get("posting_id") or "") == str(st["posting_id"])]
        data["mail"] = [r for r in _waiting(ws) if r["app_id"] == st["id"]]
        gists = _call("inbox", "summaries", ws, default={}) or {}
        for t in data["timeline"]:
            if t.get("source") == "gmail" and gists.get(t.get("message_id")):
                t["gist"] = gists[t["message_id"]]
        if st["stage"] == "interview" and st.get("awaiting_time") and not st["closed"]:
            data["awaiting"] = _call("inbox", "mail_state", ws, st["id"], default={}) or {}
        return data

    def due(self, q):
        return _due(self.ws)

    # -- feed and reports
    def events(self, q):
        evs = util.jsonl_read(self.ws.events_path)
        evs.sort(key=lambda e: (e.get("ts") or "", e.get("seq") or 0))
        n = _int(q.get("n"), 150, 1, 1000)
        out = []
        for e in reversed(evs[-n:]):
            data = dict(e.get("data") or {})
            if e.get("kind") == "inbox" and data.get("message_id"):
                data.update(_mail_link(self.ws, data["message_id"]))
            out.append({"ts": e.get("ts"), "kind": e.get("kind"), "text": e.get("text"), "data": data})
        return out

    def reports(self, q):
        latest = brief.latest(self.ws)
        return {"dates": list(reversed(brief.dates(self.ws))), "latest": latest}

    def report(self, q):
        date = q.get("date") or ""
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
            raise ApiError(400, "date must look like 2026-10-19")
        path = self.ws.reports / ("%s.json" % date)
        data = util.read_json(path)
        if data is None:
            raise ApiError(404, "no report for %s" % date)
        md = path.with_suffix(".md")
        data["markdown"] = md.read_text(encoding="utf-8") if md.exists() else ""
        return data

    # -- search plan and filtered postings
    def plan(self, q):
        ws, cfg = self.ws, self.ws.cfg
        mans = [m for _, m in search.manifests(ws)][-14:]
        since = (ws.now() - timedelta(days=14)).date().isoformat()
        rows = ws.db.find("first_seen >= ?", (since,))
        groups = []
        for g in search.plan(cfg):
            name = g["name"]
            last = next((m["groups"][name] for m in reversed(mans) if name in (m.get("groups") or {})), None)
            week = [m["groups"][name] for m in mans[-7:] if name in (m.get("groups") or {})]
            mine = [r for r in rows if name in (r.get("groups") or [])]
            judged = [r for r in mine if r.get("stage") in ("reviewed", "dropped")]
            admitted = [r for r in judged if r.get("status") in ADMITTED]
            f = g["filters"]
            groups.append({
                "name": name, "track": g["track"], "budget": g["budget"],
                "titles": list(f.get("titleSearch") or [])[:6], "title_count": len(f.get("titleSearch") or []),
                "exclusions": len(f.get("titleExclusionSearch") or []),
                "keywords": len(f.get("descriptionSearch") or []), "industries": len(f.get("industryFilter") or []),
                "last": {k: last.get(k) for k in ("status", "rows", "new", "cost", "truncated", "finished_at", "note")}
                if last else None,
                "week": {"rows": sum(x.get("rows") or 0 for x in week), "new": sum(x.get("new") or 0 for x in week),
                         "cost": round(sum(x.get("cost") or 0 for x in week), 4), "runs": len(week)},
                "judged": len(judged), "admitted": len(admitted),
                "admitted_share": round(len(admitted) / len(judged), 3) if judged else None})
        return {"groups": groups, "manifests": [_manifest_summary(m) for m in reversed(mans)],
                "budget": budget.summary(ws),
                "schedule": self.cached("schedule", 60, lambda: _call("schedule", "status", ws)),
                "paused": (ws.data / "PAUSE").exists(), "run_time": (cfg.get("search") or {}).get("run_time")}

    def filtered(self, q):
        ws = self.ws
        since = (ws.now() - timedelta(days=_int(q.get("days"), 14, 1, 120))).date().isoformat()
        rows = ws.db.find("status IN ('dropped','excluded','below_threshold') AND first_seen >= ?", (since,),
                          order="first_seen DESC", limit=_int(q.get("n"), 150, 1, 1000))
        out = []
        for r in rows:
            kind, why = _reason(r)
            item = _row(ws, r)
            item.update(kind=kind, why=util.clip(why, 300))
            out.append(item)
        counts = {}
        for item in out:
            counts[item["kind"]] = counts.get(item["kind"], 0) + 1
        return {"rows": out, "counts": counts}

    # -- referrals, tailoring, inbox, tasks
    def referrals(self, q):
        people = _people(self.ws)
        companies = _companies(self.ws)
        lite = []
        for p in people:
            lite.append({k: p.get(k) for k in ("pid", "company_key", "company", "name", "headline", "title", "location",
                                              "schools", "triage", "triage_reason", "score", "stage", "why",
                                              "found_at", "updated_at", "sub")})
            lite[-1]["has_card"] = bool(p.get("card")) or (self.ws.network / "cards" / ("%s.json" % util.slug(
                str(p.get("pid")), 40))).exists()
        stats = {}
        for p in lite:
            s = stats.setdefault(p.get("company_key") or "", {"people": 0, "kept": 0, "cards": 0, "contacted": 0,
                                                             "referred": 0})
            s["people"] += 1
            if p.get("triage") in ("kept", "recruiter"):
                s["kept"] += 1
            if p["has_card"]:
                s["cards"] += 1
            if p.get("stage") not in (None, "", "new", "card_ready", "dropped"):
                s["contacted"] += 1
            if p.get("stage") == "referred":
                s["referred"] += 1
        return {"available": self.session(q)["features"]["network"], "companies": companies, "people": lite,
                "stats": stats}

    def person(self, q):
        p = _person(self.ws, q.get("id") or "")
        if not p:
            raise ApiError(404, "no person %s" % q.get("id"))
        sig = _call("network", "email_signature", self.ws, default=None)
        p["email_signature"] = sig if sig is not None else [((self.ws.cfg.get("candidate") or {}).get("name") or "")]
        return p

    def tailored(self, q):
        return _tailored(self.ws)

    def inbox(self, q):
        return {"available": _mod("inbox") is not None, "status": _call("inbox", "status", self.ws),
                "unmatched": _call("inbox", "unmatched", self.ws, default=[]) or []}

    def tasks(self, q):
        return [] if self.static else tasks.list(self.ws, _int(q.get("n"), 20, 1, 100))


READS = ("session", "summary", "queue", "pool", "posting", "apps", "app", "due", "events", "reports", "report",
         "plan", "filtered", "referrals", "person", "tailored", "inbox", "tasks")


# ---------------------------------------------------------------- writes

class Writes:
    def __init__(self, ws):
        self.ws = ws

    def _audit(self, action, body):
        clean = {k: (v[:300] if isinstance(v, str) else v) for k, v in (body or {}).items() if k != "key"}
        util.jsonl_append(self.ws.data / "dashboard-actions.jsonl", {"action": action, "body": clean})

    def _no_demo(self, what):
        if is_demo(self.ws):
            raise ApiError(409, "This is the demo workspace with fictional data, so %s is turned off. Create your own "
                                "workspace with /jobpilot:setup." % what)

    def posting_mark(self, body):
        pid = _text(body, "id", 80, required=True)
        status = _text(body, "status", 20, required=True)
        if status not in ("ready", "skipped", "hold"):
            raise ApiError(400, "status must be ready, skipped or hold")
        note = _text(body, "note", 300)
        row = pool.mark(self.ws, pid, status, note=note, by="dashboard")
        self.ws.event("pool", "%s: %s at %s -> %s" % (pid, row.get("title"), row.get("company"), status), id=pid)
        return {"ok": True, "posting": _row(self.ws, row)}

    def app_create(self, body):
        ws = self.ws
        pid = _text(body, "posting_id", 80)
        posting = ws.db.get(pid) if pid else None
        if pid and not posting:
            raise ApiError(404, "no posting %s" % pid)
        company = _text(body, "company", 200) or (posting or {}).get("company") or ""
        role = _text(body, "role", 200) or (posting or {}).get("title") or ""
        if not company or not role:
            raise ApiError(400, "an application needs a company and a role")
        url = _url(_text(body, "url", 500)) or (posting or {}).get("apply_url") or (posting or {}).get("url") or ""
        applied = bool(body.get("applied"))
        date = _text(body, "date", 10) or ws.today()
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
            raise ApiError(400, "date must look like 2026-10-19")
        app_id = ledger.create(ws, company, role, posting_id=pid or None, url=url,
                               track=(posting or {}).get("track") or _text(body, "track", 24),
                               location=(posting or {}).get("location") or "", note=_text(body, "note", 500),
                               by="dashboard")
        if applied and ledger.state(ws, app_id)["stage"] == "planned":
            ledger.record(ws, app_id, "applied", date=date, by="dashboard")
        if posting:
            pool.mark(ws, posting["id"], "applied" if applied else "hold", note="application %s" % app_id,
                      app_id=app_id, by="dashboard")
        ws.event("app", "%s %s: %s — %s" % (app_id, "applied" if applied else "saved", company, role), app_id=app_id)
        after = int((ws.cfg.get("followup") or {}).get("after_days") or 10)
        return {"ok": True, "app": _app_lite(ws, ledger.state(ws, app_id), after)}

    def app_event(self, body):
        ws = self.ws
        app_id = _text(body, "app_id", 12, required=True)
        event = _text(body, "event", 30, required=True)
        if event not in ledger.EVENTS or event == "created":
            raise ApiError(400, "unknown event %s" % event)
        fields = {"by": "dashboard"}
        for key, n in (("date", 10), ("deadline", 25), ("time", 25), ("note", 1000), ("referrer", 80)):
            value = _text(body, key, n)
            if value:
                fields[key] = value
        if body.get("minutes"):
            fields["minutes"] = _int(body.get("minutes"), None, 5, 600)
        if body.get("rescheduled") is True and event == "interview_scheduled":
            fields["rescheduled"] = True
        if fields.get("date") and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", fields["date"]):
            raise ApiError(400, "date must look like 2026-10-19")
        for key in ("deadline", "time"):
            if fields.get(key) and not util.parse_time(fields[key]):
                raise ApiError(400, "%s must be a date or an ISO time" % key)
        ledger.record(ws, app_id, event, **fields)
        st = ledger.state(ws, app_id)
        if st.get("posting_id") and event in ("applied", "void"):
            try:
                pool.mark(ws, st["posting_id"], "applied" if event == "applied" else "ready",
                          note="application %s%s" % (app_id, " voided" if event == "void" else ""),
                          app_id=app_id, by="dashboard")
            except util.JobPilotError:
                pass
        ws.event("app", "%s %s: %s — %s" % (app_id, event, st["company"], st["role"]), app_id=app_id)
        ics.refresh(ws)
        after = int((ws.cfg.get("followup") or {}).get("after_days") or 10)
        return {"ok": True, "app": _app_lite(ws, st, after)}

    def _inbox(self):
        mod = _mod("inbox")
        if mod is None or not hasattr(mod, "waiting"):
            raise ApiError(501, "the inbox module is not installed")
        return mod

    def inbox_answered(self, body):
        app_id = _text(body, "app_id", 12, required=True).upper()
        return dict(self._inbox().answered(self.ws, app_id, by="dashboard"), ok=True)

    def inbox_invite(self, body):
        app_id = _text(body, "app_id", 12, required=True).upper()
        res = self._inbox().invite(self.ws, app_id, by="dashboard")
        after = int((self.ws.cfg.get("followup") or {}).get("after_days") or 10)
        return dict(res, ok=True, app=_app_lite(self.ws, ledger.state(self.ws, app_id), after))

    def tailor_start(self, body):
        self._no_demo("tailoring")
        pid = _text(body, "posting_id", 80, required=True)
        row = self.ws.db.get(pid)
        if not row:
            raise ApiError(404, "no posting %s" % pid)
        task = tasks.start(self.ws, "tailor", ["tailor", "auto", pid], target=pid)
        self.ws.event("tailor", "tailoring started: %s at %s" % (row.get("title"), row.get("company")), id=pid)
        return {"ok": True, "task": task}

    def run_daily(self, body):
        self._no_demo("the paid search")
        task = tasks.start(self.ws, "daily", ["daily", "--quiet"])
        self.ws.event("daily", "daily run started from the dashboard")
        return {"ok": True, "task": task}

    def net_mark(self, body):
        pid = _text(body, "pid", 40, required=True)
        event = _text(body, "event", 30, required=True)
        if event not in NET_EVENTS:
            raise ApiError(400, "unknown referral event %s" % event)
        mod = _mod("network")
        if mod is None or not hasattr(mod, "mark"):
            raise ApiError(501, "the referrals module is not installed")
        person = mod.mark(self.ws, pid, event, note=_text(body, "note", 500))
        return {"ok": True, "person": person}

    def net_find(self, body):
        self._no_demo("the paid alumni search")
        company = _text(body, "company", 120, required=True)
        argv = ["net", "find", company]
        url = _url(_text(body, "url", 300))
        if url:
            argv += ["--url", url]
        if body.get("n"):
            argv += ["--n", str(_int(body.get("n"), 30, 5, 100))]
        task = tasks.start(self.ws, "net-find", argv, target=util.company_key(company))
        self.ws.event("net", "alumni search started: %s" % company)
        return {"ok": True, "task": task}

    def net_cards(self, body):
        self._no_demo("card writing")
        company = _text(body, "company", 120, required=True)
        argv = ["net", "cards", company]
        if body.get("n"):
            argv += ["--n", str(_int(body.get("n"), 5, 1, 10))]
        task = tasks.start(self.ws, "net-cards", argv, target=util.company_key(company))
        self.ws.event("net", "writing outreach cards: %s" % company)
        return {"ok": True, "task": task}

    def net_email(self, body):
        self._no_demo("email writing")
        pid = _text(body, "pid", 12, required=True).upper()
        if not re.fullmatch(r"P\d{3,6}", pid):
            raise ApiError(400, "pid must look like P0007")
        argv = ["net", "email", pid]
        feedback = _text(body, "feedback", 1000)
        if feedback:
            argv += ["--feedback", feedback]
        task = tasks.start(self.ws, "net-email", argv, target=pid)
        self.ws.event("net", "writing the cold email for %s" % pid, pid=pid)
        return {"ok": True, "task": task}


WRITES = {"posting/mark": "posting_mark", "app/create": "app_create", "app/event": "app_event",
          "inbox/answered": "inbox_answered", "inbox/invite": "inbox_invite", "tailor/start": "tailor_start",
          "run/daily": "run_daily", "net/mark": "net_mark", "net/find": "net_find", "net/cards": "net_cards",
          "net/email": "net_email"}


# ---------------------------------------------------------------- page

def page(nonce, boot, static_data=None):
    html = (HERE / "app.html").read_text(encoding="utf-8")
    boot_json = json.dumps(boot, ensure_ascii=False).replace("</", "<\\/")
    html = html.replace("{{NONCE}}", nonce).replace("/*BOOT*/null", boot_json)
    blob = ""
    if static_data is not None:
        payload = json.dumps(static_data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
        blob = '<script id="jp-static" type="application/json">%s</script>' % payload
    return html.replace("<!--STATIC-->", blob)


# ---------------------------------------------------------------- HTTP

class Dashboard:
    def __init__(self, ws):
        self.ws = ws
        self.key = load_key(ws)
        self.cookie_name = "jp_%s" % hashlib.sha256(str(ws.root).encode()).hexdigest()[:10]
        self.session_value = _mac(self.key, "session")
        self.csrf = _mac(self.key, "csrf")
        self.views = Views(ws)
        self.writes = Writes(ws)
        self.port = None
        self.fails = []
        self.lock = threading.Lock()

    def key_ok(self, value):
        return bool(value) and hmac.compare_digest(str(value).strip(), self.key)

    def throttled(self):
        now = time.monotonic()
        with self.lock:
            self.fails = [t for t in self.fails if now - t < 60]
            return len(self.fails) >= 8

    def failed(self):
        with self.lock:
            self.fails.append(time.monotonic())


def make_handler(app):
    class Handler(BaseHTTPRequestHandler):
        server_version = "JobPilot"
        sys_version = ""

        def log_message(self, fmt, *args):  # quiet: no request log on the terminal
            return

        # -- helpers
        def _host_ok(self):
            host = (self.headers.get("Host") or "").lower()
            return host in ("127.0.0.1:%d" % app.port, "localhost:%d" % app.port)

        def _origin_ok(self):
            origin = (self.headers.get("Origin") or "").lower()
            return origin in ("http://127.0.0.1:%d" % app.port, "http://localhost:%d" % app.port)

        def _authed(self):
            raw = self.headers.get("Cookie") or ""
            for part in raw.split(";"):
                name, _, value = part.strip().partition("=")
                if name == app.cookie_name and hmac.compare_digest(value, app.session_value):
                    return True
            return False

        def _cookie(self, clear=False):
            value = "" if clear else app.session_value
            age = 0 if clear else 30 * 86400
            return "%s=%s; Path=/; HttpOnly; SameSite=Strict; Max-Age=%d" % (app.cookie_name, value, age)

        def _send(self, status, body, ctype="application/json; charset=utf-8", headers=None, nonce=None):
            data = body if isinstance(body, bytes) else body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Cross-Origin-Opener-Policy", "same-origin")
            self.send_header("Cross-Origin-Resource-Policy", "same-origin")
            csp = ("default-src 'none'; img-src 'self' data:; connect-src 'self'; base-uri 'none'; form-action 'self'; "
                   "frame-ancestors 'none'")
            if nonce:
                csp += "; script-src 'nonce-%s'; style-src 'nonce-%s'" % (nonce, nonce)
            self.send_header("Content-Security-Policy", csp)
            if ctype.startswith("application/json") or status >= 300:
                self.send_header("Cache-Control", "no-store")
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(data)

        def _json(self, status, obj, headers=None):
            self._send(status, json.dumps(obj, ensure_ascii=False, default=str), headers=headers)

        def _error(self, status, message):
            self._json(status, {"error": message})

        # -- GET
        def do_HEAD(self):
            self.do_GET()

        def do_GET(self):
            if not self._host_ok():
                return self._send(421, "wrong host", "text/plain; charset=utf-8")
            parts = urlsplit(self.path)
            q = {k: v[-1] for k, v in parse_qs(parts.query).items()}
            path = parts.path
            if path in ("/", "/index.html"):
                if q.get("key"):
                    if app.key_ok(q["key"]):
                        return self._send(302, "", "text/plain", headers={"Location": "/", "Set-Cookie": self._cookie()})
                    app.failed()
                nonce = secrets.token_urlsafe(16)
                return self._send(200, page(nonce, {"mode": "live"}), "text/html; charset=utf-8", nonce=nonce,
                                  headers={"Cache-Control": "no-store"})
            if path == "/favicon.svg" or path == "/favicon.ico":
                return self._send(200, FAVICON, "image/svg+xml", headers={"Cache-Control": "max-age=86400"})
            if path.startswith("/api/"):
                if not self._authed():
                    return self._error(401, "sign in: open the link `jobpilot dashboard` printed")
                name = path[5:]
                if name == "flow.svg":
                    try:
                        return self._send(200, app.views.flow_svg(q), "image/svg+xml; charset=utf-8",
                                          headers={"Cache-Control": "no-store"})
                    except Exception as exc:  # noqa: BLE001 - a view error must not kill the server
                        return self._error(500, "%s: %s" % (type(exc).__name__, exc))
                if name not in READS:
                    return self._error(404, "unknown view %s" % name)
                try:
                    if name == "session":
                        return self._json(200, app.views.session(q, csrf=app.csrf))
                    return self._json(200, getattr(app.views, name)(q))
                except ApiError as exc:
                    return self._error(exc.status, str(exc))
                except util.JobPilotError as exc:
                    return self._error(400, str(exc))
                except Exception as exc:  # noqa: BLE001 - a view error must not kill the server
                    return self._error(500, "%s: %s" % (type(exc).__name__, exc))
            if path == "/files":
                if not self._authed():
                    return self._error(401, "sign in first")
                return self._file(q.get("path") or "")
            if path == "/calendar.ics":
                if not self._authed():
                    return self._error(401, "sign in first")
                text, _ = ics.build(app.ws)
                return self._send(200, text, "text/calendar; charset=utf-8",
                                  headers={"Content-Disposition": 'attachment; filename="jobpilot.ics"',
                                           "Cache-Control": "no-store"})
            return self._send(404, "not found", "text/plain; charset=utf-8")

        def _file(self, rel):
            base = app.ws.tailored.resolve()
            try:
                target = (app.ws.root / rel).resolve()
                target.relative_to(base)
            except (ValueError, OSError):
                return self._error(403, "only files under tailored/ can be downloaded")
            if not target.is_file() or target.suffix.lower() not in (".pdf", ".docx", ".md", ".txt", ".json"):
                return self._error(404, "no such file")
            ctype = {".pdf": "application/pdf", ".md": "text/markdown; charset=utf-8",
                     ".txt": "text/plain; charset=utf-8", ".json": "application/json; charset=utf-8",
                     ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}[
                target.suffix.lower()]
            disp = "inline" if target.suffix.lower() == ".pdf" else "attachment"
            name = re.sub(r"[^A-Za-z0-9._-]", "_", target.name)
            return self._send(200, target.read_bytes(), ctype,
                              headers={"Content-Disposition": '%s; filename="%s"' % (disp, name),
                                       "Cache-Control": "no-store"})

        # -- POST
        def do_POST(self):
            if not self._host_ok():
                return self._send(421, "wrong host", "text/plain; charset=utf-8")
            if not self._origin_ok():
                return self._error(403, "cross-origin request refused")
            ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            if ctype != "application/json":
                return self._error(415, "send JSON")
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                return self._error(400, "bad length")
            if length > MAX_BODY:
                return self._error(413, "request too large")
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
            except ValueError:
                return self._error(400, "invalid JSON")
            if not isinstance(body, dict):
                return self._error(400, "send a JSON object")
            path = urlsplit(self.path).path
            if path == "/api/login":
                if app.throttled():
                    return self._error(429, "too many attempts; wait a minute")
                if not app.key_ok(body.get("key")):
                    app.failed()
                    return self._error(403, "that key does not match data/dashboard.token")
                return self._json(200, {"ok": True}, headers={"Set-Cookie": self._cookie()})
            if path == "/api/logout":
                return self._json(200, {"ok": True}, headers={"Set-Cookie": self._cookie(clear=True)})
            if not self._authed():
                return self._error(401, "sign in first")
            if not hmac.compare_digest(self.headers.get("X-JobPilot-CSRF") or "", app.csrf):
                return self._error(403, "missing or wrong CSRF token; reload the page")
            name = WRITES.get(path[5:]) if path.startswith("/api/") else None
            if not name:
                return self._error(404, "unknown action")
            try:
                res = getattr(app.writes, name)(body)
                app.writes._audit(path[5:], body)
                return self._json(200, res)
            except ApiError as exc:
                return self._error(exc.status, str(exc))
            except util.JobPilotError as exc:
                return self._error(400, str(exc))
            except Exception as exc:  # noqa: BLE001
                return self._error(500, "%s: %s" % (type(exc).__name__, exc))

    return Handler


def make_server(ws, port=None, tries=10):
    """(server, app) bound to 127.0.0.1 on `port` or the next free one."""
    app = Dashboard(ws)
    start = int((ws.cfg.get("dashboard") or {}).get("port") or 8765) if port is None else int(port)
    last = None
    for p in ([0] if start == 0 else range(start, start + max(1, tries) + 1)):
        try:
            server = ThreadingHTTPServer(("127.0.0.1", p), make_handler(app))
            break
        except OSError as exc:
            last = exc
            continue
    else:
        raise util.JobPilotError("no free port from %d to %d (%s)" % (start, start + tries, last))
    server.daemon_threads = True
    app.port = server.server_address[1]
    return server, app


def serve(ws, port=None, open_browser=True):
    server, app = make_server(ws, port)
    url = "http://127.0.0.1:%d/?key=%s" % (app.port, app.key)
    print("JobPilot dashboard: %s" % url)
    print("(local only; keep this link private. Ctrl-C stops the dashboard.)")
    sys.stdout.flush()
    if open_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


# ---------------------------------------------------------------- static export

def export_static(ws, out_path):
    """Write one self-contained HTML file with every read view of `ws` embedded; writes are disabled in it.
    It contains the workspace's data (postings, applications, referrals): share only demo exports."""
    v = Views(ws, static=True)
    data = {"session": v.session({}), "summary": v.summary({}), "pool_all": v.pool_all(), "queue": v.queue({"n": 200}),
            "apps": v.apps({}), "due": v.due({}), "events": v.events({"n": 300}), "reports": v.reports({}),
            "plan": v.plan({}), "filtered": v.filtered({"days": 60, "n": 400}), "referrals": v.referrals({}),
            "tailored": v.tailored({}), "inbox": v.inbox({}), "tasks": [],
            "posting": {}, "app": {}, "person": {}, "report": {}}
    for r in ws.db.find("1=1", order="first_seen DESC", limit=600):
        try:
            data["posting"][r["id"]] = v.posting({"id": r["id"]})
        except ApiError:
            continue
    for a in data["apps"]["apps"]:
        data["app"][a["id"]] = v.app({"id": a["id"]})
    for p in data["referrals"]["people"]:
        try:
            data["person"][p["pid"]] = v.person({"id": p["pid"]})
        except ApiError:
            continue
    for d in data["reports"]["dates"][:14]:
        try:
            data["report"][d] = v.report({"date": d})
        except ApiError:
            continue
    # the flow pre-rendered for each language, theme and width the page may need
    data["flow_svg"] = {lang: {theme: {str(w): flow.render_svg(flow.compute(ws, lang), theme=theme, width=w, lang=lang)
                                       for w in FLOW_WIDTHS} for theme in flow.THEMES} for lang in flow.LABELS}
    html = page("static", {"mode": "static"}, data)
    html = html.replace('<link rel="icon" href="/favicon.svg">',
                        '<link rel="icon" href="data:image/svg+xml,%s">' % FAVICON.replace("#", "%23")
                        .replace('"', "'"))
    util.atomic_write_text(Path(out_path), html)
    return Path(out_path)


def _main(argv):
    import argparse
    from .. import workspace
    p = argparse.ArgumentParser(prog="python3 -m jobpilot.dashboard.server")
    p.add_argument("--workspace")
    p.add_argument("--export", help="write a self-contained HTML file and exit")
    p.add_argument("--port", type=int)
    p.add_argument("--no-open", action="store_true")
    args = p.parse_args(argv)
    ws = workspace.find(explicit=args.workspace)
    if args.export:
        print(export_static(ws, args.export))
        return 0
    return serve(ws, port=args.port, open_browser=not args.no_open)


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
