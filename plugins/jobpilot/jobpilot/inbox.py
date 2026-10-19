"""Gmail progress tracking: application emails (receipts, rejections, assessments, interviews, offers, recruiter
replies, your own replies) become ledger events, so the pipeline keeps itself up to date. The triage works like a
secretary: it reads each job thread in full, your replies included, and adds a short summary and the next step to
every new message; an interview time both sides agreed by email becomes the interview's time.

Read-only by design: nothing here labels, archives, sends, drafts or deletes mail.

    brief(ws)            what the /jobpilot:inbox skill needs: the time window, open applications, message ids
                         already processed, ready-made Gmail queries and the results schema
    apply(ws, results)   the skill's classified messages -> checked -> ledger events (source "gmail", message id)
    link(ws, msg, app)   an unmatched email recorded against an application by hand
    waiting(ws)          recruiter mail that asks you to act and is still unanswered (the dashboard's Up next);
                         answered(ws, app) stops the reminder, invite(ws, app) records the mail as an interview
                         invitation
    scan_headless(ws)    experimental: the same triage as one `claude -p` run with the claude.ai Gmail connector

The session reads the mail with your own Gmail connector; the code then refuses anything it cannot check: an unknown
category, a duplicate message, an application that does not exist, or progress on an application already closed
(recorded as a note instead, so you can reopen it yourself). A message you sent can only be your reply or an agreed
interview time; an agreed time counts only when the quoted sentence states that hour (and day, when it names one) and
the time lies between a day before the email and 180 days after it, else the message stays an invitation (or your
reply). Summaries with links, addresses, long numbers or amounts of money are dropped.
"""
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlsplit

from . import claude, config, ledger, schema as S, util

PROMPTS = Path(__file__).resolve().parent / "prompts"
CATEGORIES = {
    "receipt": "the employer confirms it received the application",
    "rejection": "the employer declines or closes the candidacy",
    "oa_invite": "an online assessment, coding test, recorded video interview or take-home task",
    "interview_invite": "an invitation to interview or to give availability for one, including a phone screen or an "
                        "introductory call a recruiter or hiring manager asks to schedule",
    "interview_scheduled": "the message that fixes the day and time of an interview or call both sides agreed: the "
                           "employer confirms a time or sends a calendar invitation, or the candidate accepts one "
                           "specific time the employer offered (a list of possible times is not an agreement)",
    "offer": "an offer of employment",
    "recruiter_reply": "a person writes personally about the candidacy without inviting the candidate to a call or an "
                       "interview (a question, a follow-up, information)",
    "owner_reply": "any other message the candidate sent in a job thread (offering times, answering a question, "
                   "thanks)",
    "other": "job alerts, newsletters, marketing, verification codes, everything else",
}
# A message the candidate sent is only ever one of these.
OWN = ("interview_scheduled", "owner_reply")
NEXT_STEPS = ("reply", "pick_time", "prepare", "do_oa", "wait", "none")
# Summaries are shown in the dashboard: no links, addresses, codes, phone numbers or money.
SUMMARY_BAD = re.compile(r"(?i)https?://|www\.|@|\d{5,}|\d{3}[\s.-]\d{4}|[$€£¥]\s?\d|\d\s?(?:usd|dollars|k\b)")
# Emails that prove an application was sent (a planned application then counts as applied on that date).
APPLIED_PROOF = ("receipt", "rejection", "oa_invite", "interview_invite", "offer")
EVENT_OF = {"receipt": "receipt", "rejection": "rejected", "oa_invite": "oa_invited",
            "interview_invite": "interview_invited", "interview_scheduled": "interview_scheduled", "offer": "offer",
            "recruiter_reply": "recruiter_reply", "owner_reply": "note"}
ATS_DOMAINS = ("greenhouse.io", "greenhouse-mail.io", "lever.co", "myworkday.com", "myworkdayjobs.com", "workday.com", "ashbyhq.com",
               "icims.com", "smartrecruiters.com", "jobvite.com", "successfactors.com", "taleo.net", "workable.com",
               "bamboohr.com", "oraclecloud.com", "eightfold.ai", "avature.net", "rippling.com", "breezy.hr",
               "hirevue.com", "codesignal.com", "hackerrank.com", "karat.com", "codility.com", "modernhire.com",
               "pymetrics.ai", "testgorilla.com")
SUBJECT_WORDS = ("application", "applying", "interview", "assessment", "\"coding challenge\"", "\"next steps\"",
                 "candidacy", "offer", "\"your interest\"")
# A stage after a recruiter's mail answers it: the mail no longer waits in Up next.
SETTLED = ("oa_invited", "interview_invited", "interview_scheduled", "interview_done", "offer", "rejected", "withdrawn",
           "closed")
