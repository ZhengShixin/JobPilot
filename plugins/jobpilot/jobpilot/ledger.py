"""The application ledger (data/apps.jsonl): one append-only, hash-chained event per thing that happened.

An application's state is derived from its events, never stored: the stage is the furthest milestone reached
(planned -> applied -> oa -> interview -> offer) unless a closing event (rejected, withdrawn, closed) ended it.
`void` takes back an application recorded by mistake (it then counts nowhere).

    create(ws, company=, role=, posting_id=, url=, track=)        -> app id "A001"
    record(ws, app_id, event, date=, deadline=, time=, note=, by=)
    state(ws, app_id) / states(ws)                                 -> derived dicts

A time written without a UTC offset (the dashboard's date-time field, `--time 2026-11-03T14:00`) is your local time:
it is stored with the workspace time zone's offset.
"""
import re
from datetime import datetime

from . import util

EVENTS = {
    # event: (stage it moves to or None, label)
    "created": ("planned", "Saved to apply"),
    "tailored": (None, "Resume tailored"),
    "applied": ("applied", "Applied"),
    "receipt": (None, "Application received"),
    "referral": (None, "Referral"),
    "followup_sent": (None, "Follow-up sent"),
    "recruiter_reply": (None, "Recruiter replied"),
    "oa_invited": ("oa", "Assessment invited"),
    "oa_done": ("oa", "Assessment done"),
    "interview_invited": ("interview", "Interview invited"),
    "interview_scheduled": ("interview", "Interview scheduled"),
    "interview_done": ("interview", "Interview done"),
    "offer": ("offer", "Offer"),
    "rejected": ("rejected", "Rejected"),
    "withdrawn": ("withdrawn", "Withdrawn"),
    "closed": ("closed", "Closed (no response)"),
    "reopened": (None, "Reopened"),
    "edited": (None, "Details edited"),
    "note": (None, "Note"),
    "void": (None, "Voided"),
}
ORDER = {"planned": 0, "applied": 1, "oa": 2, "interview": 3, "offer": 4}
TERMINAL = ("rejected", "withdrawn", "closed")
FIELDS = ("company", "role", "posting_id", "url", "track", "date", "deadline", "time", "note", "minutes", "by",
          "source", "message_id", "referrer", "location", "rescheduled")


def events(ws, app_id=None):
    out = util.jsonl_read(ws.apps_path)
    return [e for e in out if e.get("app_id") == app_id] if app_id else out


def next_id(ws):
    n = 0
    for e in events(ws):
        try:
            n = max(n, int(str(e.get("app_id", "A0"))[1:]))
        except ValueError:
            continue
    return "A%03d" % (n + 1)


def find(ws, company=None, role=None, posting_id=None, include_closed=True):
    out = []
    ck = util.company_key(company) if company else None
    for st in states(ws).values():
        if st["voided"] or (not include_closed and st["closed"]):
            continue
        if posting_id and st.get("posting_id") == str(posting_id):
            out.append(st)
        elif ck and util.company_key(st.get("company")) == ck and (
                not role or util.norm_title(role) == util.norm_title(st.get("role"))):
            out.append(st)
    return out


def create(ws, company, role, posting_id=None, url="", track="", date=None, note="", by="user", location=""):
    if not (company or "").strip() or not (role or "").strip():
        raise util.JobPilotError("an application needs a company and a role")
    if posting_id:
        dup = [s for s in find(ws, posting_id=posting_id) if not s["voided"]]
        if dup:
            return dup[0]["id"]
    with ws.lock("ledger"):
        app_id = next_id(ws)
        _append(ws, app_id, "created", company=company.strip(), role=role.strip(), posting_id=str(posting_id or "") or None,
                url=url or None, track=track or None, date=date or ws.today(), note=note or None, by=by,
                location=location or None)
    return app_id


def _append(ws, app_id, event, **fields):
    rec = {"app_id": app_id, "event": event}
    rec.update({k: v for k, v in fields.items() if v not in (None, "") and k in FIELDS})
    return util.jsonl_append(ws.apps_path, rec)


