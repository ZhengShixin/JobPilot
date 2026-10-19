"""A calendar file of what is coming up (data/calendar.ics): interviews with their time and length, and assessment
deadlines, for any calendar app that imports iCalendar (Apple Calendar, Google Calendar, Outlook).

    build(ws) -> (text, count)        refresh(ws) -> path or None (never raises; after every ledger change)

Each entry keeps the same UID while it exists, so importing a newer file updates an entry instead of adding a second
one (a rescheduled interview moves). Times are written in UTC; a time recorded without an offset is read in the
workspace's time zone. JobPilot only writes the file: it never connects to a calendar account.
"""
import hashlib
from datetime import datetime, timedelta, timezone

from . import ledger, util

PRODID = "-//JobPilot//Applications//EN"


def _esc(text):
    return (str(text or "").replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")
            .replace("\r\n", "\\n").replace("\n", "\\n"))


def _fold(line):
    """RFC 5545 line folding: at most 75 octets per line, continuation lines start with a space."""
    data = line.encode("utf-8")
    if len(data) <= 75:
        return line
    out, cur = [], b""
    for ch in line:
        b = ch.encode("utf-8")
        if len(cur) + len(b) > (75 if not out else 74):
            out.append(cur.decode("utf-8"))
            cur = b""
        cur += b
    out.append(cur.decode("utf-8"))
    return "\r\n ".join(out)


def _utc(dt):
    return dt.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _when(ws, value):
    """An aware datetime (a time without an offset is the workspace's local time), or None."""
    text = str(value or "").strip()
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=ws.tz)


def entries(ws, days_back=1, days_ahead=180):
    """[{uid, kind, start, end, all_day, summary, description, alarm}] of open interviews and assessment deadlines."""
    now = ws.now()
    lo, hi = now - timedelta(days=days_back), now + timedelta(days=days_ahead)
    space = hashlib.sha256(str(ws.root).encode()).hexdigest()[:10]
    out = []
    for st in ledger.states(ws).values():
        if st["voided"] or st["closed"]:
            continue
        seq = len(st["timeline"])
        what = "%s — %s" % (st["company"], st["role"])
        for i, iv in enumerate(st["interviews"]):
            start = _when(ws, iv.get("time"))
            if iv.get("done") or not start or not lo <= start <= hi:
                continue
            minutes = iv.get("minutes") or 60
            note = ["Application %s: %s" % (st["id"], what)]
            if iv.get("source") == "gmail":
                note.append("Time agreed by email.")
            if not iv.get("minutes"):
                note.append("Length not recorded; one hour is a placeholder.")
            out.append({"uid": "jobpilot-%s-%s-interview-%d@jobpilot" % (space, st["id"], i + 1), "kind": "interview",
                        "start": start, "end": start + timedelta(minutes=int(minutes)), "all_day": False,
                        "summary": "Interview: %s" % what, "description": "\n".join(note), "alarm": "-PT1H",
                        "sequence": seq})
        for i, d in enumerate(st["deadlines"]):
            due = str(d.get("due") or "")
            day = _when(ws, due[:10])
            if d.get("done") or day is None or not lo.date() <= day.date() <= hi.date():
                continue
            note = ["Application %s: %s" % (st["id"], what), "Take the assessment yourself."]
            if len(due) > 10:
                exact = _when(ws, due)
                if exact:
                    note.insert(1, "Due %s." % exact.astimezone(ws.tz).strftime("%Y-%m-%d %H:%M %Z"))
            out.append({"uid": "jobpilot-%s-%s-assessment-%d@jobpilot" % (space, st["id"], i + 1),
                        "kind": "assessment", "start": day, "end": day + timedelta(days=1), "all_day": True,
                        "summary": "Assessment due: %s" % what, "description": "\n".join(note), "alarm": "-P1D",
                        "sequence": seq})
    out.sort(key=lambda e: (e["start"].date(), e["all_day"] is False, e["start"]))
    return out


def build(ws):
    """(the iCalendar text, the number of entries)."""
    stamp = _utc(util.now())
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:%s" % PRODID, "CALSCALE:GREGORIAN", "METHOD:PUBLISH",
             "X-WR-CALNAME:JobPilot"]
    items = entries(ws)
    for e in items:
        lines += ["BEGIN:VEVENT", "UID:%s" % e["uid"], "DTSTAMP:%s" % stamp, "SEQUENCE:%d" % e["sequence"]]
        if e["all_day"]:
            lines += ["DTSTART;VALUE=DATE:%s" % e["start"].strftime("%Y%m%d"),
                      "DTEND;VALUE=DATE:%s" % e["end"].strftime("%Y%m%d"), "TRANSP:TRANSPARENT"]
        else:
            lines += ["DTSTART:%s" % _utc(e["start"]), "DTEND:%s" % _utc(e["end"])]
        lines += ["SUMMARY:%s" % _esc(e["summary"]), "DESCRIPTION:%s" % _esc(e["description"]),
                  "BEGIN:VALARM", "ACTION:DISPLAY", "DESCRIPTION:%s" % _esc(e["summary"]),
                  "TRIGGER:%s" % e["alarm"], "END:VALARM", "END:VEVENT"]
    lines.append("END:VCALENDAR")
    return "".join(_fold(line) + "\r\n" for line in lines), len(items)


def path(ws):
    return ws.data / "calendar.ics"


def write(ws, out=None):
    text, n = build(ws)
    target = out or path(ws)
    util.atomic_write_text(target, text, mode=0o600 if out is None else 0o644)
    return target, n


def refresh(ws):
    """Rewrite data/calendar.ics after a ledger change; a failure never stops the change itself."""
    try:
        return write(ws)[0]
    except Exception:  # noqa: BLE001 - the calendar file is a convenience
        return None