# Categories that become their own line in the activity feed (receipts only count in the scan's summary line).
NEWS = ("recruiter_reply", "interview_invite", "interview_scheduled", "owner_reply", "oa_invite", "offer", "rejection")
NEWS_TEXT = {
    "en": {"recruiter_reply": "a recruiter wrote", "interview_invite": "interview invitation by email",
           "interview_scheduled": "interview time agreed by email: %s", "owner_reply": "you replied by email",
           "oa_invite": "assessment invitation by email", "offer": "offer by email", "rejection": "rejection by email",
           "reply": " — needs your reply", "due": ", due %s", "answered": "recruiter mail marked answered"},
    "zh": {"recruiter_reply": "招聘方来信", "interview_invite": "邮件里的面试邀请",
           "interview_scheduled": "邮件里约定了面试时间：%s", "owner_reply": "你回复了邮件",
           "oa_invite": "邮件里的测评邀请", "offer": "邮件里的 offer", "rejection": "拒信", "reply": "，等你回复",
           "due": "，%s 截止", "answered": "招聘方来信已处理"},
}
GMAIL_ID = re.compile(r"^[0-9a-f]{12,20}$")
GMAIL_READ = ("mcp__claude_ai_Gmail__search_threads", "mcp__claude_ai_Gmail__get_thread",
              "mcp__claude_ai_Gmail__get_message", "mcp__claude_ai_Gmail__list_labels")
GMAIL_WRITE = tuple("mcp__claude_ai_Gmail__" + t for t in (
    "send_message", "create_draft", "update_draft", "delete_draft", "reply", "forward", "trash_message", "trash_thread",
    "untrash_message", "untrash_thread", "label_message", "label_thread", "unlabel_message", "unlabel_thread",
    "update_message_labels", "create_label", "update_label", "delete_label", "mark_message_spam", "mark_thread_spam",
    "unmark_message_spam", "unmark_thread_spam", "apply_sensitive_message_label", "apply_sensitive_thread_label"))


def results_schema():
    interview = {"type": ["object", "null"], "additionalProperties": False,
                 "required": ["time", "minutes", "time_snippet", "rescheduled"],
                 "properties": {"time": S.string(10, 40), "minutes": {"type": ["integer", "null"], "minimum": 5,
                                                                      "maximum": 600},
                                "time_snippet": S.string(6, 300), "rescheduled": {"type": "boolean"}}}
    msg = S.obj({
        "message_id": S.string(1, 200), "thread_id": S.string(0, 200),
        "received_at": S.string(10, 40), "sender": S.string(0, 320), "subject": S.string(0, 500),
        "snippet": S.string(12, 300), "category": {"type": "string", "enum": list(CATEGORIES)},
        "app_id": {"type": ["string", "null"], "maxLength": 12}, "company": S.string(0, 200),
        "deadline": {"type": ["string", "null"], "maxLength": 40},
        "action_required": {"type": "boolean"}, "from_me": {"type": "boolean"},
        "summary": {"type": ["string", "null"], "maxLength": 300},
        "next_step": {"type": ["string", "null"], "enum": list(NEXT_STEPS) + [None]},
        "interview": interview,
    })
    return S.obj({"messages": S.array(msg, 500), "covered_until": S.string(10, 40), "complete": {"type": "boolean"},
                  "problems": S.array(S.string(0, 400), 20)})


# ---------------------------------------------------------------- state

def _dir(ws):
    ws.inbox.mkdir(parents=True, exist_ok=True)
    return ws.inbox


def _state(ws):
    return util.read_json(_dir(ws) / "state.json", {}) or {}


def _seen(ws):
    return util.read_json(_dir(ws) / "seen.json", {}) or {}


def status(ws):
    st = _state(ws)
    return {"last_scan": st.get("last_scan"), "covered_until": st.get("covered_until"),
            "messages_recorded": int(st.get("messages_recorded") or 0), "unmatched": len(unmatched(ws)),
            "waiting": len(waiting(ws))}


def _done(ws):
    return util.read_json(_dir(ws) / "mail-done.json", {}) or {}


def gmail_link(message_id):
    """The Gmail web link of a message (Gmail API ids are 12-20 hex digits), or None."""
    mid = str(message_id or "")
    return "https://mail.google.com/mail/u/0/#all/%s" % mid if GMAIL_ID.match(mid) else None


def unmatched(ws):
    """Job emails that matched no application and were not linked or dismissed since."""
    seen = _seen(ws)
    out, ids = [], set()
    for rec in util.jsonl_read(_dir(ws) / "unmatched.jsonl"):
        mid = rec.get("message_id")
        if mid in ids or (seen.get(mid) or {}).get("outcome") != "unmatched":
            continue
        ids.add(mid)
        out.append({k: rec.get(k) for k in ("message_id", "received_at", "sender", "subject", "category", "company",
                                            "snippet", "deadline", "interview", "summary")})
    return out


# ---------------------------------------------------------------- brief

def window(ws):
    st = _state(ws)
    now = ws.now()
    covered = util.parse_time(st.get("covered_until"))
    lookback = float((ws.cfg.get("inbox") or {}).get("lookback_days") or 14)
    after = (covered - timedelta(days=1)) if covered else now - timedelta(days=lookback)
    after = max(after, now - timedelta(days=max(lookback, 60)))
    before = now + timedelta(days=1)
    return after, before


def _host(url):
    try:
        return (urlsplit(url or "").hostname or "").lower()
    except ValueError:
        return ""


def _apps(ws):
    out = []
    for st in ledger.states(ws).values():
        if st["voided"]:
            continue
        recent = st.get("last_date") and (util.days_between(st["last_date"], ws.now()) or 0) <= 60
        if st["closed"] and not recent:
            continue
        out.append({"id": st["id"], "company": st["company"], "role": st["role"], "stage": st["stage"],
                    "closed": st["closed"], "applied_date": st.get("applied_date"), "url_host": _host(st.get("url"))})
    return sorted(out, key=lambda a: a["id"])