def record(ws, app_id, event, **fields):
    if event not in EVENTS or event == "created":
        raise util.JobPilotError("event must be one of: %s" % ", ".join(e for e in EVENTS if e != "created"))
    st = state(ws, app_id)
    if st is None:
        raise util.JobPilotError("no application %s" % app_id)
    if st["voided"] and event != "note":
        raise util.JobPilotError("%s was voided" % app_id)
    if event == "void" and st["stage"] not in ("planned", "applied"):
        raise util.JobPilotError("only an application without later progress can be voided; record the outcome instead")
    if event == "reopened" and not st["closed"]:
        raise util.JobPilotError("%s is not closed" % app_id)
    fields.setdefault("date", ws.today())
    fields.setdefault("by", "user")
    for key in ("time", "deadline"):
        if fields.get(key):
            fields[key] = _localize(ws, fields[key])
    with ws.lock("ledger"):
        rec = _append(ws, app_id, event, **fields)
    return rec


def _localize(ws, value):
    """'2026-11-03T14:00' -> '2026-11-03T14:00-05:00' (the workspace's zone); dates and times with an offset stay."""
    text = str(value).strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2})?", text):
        return datetime.fromisoformat(text).replace(tzinfo=ws.tz).isoformat(timespec="minutes")
    return text


def _derive(evs, today=None):
    first = evs[0]
    st = {"id": first["app_id"], "company": first.get("company", ""), "role": first.get("role", ""),
          "posting_id": first.get("posting_id"), "url": first.get("url"), "track": first.get("track"),
          "location": first.get("location"), "created": first.get("date") or first.get("ts", "")[:10],
          "stage": "planned", "closed": False, "voided": False, "applied_date": None, "last_date": None,
          "deadlines": [], "interviews": [], "notes": [], "timeline": [], "referrer": None, "oa_rounds": 0,
          "interview_rounds": 0, "awaiting_time": False, "invited_date": None}
    best = 0
    for e in evs:
        ev = e.get("event")
        date = e.get("date") or e.get("ts", "")[:10]
        st["timeline"].append({k: e[k] for k in ("event", "date", "ts", "note", "deadline", "time", "by", "source",
                                                 "message_id") if e.get(k)})
        if ev == "void":
            st["voided"] = True
            continue
        if ev == "reopened":
            st["closed"] = False
            st["stage"] = [s for s, n in ORDER.items() if n == best][0]
            continue
        if ev in ("created", "edited"):
            for k in ("company", "role", "url", "track", "posting_id", "location"):
                if e.get(k):
                    st[k] = e[k]
        if ev == "referral" and e.get("referrer"):
            st["referrer"] = e["referrer"]
        if ev == "note" and e.get("note"):
            st["notes"].append({"date": date, "note": e["note"]})
        if ev == "applied":
            st["applied_date"] = st["applied_date"] or date
        if ev == "oa_invited":
            st["oa_rounds"] += 1
            if e.get("deadline"):
                st["deadlines"].append({"kind": "oa", "due": e["deadline"], "done": False})
        if ev == "oa_done":
            for d in st["deadlines"]:
                if d["kind"] == "oa":
                    d["done"] = True
        if ev in ("interview_invited", "interview_scheduled"):
            if ev == "interview_invited":
                st["interview_rounds"] += 1
                st["invited_date"] = date
            st["awaiting_time"] = not e.get("time")   # an invitation without a time yet
            if e.get("time"):
                _book(st["interviews"], e)
        if ev == "interview_done":
            st["awaiting_time"] = False
            for iv in st["interviews"]:
                if not iv["done"]:
                    iv["done"] = True
                    break
        if ev == "offer":
            st["awaiting_time"] = False
        stage = EVENTS.get(ev, (None,))[0]
        if stage in ORDER and ORDER[stage] >= best and not st["closed"]:
            best = ORDER[stage]
            st["stage"] = stage
        elif stage in TERMINAL:
            st["stage"] = stage
            st["closed"] = True
        if ev != "note":
            st["last_date"] = date
    st["applied"] = best >= ORDER["applied"] and not st["voided"]
    st["responded"] = best >= ORDER["oa"] or any(
        t["event"] in ("rejected", "recruiter_reply", "oa_invited", "interview_invited", "offer") for t in st["timeline"])
    return st


