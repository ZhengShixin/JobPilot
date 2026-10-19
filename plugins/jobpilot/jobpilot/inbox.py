"""Gmail progress tracking: application emails (receipts, rejections, assessments, interviews, offers, recruiter
replies) become ledger events, so the pipeline keeps itself up to date.

Read-only by design: nothing here labels, archives, sends, drafts or deletes mail.

    brief(ws)            what the /jobpilot:inbox skill needs: the time window, open applications, message ids
                         already processed, ready-made Gmail queries and the results schema
    apply(ws, results)   the skill's classified messages -> checked -> ledger events (source "gmail", message id)
    link(ws, msg, app)   an unmatched email recorded against an application by hand
    scan_headless(ws)    experimental: the same triage as one `claude -p` run with the claude.ai Gmail connector

The session reads the mail with your own Gmail connector; the code then refuses anything it cannot check: an unknown
category, a duplicate message, an application that does not exist, or progress on an application already closed
(recorded as a note instead, so you can reopen it yourself).
"""
import json
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlsplit

from . import claude, ledger, schema as S, util

PROMPTS = Path(__file__).resolve().parent / "prompts"
CATEGORIES = {
    "receipt": "the employer confirms it received the application",
    "rejection": "the employer declines or closes the candidacy",
    "oa_invite": "an online assessment, coding test, recorded video interview or take-home task",
    "interview_invite": "an invitation to interview or to give availability for one",
    "offer": "an offer of employment",
    "recruiter_reply": "a person writes personally about the candidacy",
    "other": "job alerts, newsletters, marketing, verification codes, everything else",
}
# Emails that prove an application was sent (a planned application then counts as applied on that date).
APPLIED_PROOF = ("receipt", "rejection", "oa_invite", "interview_invite", "offer")
EVENT_OF = {"receipt": "receipt", "rejection": "rejected", "oa_invite": "oa_invited",
            "interview_invite": "interview_invited", "offer": "offer", "recruiter_reply": "recruiter_reply"}
ATS_DOMAINS = ("greenhouse.io", "greenhouse-mail.io", "lever.co", "myworkday.com", "myworkdayjobs.com", "workday.com", "ashbyhq.com",
               "icims.com", "smartrecruiters.com", "jobvite.com", "successfactors.com", "taleo.net", "workable.com",
               "bamboohr.com", "oraclecloud.com", "eightfold.ai", "avature.net", "rippling.com", "breezy.hr",
               "hirevue.com", "codesignal.com", "hackerrank.com", "karat.com", "codility.com", "modernhire.com",
               "pymetrics.ai", "testgorilla.com")
SUBJECT_WORDS = ("application", "applying", "interview", "assessment", "\"coding challenge\"", "\"next steps\"",
                 "candidacy", "offer", "\"your interest\"")
GMAIL_READ = ("mcp__claude_ai_Gmail__search_threads", "mcp__claude_ai_Gmail__get_thread",
              "mcp__claude_ai_Gmail__get_message", "mcp__claude_ai_Gmail__list_labels")
GMAIL_WRITE = tuple("mcp__claude_ai_Gmail__" + t for t in (
    "send_message", "create_draft", "update_draft", "delete_draft", "reply", "forward", "trash_message", "trash_thread",
    "untrash_message", "untrash_thread", "label_message", "label_thread", "unlabel_message", "unlabel_thread",
    "update_message_labels", "create_label", "update_label", "delete_label", "mark_message_spam", "mark_thread_spam",
    "unmark_message_spam", "unmark_thread_spam", "apply_sensitive_message_label", "apply_sensitive_thread_label"))


def results_schema():
    msg = S.obj({
        "message_id": S.string(1, 200), "thread_id": S.string(0, 200),
        "received_at": S.string(10, 40), "sender": S.string(0, 320), "subject": S.string(0, 500),
        "snippet": S.string(12, 300), "category": {"type": "string", "enum": list(CATEGORIES)},
        "app_id": {"type": ["string", "null"], "maxLength": 12}, "company": S.string(0, 200),
        "deadline": {"type": ["string", "null"], "maxLength": 40},
        "interview_time": {"type": ["string", "null"], "maxLength": 40},
        "action_required": {"type": "boolean"},
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
            "messages_recorded": int(st.get("messages_recorded") or 0), "unmatched": len(unmatched(ws))}


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
                                            "snippet", "deadline", "interview_time")})
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
    span = "after:%d before:%d -in:spam -in:trash -in:sent" % (int(after.timestamp()), int(before.timestamp()))
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
        "categories": CATEGORIES,
        "queries": queries(ws, after, before, apps),
        "output_schema": results_schema(),
        "instructions": ("Read-only. Run the queries with the Gmail tools, skip known message ids and mail the "
                         "candidate sent, read each possible application email and classify it into one category. "
                         "Copy a 12-300 character snippet verbatim. Match app_id from `apps` (null when unsure; then "
                         "give the company). Write the results as JSON matching output_schema to a file and run "
                         "`jobpilot inbox apply <file>`. Never label, archive, send, draft or delete mail; never copy "
                         "verification codes."),
    }


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
    if st["closed"]:
        if cat == "rejection" and st["stage"] == "closed":
            return [("rejected", base)], None   # "no response" becomes the real outcome
        if cat == "rejection":
            return [], None                     # already rejected or withdrawn
        return [("note", dict(base, note=util.clip("Gmail (%s) after the application was closed: %s" % (
            cat, msg.get("subject") or msg.get("snippet")), 200)))], "%s %s arrived after %s was closed" % (
            st["id"], cat, st["company"])
    events = []
    if st["stage"] == "planned" and cat in APPLIED_PROOF:
        events.append(("applied", dict(base, note="inferred from an email (%s)" % cat)))
    event = EVENT_OF[cat]
    fields = dict(base)
    if cat == "oa_invite" and msg.get("deadline"):
        fields["deadline"] = msg["deadline"]
    events.append((event, fields))
    if cat == "interview_invite" and msg.get("interview_time"):
        events.append(("interview_scheduled", dict(base, time=msg["interview_time"])))
    return events, None