def queries(ws, after, before, apps):
    # sent mail included: your replies are part of the conversation
    span = "after:%d before:%d -in:spam -in:trash" % (int(after.timestamp()), int(before.timestamp()))
    label = ((ws.cfg.get("inbox") or {}).get("label") or "").strip()
    out = ["%s (from:(%s) OR subject:(%s))" % (span, " OR ".join(ATS_DOMAINS), " OR ".join(SUBJECT_WORDS))]
    names = sorted({a["company"] for a in apps if a.get("company")})
    for i in range(0, len(names), 8):
        chunk = " OR ".join('"%s"' % n.replace('"', "") for n in names[i:i + 8])
        out.append("%s (%s)" % (span, chunk))
    if label:
        out.append('%s label:"%s"' % (span, label.replace('"', "")))
    return out


def brief(ws):
    after, before = window(ws)
    apps = _apps(ws)
    seen = _seen(ws)
    known = sorted(seen, key=lambda m: (seen[m] or {}).get("at") or "")[-2000:]
    return {
        "window": {"after": after.isoformat(timespec="seconds"), "after_date": after.date().isoformat(),
                   "after_epoch": int(after.timestamp()), "before": before.isoformat(timespec="seconds"),
                   "before_epoch": int(before.timestamp())},
        "apps": apps,
        "known_message_ids": known,
        "my_addresses": _my_addresses(ws),
        "summary_language": "Simplified Chinese" if config.language(ws.cfg) == "zh" else "English",
        "timezone": str(ws.tz),
        "categories": CATEGORIES,
        "next_steps": list(NEXT_STEPS),
        "queries": queries(ws, after, before, apps),
        "output_schema": results_schema(),
        "instructions": ("Read-only. Run the queries with the Gmail tools and skip known message ids. Read every job "
                         "thread with a new message in full (get_thread), the candidate's own replies included, and "
                         "report each new message, the candidate's own too (from_me): its category, a 12-300 "
                         "character snippet copied verbatim, a one- or two-sentence summary in summary_language "
                         "(what it says or asks, what is agreed, what to do next; no links, addresses, numbers or "
                         "money) and next_step. An interview time both sides agreed is interview_scheduled with "
                         "`interview` (the start in the time zone the message states, the verbatim sentence that "
                         "states it). Match app_id from `apps` (null when unsure; then give the company). Write the "
                         "results as JSON matching output_schema to a file and run `jobpilot inbox apply <file>`. "
                         "Never label, archive, send, draft or delete mail; never copy verification codes."),
    }


# ---------------------------------------------------------------- checks

def _my_addresses(ws):
    return sorted({str(a).strip().lower() for a in (ws.cfg.get("inbox") or {}).get("my_addresses") or []
                   if str(a).strip()})


def _address(sender):
    m = re.search(r"[\w.+'-]+@[\w-]+(?:\.[\w-]+)+", sender or "")
    return m.group(0).lower() if m else ""


def _local(ws, value):
    """An aware datetime; a time written without an offset is read in the workspace's time zone."""
    text = str(value or "").strip()
    t = util.parse_time(text)
    if t is None:
        return None
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?)?", text):
        t = t.replace(tzinfo=None).replace(tzinfo=ws.tz)
    return t


WEEKDAYS = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3, "friday": 4, "saturday": 5, "sunday": 6,
            "tues": 1, "weds": 2, "thur": 3, "thurs": 3, "mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5,
            "sun": 6}
# full names in any case; three-letter forms only capitalized ("we sat down" names no Saturday)
WEEKDAY_WORD = re.compile(r"(?i:\b(monday|tuesday|wednesday|thursday|friday|saturday|sunday|tues|weds|thurs?)\b)|"
                          r"\b(Mon|Tue|Wed|Thu|Fri|Sat|Sun)\b")
WEEKDAY_ZH = re.compile(r"(?:周|星期|礼拜)([一二三四五六日天])")
MONTH_WORD = r"(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?"
DATES = (re.compile(r"(?i)\b" + MONTH_WORD + r"\s+(\d{1,2})(?:st|nd|rd|th)?\b"),          # October 21
         re.compile(r"(?i)\b(\d{1,2})(?:st|nd|rd|th)?\s+(?:of\s+)?" + MONTH_WORD),          # 21 October
         re.compile(r"\b(\d{1,2})/(\d{1,2})(?:/\d{2,4})?\b"),                                  # 10/21
         re.compile(r"\b\d{4}-(\d{2})-(\d{2})\b"),                                            # 2026-10-21
         re.compile(r"(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]"))                                     # 10月21日
TIME_12 = re.compile(r"(?i)\b(1[0-2]|0?[1-9])(?:[:.]([0-5]\d))?\s*([ap])\.?\s?m\b\.?")
TIME_24 = re.compile(r"\b([01]?\d|2[0-3])[:h]([0-5]\d)\b")
TIME_ZH = re.compile(r"(上午|中午|下午|晚上)?\s*(\d{1,2})\s*[点點:：]\s*(半|\d{1,2})?")
MONTHS = {m: i for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov",
                                      "dec"), 1)}