def _book(interviews, e):
    """Add an interview time: a rescheduled one replaces the open booking it moves; the same start twice is one."""
    booking = {"time": e["time"], "minutes": e.get("minutes"), "done": False, "source": e.get("source")}
    open_ = [iv for iv in interviews if not iv["done"]]
    when = util.parse_time(e["time"])
    if e.get("rescheduled") and open_:
        open_[-1].update(booking)
    elif not any(when and util.parse_time(iv["time"]) == when for iv in open_):
        interviews.append(booking)


def states(ws):
    grouped = {}
    for e in events(ws):
        grouped.setdefault(e.get("app_id"), []).append(e)
    return {k: _derive(v) for k, v in grouped.items() if v and v[0].get("event") == "created"}


def state(ws, app_id):
    return states(ws).get(app_id)


def due(ws, days_ahead=7):
    """Upcoming deadlines and interviews, interviews still waiting for a time, and applications that deserve a
    follow-up."""
    now = ws.now()
    out = {"deadlines": [], "interviews": [], "unscheduled": [], "followups": []}
    after = float(ws.cfg["followup"].get("after_days") or 10)
    for st in states(ws).values():
        if st["voided"] or st["closed"]:
            continue
        for d in st["deadlines"]:
            when = util.parse_time(d["due"])
            if not d["done"] and when and (when - now).total_seconds() < days_ahead * 86400:
                out["deadlines"].append({"app_id": st["id"], "company": st["company"], "role": st["role"],
                                         "due": d["due"], "overdue": when < now})
        for iv in st["interviews"]:
            when = util.parse_time(iv["time"])
            if not iv["done"] and when and (when - now).total_seconds() < days_ahead * 86400:
                out["interviews"].append({"app_id": st["id"], "company": st["company"], "role": st["role"],
                                          "time": iv["time"], "minutes": iv.get("minutes"),
                                          "source": iv.get("source"), "past": when < now})
        if st["stage"] == "interview" and st["awaiting_time"]:
            out["unscheduled"].append({"app_id": st["id"], "company": st["company"], "role": st["role"],
                                       "invited": st["invited_date"]})
        if st["stage"] == "applied":
            age = util.days_between(st.get("last_date") or st.get("applied_date"), now)
            if age is not None and age >= after:
                out["followups"].append({"app_id": st["id"], "company": st["company"], "role": st["role"],
                                         "days": int(age)})
    out["deadlines"].sort(key=lambda d: d["due"])
    out["interviews"].sort(key=lambda d: util.parse_time(d["time"]))
    out["unscheduled"].sort(key=lambda d: d["invited"] or "")
    out["followups"].sort(key=lambda d: -d["days"])
    return out


def funnel(ws):
    sts = [s for s in states(ws).values() if not s["voided"]]
    applied = [s for s in sts if s["applied"]]
    reached = lambda stage: sum(1 for s in applied if ORDER.get(s["stage"], -1) >= ORDER[stage] or (  # noqa: E731
        s["closed"] and any(t["event"] in _REACHES[stage] for t in s["timeline"])))
    return {"saved": len(sts), "applied": len(applied), "oa": reached("oa"), "interview": reached("interview"),
            "offer": reached("offer"), "rejected": sum(1 for s in applied if s["stage"] == "rejected"),
            "open": sum(1 for s in applied if not s["closed"])}


_REACHES = {
    "oa": ("oa_invited", "oa_done", "interview_invited", "interview_scheduled", "interview_done", "offer"),
    "interview": ("interview_invited", "interview_scheduled", "interview_done", "offer"),
    "offer": ("offer",),
}