def apply(ws, results):
    """Record checked messages; returns a summary. Invalid results raise and record nothing."""
    if isinstance(results, str):
        results = json.loads(results)
    problems = S.validate(results, results_schema())
    if problems:
        raise util.JobPilotError("inbox results do not match the schema: " + "; ".join(problems[:5]))
    for m in results["messages"]:
        for key in ("deadline", "interview_time"):
            if m.get(key) and not util.parse_time(m[key]):
                raise util.JobPilotError("message %s: %s %r is not a date or ISO time" % (m["message_id"], key, m[key]))
        if not util.parse_time(m["received_at"]):
            raise util.JobPilotError("message %s: received_at %r is not an ISO time" % (m["message_id"], m["received_at"]))
    if not util.parse_time(results["covered_until"]):
        raise util.JobPilotError("covered_until %r is not an ISO time" % results["covered_until"])
    summary = {"messages": len(results["messages"]), "recorded": 0, "events": [], "ignored": 0, "duplicates": 0,
               "unmatched": 0, "attention": [], "problems": list(results.get("problems") or [])}
    with ws.lock("inbox"):
        seen = _seen(ws)
        now = util.stamp(ws.now())
        for m in sorted(results["messages"], key=lambda m: util.parse_time(m["received_at"])):
            mid = m["message_id"]
            if mid in seen:
                summary["duplicates"] += 1
                continue
            if m["category"] == "other":
                seen[mid] = {"at": now, "outcome": "ignored", "category": "other"}
                summary["ignored"] += 1
                continue
            states = ledger.states(ws)
            st, how = _match(ws, m, states)
            if st is None:
                util.jsonl_append(_dir(ws) / "unmatched.jsonl", dict(
                    {k: m.get(k) for k in ("message_id", "thread_id", "received_at", "sender", "subject", "category",
                                           "company", "snippet", "deadline", "interview_time")}, reason=how))
                seen[mid] = {"at": now, "outcome": "unmatched", "category": m["category"]}
                summary["unmatched"] += 1
                continue
            events, attention = plan(st, m)
            for event, fields in events:
                ledger.record(ws, st["id"], event, date=_day(ws, m["received_at"]), **fields)
                summary["events"].append({"app_id": st["id"], "event": event})
            if attention:
                summary["attention"].append(attention)
            seen[mid] = {"at": now, "outcome": "recorded" if events else "duplicate_outcome", "app_id": st["id"],
                         "category": m["category"]}
            summary["recorded"] += bool(events)
        util.atomic_write_json(_dir(ws) / "seen.json", seen, mode=0o600)
        st = _state(ws)
        covered = max(filter(None, [util.parse_time(st.get("covered_until")), util.parse_time(results["covered_until"])]))
        st.update(last_scan=now, covered_until=covered.isoformat(timespec="seconds"),
                  messages_recorded=int(st.get("messages_recorded") or 0) + summary["recorded"],
                  last_complete=bool(results.get("complete")))
        util.atomic_write_json(_dir(ws) / "state.json", st, mode=0o600)
    if summary["recorded"] or summary["unmatched"]:
        ws.event("inbox", "inbox: %d updates recorded, %d emails unmatched" % (summary["recorded"], summary["unmatched"]),
                 recorded=summary["recorded"], unmatched=summary["unmatched"])
    return summary


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
        seen[message_id] = {"at": util.stamp(ws.now()), "outcome": "linked", "app_id": st["id"],
                            "category": rec.get("category")}
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
    context = {k: b[k] for k in ("window", "apps", "known_message_ids", "categories", "queries")}
    task = ("Triage the candidate's Gmail for job-application updates in the window below and answer with the "
            "structured output.\n\n<brief>\n%s\n</brief>" % json.dumps(context, ensure_ascii=False, indent=1))
    schema = results_schema()
    system = (PROMPTS / "inbox-triage.md").read_text(encoding="utf-8")
    cmd = [binary, "-p", task, "--output-format", "json", "--json-schema", json.dumps(schema, separators=(",", ":")),
           "--model", model, "--max-turns", "30", "--permission-mode", "dontAsk", "--no-session-persistence",
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
                   "".join("\n! " + x for x in res["attention"]))
    if a == "link":
        res = link(ws, args.message_id, args.app_id)
        return out(args, res, "%s: %s" % (res["app_id"], ", ".join(res["events"]) or "nothing new"))
    if a == "dismiss":
        return out(args, dismiss(ws, args.message_id), "dismissed")
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
    sp = add("inbox", cmd_inbox, help="Gmail progress tracking: brief, apply, link, unmatched, status, scan")
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
    q("status", help="last scan and coverage")
    p = q("scan", help="experimental: scan Gmail headless through the claude.ai Gmail connector")
    p.add_argument("--model", default="sonnet")