def stated_times(text):
    """{(hour, minute)} a sentence states: 2pm, 2:30 p.m., 14:00, noon, 下午2点半."""
    out, rest = set(), text or ""
    for m in TIME_12.finditer(rest):
        out.add((int(m.group(1)) % 12 + (12 if m.group(3).lower() == "p" else 0), int(m.group(2) or 0)))
    rest = TIME_12.sub(" ", rest)
    out |= {(int(m.group(1)), int(m.group(2))) for m in TIME_24.finditer(rest)}
    for m in TIME_ZH.finditer(rest):
        h = int(m.group(2))
        if m.group(1) in ("下午", "晚上") and h < 12:
            h += 12
        if h <= 23:
            out.add((h, 30 if m.group(3) == "半" else int(m.group(3) or 0)))
    if re.search(r"(?i)\bnoon\b|\bmidday\b|中午12", rest):
        out.add((12, 0))
    return out


def stated_days(text):
    """([(month, day)], [weekday 0-6]) a sentence names."""
    dates = []
    for i, rx in enumerate(DATES):
        for m in rx.finditer(text or ""):
            if i == 0:
                dates.append((MONTHS[m.group(1).lower()[:3]], int(m.group(2))))
            elif i == 1:
                dates.append((MONTHS[m.group(2).lower()[:3]], int(m.group(1))))
            else:
                dates.append((int(m.group(1)), int(m.group(2))))
    days = [WEEKDAYS[(m.group(1) or m.group(2)).lower()] for m in WEEKDAY_WORD.finditer(text or "")]
    days += ["一二三四五六".index(m.group(1)) if m.group(1) in "一二三四五六" else 6
             for m in WEEKDAY_ZH.finditer(text or "")]
    return dates, days


def agreed_time(ws, m):
    """({time, minutes, rescheduled}, None) for an interview_scheduled message, or (None, why) when the time it claims
    is not stated in its quoted sentence or lies outside [a day before the email, 180 days after]."""
    iv = m.get("interview") if isinstance(m.get("interview"), dict) else {}
    when = _local(ws, iv.get("time"))
    if when is None or len(str(iv.get("time"))) < 16:
        return None, "no interview time"
    quote = iv.get("time_snippet") or ""
    if (when.hour, when.minute) not in stated_times(quote):
        return None, "the quoted sentence does not state %s" % when.strftime("%H:%M")
    dates, days = stated_days(quote)
    if dates and (when.month, when.day) not in dates:
        return None, "the quoted sentence names another date"
    if days and when.weekday() not in days:
        return None, "the quoted sentence names another weekday"
    received = util.parse_time(m.get("received_at")) or ws.now()
    if not received - timedelta(days=1) <= when <= received + timedelta(days=180):
        return None, "interview time out of range"
    minutes = iv.get("minutes")
    return {"time": when.isoformat(timespec="minutes"),
            "minutes": minutes if isinstance(minutes, int) and 5 <= minutes <= 600 else None,
            "rescheduled": bool(iv.get("rescheduled"))}, None


def clean_summary(text):
    s = " ".join(str(text or "").split())[:200]
    return s if s and not SUMMARY_BAD.search(s) else None


def check(ws, m):
    """(message, problem): the code's own rules on one reported message (who sent it, an agreed time, the summary).
    problem is set when the message must not be recorded at all."""
    m = dict(m)
    mine = _my_addresses(ws)
    m["from_me"] = _address(m.get("sender")) in mine if mine else bool(m.get("from_me"))
    if m["category"] == "owner_reply" and not m["from_me"]:
        return m, "message %s: owner_reply from someone else" % m["message_id"]
    if m["from_me"]:
        m["action_required"] = False
        if m["category"] not in OWN:
            m["category"] = "owner_reply"
    m["interview"] = None if m["category"] != "interview_scheduled" else m.get("interview")
    if m["category"] == "interview_scheduled":
        iv, why = agreed_time(ws, m)
        if iv is None:      # no proven time: still an invitation (or your reply)
            m["interview_dropped"] = why
            m["category"] = "owner_reply" if m["from_me"] else "interview_invite"
        m["interview"] = iv
    m["summary"] = None if m["category"] == "other" else clean_summary(m.get("summary"))
    m["next_step"] = m.get("next_step") if m.get("next_step") in NEXT_STEPS else None
    return m, None


# ---------------------------------------------------------------- apply

def _day(ws, stamp):
    t = util.parse_time(stamp)
    return t.astimezone(ws.tz).date().isoformat() if t else ws.today()


def _match(ws, msg, states):
    """(application state or None, how). An explicit app_id must exist; otherwise the company's only application."""
    app_id = (msg.get("app_id") or "").strip().upper()
    if app_id:
        st = states.get(app_id)
        return (st, "app_id") if st and not st["voided"] else (None, "unknown application %s" % app_id)
    key = util.company_key(msg.get("company"))
    if not key:
        return None, "no application or company named"
    cands = [s for s in states.values() if not s["voided"] and util.company_key(s.get("company")) == key]
    open_cands = [s for s in cands if not s["closed"]]
    if len(open_cands) == 1:
        return open_cands[0], "company"
    if not open_cands and len(cands) == 1:
        return cands[0], "company"
    return None, "%d applications at %s" % (len(cands), msg.get("company")) if cands else "no application at %s" % (
        msg.get("company"))


def plan(st, msg):
    """[(event, fields)] for one message on one application, plus a note for the user when progress arrives on a
    closed application (never recorded as progress: the user decides whether to reopen it)."""
    cat = msg["category"]
    base = {"source": "gmail", "message_id": msg["message_id"], "note": util.clip(msg.get("subject") or "", 120),
            "by": "inbox"}
    if cat == "owner_reply":
        return [("note", dict(base, note=util.clip("you replied: %s" % (msg.get("subject") or ""), 120)))], None
    if st["closed"]:
        if cat == "rejection" and st["stage"] == "closed":
            return [("rejected", base)], None   # "no response" becomes the real outcome
        if cat == "rejection":
            return [], None                     # already rejected or withdrawn
        return [("note", dict(base, note=util.clip("Gmail (%s) after the application was closed: %s" % (
            cat, msg.get("subject") or msg.get("snippet")), 200)))], "%s %s arrived after %s was closed" % (
            st["id"], cat, st["company"])
    events = []
    if st["stage"] == "planned" and cat in APPLIED_PROOF + ("interview_scheduled",):
        events.append(("applied", dict(base, note="inferred from an email (%s)" % cat)))
    iv = msg.get("interview") if cat == "interview_scheduled" else None
    if iv is None and msg.get("interview_time") and cat == "interview_invite":   # results written before 0.2
        iv = {"time": msg["interview_time"], "minutes": None, "rescheduled": False}
    if cat == "interview_scheduled" and st["stage"] in ("planned", "applied", "oa"):
        events.append(("interview_invited", dict(base)))      # the agreement is also the invitation
    if cat != "interview_scheduled":
        fields = dict(base)
        if cat == "oa_invite" and msg.get("deadline"):
            fields["deadline"] = msg["deadline"]
        events.append((EVENT_OF[cat], fields))
    if iv:
        fields = dict(base, time=iv["time"], note=util.clip("time agreed by email%s: %s" % (
            " (rescheduled)" if iv.get("rescheduled") else "", msg.get("subject") or ""), 120))
        if iv.get("minutes"):
            fields["minutes"] = iv["minutes"]
        if iv.get("rescheduled"):
            fields["rescheduled"] = True
        events.append(("interview_scheduled", fields))
    return events, None


def apply(ws, results):
    """Record checked messages; returns a summary. Invalid results raise and record nothing."""
    if isinstance(results, str):
        results = json.loads(results)
    problems = S.validate(results, results_schema())
    if problems:
        raise util.JobPilotError("inbox results do not match the schema: " + "; ".join(problems[:5]))
    for m in results["messages"]:
        if m.get("deadline") and not util.parse_time(m["deadline"]):
            raise util.JobPilotError("message %s: deadline %r is not a date or ISO time" % (m["message_id"],
                                                                                         m["deadline"]))
        if not util.parse_time(m["received_at"]):
            raise util.JobPilotError("message %s: received_at %r is not an ISO time" % (m["message_id"], m["received_at"]))
    if not util.parse_time(results["covered_until"]):
        raise util.JobPilotError("covered_until %r is not an ISO time" % results["covered_until"])
    summary = {"messages": len(results["messages"]), "recorded": 0, "events": [], "ignored": 0, "duplicates": 0,
               "unmatched": 0, "rejected": 0, "attention": [], "reply_needed": [], "agreed": [], "your_replies": [],
               "problems": list(results.get("problems") or [])}
    news = []
    with ws.lock("inbox"):
        seen = _seen(ws)
        now = util.stamp(ws.now())
        for m in sorted(results["messages"], key=lambda m: util.parse_time(m["received_at"])):
            mid = m["message_id"]
            if mid in seen:
                summary["duplicates"] += 1
                continue
            m, problem = check(ws, m)
            if problem:
                summary["rejected"] += 1
                summary["problems"].append(problem)
                continue
            if m.get("interview_dropped"):
                summary["problems"].append("message %s: not an agreed time (%s)" % (mid, m["interview_dropped"]))
            if m["category"] == "other":
                seen[mid] = {"at": now, "outcome": "ignored", "category": "other"}
                summary["ignored"] += 1
                continue
            states = ledger.states(ws)
            st, how = _match(ws, m, states)
            if st is None:
                util.jsonl_append(_dir(ws) / "unmatched.jsonl", dict(
                    {k: m.get(k) for k in ("message_id", "thread_id", "received_at", "sender", "subject", "category",
                                           "company", "snippet", "deadline", "interview", "summary", "from_me")},
                    reason=how))
                seen[mid] = {"at": now, "outcome": "unmatched", "category": m["category"],
                             "received_at": m["received_at"], "summary": m["summary"], "from_me": m["from_me"]}
                summary["unmatched"] += 1
                continue
            events, attention = plan(st, m)
            for event, fields in events:
                ledger.record(ws, st["id"], event, date=_day(ws, m["received_at"]), **fields)
                summary["events"].append({"app_id": st["id"], "event": event})
            if attention:
                summary["attention"].append(attention)
            seen[mid] = {"at": now, "outcome": "recorded" if events else "duplicate_outcome", "app_id": st["id"],
                         "category": m["category"], "received_at": m["received_at"], "thread_id": m.get("thread_id"),
                         "action_required": bool(m.get("action_required")), "from_me": m["from_me"],
                         "summary": m["summary"], "next_step": m["next_step"], "interview": m["interview"]}
            if m.get("interview_dropped"):
                seen[mid]["interview_dropped"] = m["interview_dropped"]
            summary["recorded"] += bool(events)
            if events and m["category"] in NEWS and not attention:     # not the note on a closed application
                news.append((st, m))
        util.atomic_write_json(_dir(ws) / "seen.json", seen, mode=0o600)
        st = _state(ws)
        covered = max(filter(None, [util.parse_time(st.get("covered_until")), util.parse_time(results["covered_until"])]))
        st.update(last_scan=now, covered_until=covered.isoformat(timespec="seconds"),
                  messages_recorded=int(st.get("messages_recorded") or 0) + summary["recorded"],
                  last_complete=bool(results.get("complete")))
        util.atomic_write_json(_dir(ws) / "state.json", st, mode=0o600)
    for st, m in news:
        _news(ws, st, m)
        row = {"app_id": st["id"], "company": st["company"], "subject": m.get("subject"), "summary": m["summary"]}
        if m["category"] == "recruiter_reply" and m.get("action_required"):
            summary["reply_needed"].append(row)
        elif m["category"] == "interview_scheduled":
            summary["agreed"].append(dict(row, time=m["interview"]["time"]))
        elif m["category"] == "owner_reply":
            summary["your_replies"].append(row)
    if summary["recorded"] or summary["unmatched"]:
        ws.event("inbox", "inbox: %d update%s recorded, %d email%s unmatched" % (
            summary["recorded"], "" if summary["recorded"] == 1 else "s", summary["unmatched"],
            "" if summary["unmatched"] == 1 else "s"), recorded=summary["recorded"], unmatched=summary["unmatched"])
    return summary


def _news(ws, st, m):
    """One activity-feed line for a notable email (the dashboard links it to Gmail)."""
    t = NEWS_TEXT[config.language(ws.cfg)]
    cat = m["category"]
    ask = cat == "recruiter_reply" and bool(m.get("action_required"))
    if cat == "interview_scheduled":
        when = _local(ws, m["interview"]["time"]).astimezone(ws.tz)
        text = "%s %s: %s" % (st["id"], st["company"], t[cat] % when.strftime("%Y-%m-%d %H:%M"))
    else:
        text = "%s %s: %s" % (st["id"], st["company"], t[cat])
    if ask:
        text += t["reply"]
    if cat == "oa_invite" and m.get("deadline"):
        text += t["due"] % str(m["deadline"])[:10]
    ws.event("inbox", text, app_id=st["id"], message_id=m["message_id"], thread_id=m.get("thread_id") or None,
             category=cat, needs_reply=ask, gist=m.get("summary"), next_step=m.get("next_step"),
             minor=cat == "owner_reply")


# ---------------------------------------------------------------- recruiter mail waiting for an answer

def _waiting_mails(ws, days):
    """{app id: [(ledger event, seen entry)]} of recruiter mails that ask the candidate to act, oldest first, with no
    later stage on the application and not marked answered."""
    seen, done = _seen(ws), _done(ws)
    cutoff = ws.now() - timedelta(days=days)
    out = {}
    for e in ledger.events(ws):
        app = e.get("app_id")
        mid = e.get("message_id")
        info = seen.get(mid) or {}
        if e.get("event") in SETTLED or (e.get("source") == "gmail" and info.get("category") == "owner_reply"):
            out.pop(app, None)      # a later stage, or you answered by email
            continue
        if e.get("event") != "recruiter_reply" or e.get("source") != "gmail":
            continue
        if not mid or mid in done or not info.get("action_required"):
            continue
        when = util.parse_time(info.get("received_at") or e.get("ts"))
        if when and when < cutoff:
            continue
        out.setdefault(app, []).append((e, info))
    return out


def waiting(ws, days=30):
    """Recruiter mail that asks you to act (a question, a request) and is still unanswered, newest first: one row per
    application with its newest such mail. A mail leaves the list when the application moves on (an assessment, an
    interview, an offer, its end), when you mark it answered, or after `days`."""
    states = ledger.states(ws)
    out = []
    for app, mails in _waiting_mails(ws, days).items():
        st = states.get(app)
        if not st or st["voided"] or st["closed"]:
            continue
        e, info = mails[-1]
        out.append({"app_id": app, "company": st["company"], "role": st["role"], "stage": st["stage"],
                    "track": st.get("track"), "message_id": e.get("message_id"), "thread_id": info.get("thread_id"),
                    "received_at": info.get("received_at") or e.get("ts"), "subject": e.get("note") or "",
                    "gist": info.get("summary"), "next_step": info.get("next_step"), "count": len(mails),
                    "can_invite": st["stage"] in ("planned", "applied", "oa"), "mail": gmail_link(e.get("message_id"))})
    out.sort(key=lambda r: str(r["received_at"] or ""), reverse=True)
    return out


STAGE_EVENTS = ("oa_invited", "oa_done", "interview_invited", "interview_scheduled", "interview_done", "offer")


def mail_state(ws, app_id, seen=None):
    """{"gist", "replied_at"}: the summary of the newest email from the employer since the application's newest stage,
    and when you last answered by email since then (an older email's advice is stale once the stage moved on)."""
    seen = _seen(ws) if seen is None else seen
    gist = replied = None
    for e in ledger.events(ws, app_id):
        if e.get("event") in STAGE_EVENTS:
            gist = replied = None
        if e.get("source") != "gmail" or not e.get("message_id"):
            continue
        info = seen.get(e["message_id"]) or {}
        if info.get("category") == "owner_reply":
            replied = info.get("received_at") or e.get("ts")
        elif not info.get("from_me"):
            gist = info.get("summary") or gist
    return {"gist": gist, "replied_at": replied}


def summaries(ws):
    """{message id: summary} of every recorded email that has one."""
    return {mid: v["summary"] for mid, v in _seen(ws).items() if isinstance(v, dict) and v.get("summary")}


def answered(ws, app_id, by="user"):
    """You answered the recruiter (or there is nothing to answer): the application's mails stop waiting."""
    app_id = str(app_id or "").upper()
    mails = _waiting_mails(ws, 3650).get(app_id) or []
    if not mails:
        return {"app_id": app_id, "answered": 0}
    with ws.lock("inbox"):
        done = _done(ws)
        done.update({e["message_id"]: util.stamp(ws.now()) for e, _ in mails})
        util.atomic_write_json(_dir(ws) / "mail-done.json", done, mode=0o600)
    st = ledger.state(ws, app_id) or {}
    ws.event("inbox", "%s %s: %s" % (app_id, st.get("company") or "", NEWS_TEXT[config.language(ws.cfg)]["answered"]),
             app_id=app_id, by=by)
    return {"app_id": app_id, "answered": len(mails)}


def invite(ws, app_id, by="user"):
    """Record a waiting recruiter mail (a call or an interview the classifier filed as a plain reply) as an interview
    invitation on its application, with the email as the source."""
    app_id = str(app_id or "").upper()
    st = ledger.state(ws, app_id)
    if not st or st["voided"]:
        raise util.JobPilotError("no application %s" % app_id)
    mails = _waiting_mails(ws, 3650).get(app_id) or []
    if not mails:
        raise util.JobPilotError("%s has no recruiter mail waiting for an answer" % app_id)
    if st["closed"]:
        raise util.JobPilotError("%s is closed; reopen it first" % app_id)
    if st["stage"] not in ("planned", "applied", "oa"):
        return {"app_id": app_id, "events": []}
    e, info = mails[-1]
    base = {"source": "gmail", "message_id": e["message_id"], "by": by,
            "date": _day(ws, info.get("received_at") or e.get("ts"))}
    events = []
    if st["stage"] == "planned":
        ledger.record(ws, app_id, "applied", note="inferred from an email (interview_invite)", **base)
        events.append("applied")
    ledger.record(ws, app_id, "interview_invited", note="recorded from a recruiter's email", **base)
    events.append("interview_invited")
    ws.event("app", "%s interview_invited: %s — %s" % (app_id, st["company"], st["role"]), app_id=app_id)
    return {"app_id": app_id, "events": events}


def link(ws, message_id, app_id):
    """Record an unmatched email against an application."""
    rec = next((r for r in reversed(util.jsonl_read(_dir(ws) / "unmatched.jsonl")) if r.get("message_id") == message_id),
               None)
    if rec is None:
        raise util.JobPilotError("no unmatched email %s" % message_id)
    st = ledger.state(ws, app_id.upper())
    if not st or st["voided"]:
        raise util.JobPilotError("no application %s" % app_id)
    with ws.lock("inbox"):
        seen = _seen(ws)
        if (seen.get(message_id) or {}).get("outcome") != "unmatched":
            raise util.JobPilotError("email %s was already handled" % message_id)
        msg = dict(rec, app_id=st["id"])
        events, attention = plan(st, msg)
        for event, fields in events:
            ledger.record(ws, st["id"], event, date=_day(ws, rec.get("received_at")), **fields)
        seen[message_id] = dict(seen[message_id], at=util.stamp(ws.now()), outcome="linked", app_id=st["id"],
                                category=rec.get("category"), thread_id=rec.get("thread_id"),
                                interview=rec.get("interview"))
        util.atomic_write_json(_dir(ws) / "seen.json", seen, mode=0o600)
    ws.event("inbox", "linked an email to %s (%s)" % (st["id"], rec.get("category")), app_id=st["id"])
    return {"app_id": st["id"], "events": [e for e, _ in events], "attention": attention}


def dismiss(ws, message_id):
    with ws.lock("inbox"):
        seen = _seen(ws)
        if (seen.get(message_id) or {}).get("outcome") != "unmatched":
            raise util.JobPilotError("no unmatched email %s" % message_id)
        seen[message_id]["outcome"] = "dismissed"
        util.atomic_write_json(_dir(ws) / "seen.json", seen, mode=0o600)
    return {"message_id": message_id, "outcome": "dismissed"}


# ---------------------------------------------------------------- headless scan (experimental)

def scan_headless(ws, model="sonnet", timeout=900, log=print):
    """One unattended `claude -p` run that reads Gmail through the claude.ai connector (it must be connected in
    Claude Code) and returns the results this module applies. Write tools are denied explicitly as well."""
    binary = claude.binary()
    if not binary:
        raise util.JobPilotError("claude CLI not found")
    b = brief(ws)
    context = {k: b[k] for k in ("window", "apps", "known_message_ids", "my_addresses", "summary_language", "timezone",
                                 "categories", "next_steps", "queries")}
    task = ("Triage the candidate's Gmail for job-application updates in the window below and answer with the "
            "structured output.\n\n<brief>\n%s\n</brief>" % json.dumps(context, ensure_ascii=False, indent=1))
    schema = results_schema()
    system = (PROMPTS / "inbox-triage.md").read_text(encoding="utf-8")
    cmd = [binary, "-p", task, "--output-format", "json", "--json-schema", json.dumps(schema, separators=(",", ":")),
           "--model", model, "--max-turns", "40", "--permission-mode", "dontAsk", "--no-session-persistence",
           "--append-system-prompt", system, "--setting-sources", "user", "--tools", "ToolSearch",
           "--allowedTools", *GMAIL_READ, "--disallowedTools", *GMAIL_WRITE]
    t0 = time.monotonic()
    res = claude.Result(ok=False)
    workdir = tempfile.mkdtemp(prefix="jobpilot-inbox-")
    try:
        proc = subprocess.Popen(cmd, cwd=workdir, env=claude._env(), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
                                start_new_session=True)
        try:
            out, err = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            claude._kill(proc)
            proc.communicate()
            res.error = "timeout after %ds" % timeout
            claude._log(ws, "inbox-triage", model, res, t0)
            raise util.JobPilotError("the Gmail scan timed out; try again or use /jobpilot:inbox") from None
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    data = claude._parse(out) or {}
    res.cost_usd, res.usage = data.get("total_cost_usd"), data.get("usage") or {}
    output = data.get("structured_output")
    if proc.returncode != 0 or data.get("is_error") or output is None:
        res.error = "scan failed: %s" % util.clip(str(data.get("result") or (err or "").strip()[-300:]), 300)
        claude._log(ws, "inbox-triage", model, res, t0)
        raise util.JobPilotError(res.error + " (is the Gmail connector enabled in Claude Code?)")
    res.ok, res.output = True, output
    claude._log(ws, "inbox-triage", model, res, t0)
    summary = apply(ws, output)
    log("inbox: %(recorded)d recorded, %(unmatched)d unmatched, %(duplicates)d already known" % summary)
    return summary


# ---------------------------------------------------------------- CLI

def cmd_inbox(ws, args):
    from .cli import out
    a = args.inbox_action
    if a == "brief":
        return out(args, brief(ws))
    if a == "apply":
        text = Path(args.file).read_text(encoding="utf-8") if args.file != "-" else sys.stdin.read()
        res = apply(ws, json.loads(text))
        return out(args, res, "%(recorded)d emails recorded, %(unmatched)d unmatched, %(duplicates)d already known, "
                              "%(ignored)d not about applications" % res +
                   "".join("\n! " + x for x in res["attention"]) +
                   "".join("\n> %(app_id)s %(company)s: a recruiter wrote and waits for your reply" % x
                           for x in res["reply_needed"]) +
                   "".join("\n> %(app_id)s %(company)s: interview time agreed, %(time)s" % x for x in res["agreed"]) +
                   "".join("\n  %(app_id)s %(company)s: your reply recorded" % x for x in res["your_replies"]))
    if a == "link":
        res = link(ws, args.message_id, args.app_id)
        return out(args, res, "%s: %s" % (res["app_id"], ", ".join(res["events"]) or "nothing new"))
    if a == "dismiss":
        return out(args, dismiss(ws, args.message_id), "dismissed")
    if a == "waiting":
        rows = waiting(ws)
        return out(args, rows, "\n".join("%s %-24s %s  %s" % (r["app_id"], util.clip(r["company"], 24),
                                                              str(r["received_at"])[:10], util.clip(r["subject"], 60))
                                         for r in rows) or "no recruiter mail waiting for an answer")
    if a == "answered":
        res = answered(ws, args.app_id)
        return out(args, res, "%s: %d email(s) marked answered" % (res["app_id"], res["answered"]))
    if a == "invite":
        res = invite(ws, args.app_id)
        return out(args, res, "%s: %s" % (res["app_id"], ", ".join(res["events"]) or "already past the invitation"))
    if a == "status":
        st = status(ws)
        return out(args, st, "last scan %s, covered until %s, %d recorded, %d unmatched" % (
            st["last_scan"] or "never", st["covered_until"] or "-", st["messages_recorded"], st["unmatched"]))
    if a == "unmatched":
        rows = unmatched(ws)
        return out(args, rows, "\n".join("%s %-16s %-24s %s" % (r["message_id"], r["category"], util.clip(r["company"], 24),
                                                               util.clip(r["subject"], 50)) for r in rows) or "none")
    if a == "scan":
        res = scan_headless(ws, model=args.model, log=(lambda *_: None) if args.json else print)
        return out(args, res, None)
    raise AssertionError(a)


def register(sub, add):
    sp = add("inbox", cmd_inbox, help="Gmail progress tracking: brief, apply, link, unmatched, waiting, status, scan")
    isub = sp.add_subparsers(dest="inbox_action", required=True)

    def q(name, **kw):
        p = isub.add_parser(name, **kw)
        p.add_argument("--json", action="store_true", help="machine-readable output")
        return p

    q("brief", help="window, open applications and queries for the /jobpilot:inbox skill")
    p = q("apply", help="record classified emails from a results JSON file ('-' = stdin)")
    p.add_argument("file")
    p = q("link", help="record an unmatched email against an application")
    p.add_argument("message_id")
    p.add_argument("app_id")
    p = q("dismiss", help="drop an unmatched email from the list")
    p.add_argument("message_id")
    q("unmatched", help="job emails that matched no application")
    q("waiting", help="recruiter mail that asks you to act and is still unanswered")
    p = q("answered", help="you answered an application's recruiter mail: stop reminding")
    p.add_argument("app_id")
    p = q("invite", help="record an application's waiting recruiter mail as an interview invitation")
    p.add_argument("app_id")
    q("status", help="last scan and coverage")
    p = q("scan", help="experimental: scan Gmail headless through the claude.ai Gmail connector")
    p.add_argument("--model", default="sonnet")
