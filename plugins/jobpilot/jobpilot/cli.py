"""The `jobpilot` command line. Every command works the same from Claude Code (the plugin puts `jobpilot` on the
Bash tool's PATH), from a terminal (`./jobpilot` in the workspace) and from scheduled runs.

Commands from feature modules (tailor, referrals, inbox) are added by their own register(sub) functions; a handler
takes (workspace, args) and returns an exit code. `--json` prints machine-readable output for skills.
"""
import argparse
import getpass
import importlib
import json
import os
import platform
import shutil
import subprocess
import sys

from . import __version__, budget, config, ics, ledger, pool, util, workspace
from .db import STATUSES

FEATURE_MODULES = ("tailor", "network", "inbox")


def out(args, data, text=None):
    if getattr(args, "json", False):
        print(json.dumps(data, ensure_ascii=False, indent=2, default=str))
    elif text is not None:
        print(text)
    else:
        print(json.dumps(data, ensure_ascii=False, indent=2, default=str))
    return 0


def _ws(args):
    return workspace.find(explicit=getattr(args, "workspace", None))


def _sync(args):
    """Keep the workspace's runtime copy (./jobpilot, scheduled runs) identical to the code running now."""
    try:
        workspace.sync_runtime(_ws(args))
    except (workspace.NotFound, OSError):
        pass


# ---------------------------------------------------------------- setup and health

def cmd_init(args):
    ws, created = workspace.init(args.dir or os.getcwd(), name=args.name or "", language=args.lang or "en")
    return out(args, {"workspace": str(ws.root), "created": created},
               "JobPilot workspace: %s\n%s" % (ws.root, "\n".join("  + " + c for c in created) or "  (nothing new)"))


def cmd_doctor(ws, args):
    checks = []
    add = lambda name, ok, detail="": checks.append({"check": name, "ok": ok, "detail": detail})  # noqa: E731
    add("python", sys.version_info >= (3, 11), platform.python_version())
    add("jobpilot", True, __version__)
    try:
        problems = config.check(ws.cfg, ws)
        errs = [m for lvl, m in problems if lvl == "error"]
        add("config", not errs, "; ".join(errs) or "%d warnings" % len(problems))
        for lvl, m in problems:
            if lvl == "warn":
                add("config warning", True, m)
    except util.JobPilotError as exc:
        add("config", False, str(exc))
    from . import claude
    cb = claude.binary()
    ver = ""
    if cb:
        try:
            ver = subprocess.run([cb, "--version"], capture_output=True, text=True, timeout=20).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            ver = "?"
    add("claude CLI", bool(cb), ver or "not found: install Claude Code and log in")
    from . import apify
    src = apify.token_source(ws)
    add("Apify token", bool(src), {"env": "from $APIFY_TOKEN", "file": "saved in ~/.config/jobpilot",
                                   ".env": "from the workspace .env"}.get(src, "missing: run `./jobpilot token set`"))
    soffice = shutil.which("soffice") or shutil.which("libreoffice") or (
        "/Applications/LibreOffice.app/Contents/MacOS/soffice"
        if os.path.exists("/Applications/LibreOffice.app/Contents/MacOS/soffice") else None)
    add("LibreOffice (PDF export)", True, soffice or "not found: tailored resumes stay .docx (optional)")
    for name, path in (("apps ledger", ws.apps_path), ("budget ledger", ws.budget_path)):
        bad = util.jsonl_verify(path)
        add(name, not bad, "; ".join(bad[:3]) or ("intact" if path.exists() else "empty"))
    try:
        from . import schedule
        add("schedule", True, schedule.status(ws).get("text", ""))
    except Exception as exc:  # noqa: BLE001 - doctor reports, never crashes
        add("schedule", True, "unknown (%s)" % exc)
    paused = ws.data / "PAUSE"
    add("paid runs", not paused.exists(), "paused" if paused.exists() else "allowed")
    ok = all(c["ok"] for c in checks if c["check"] != "config warning")
    text = "\n".join("%s %-26s %s" % ("✓" if c["ok"] else "✗", c["check"], c["detail"]) for c in checks)
    out(args, {"ok": ok, "checks": checks}, text)
    return 0 if ok else 1


def cmd_config(ws, args):
    if args.action == "check":
        problems = config.check(ws.cfg, ws)
        errs = [m for lvl, m in problems if lvl == "error"]
        out(args, {"ok": not errs, "problems": [{"level": lvl, "message": m} for lvl, m in problems]},
            "\n".join("%s: %s" % (lvl, m) for lvl, m in problems) or "jobpilot.toml looks good")
        return 1 if errs else 0
    return out(args, ws.cfg)


def cmd_token(args):
    from . import apify
    if args.action == "set":
        if not sys.stdin.isatty():
            print("Run this in your own terminal: it reads the token without echoing it.", file=sys.stderr)
            return 2
        value = getpass.getpass("Apify API token (input hidden): ")
        path = apify.save_token(value)
        print("saved to %s (mode 600). JobPilot never prints it." % path)
        return 0
    if args.action == "clear":
        if apify.TOKEN_FILE.exists():
            apify.TOKEN_FILE.unlink()
        print("removed %s" % apify.TOKEN_FILE)
        return 0
    ws = None
    try:
        ws = workspace.find(explicit=getattr(args, "workspace", None))
    except workspace.NotFound:
        pass
    src = apify.token_source(ws)
    return out(args, {"configured": bool(src), "source": src},
               "Apify token: %s" % ({"env": "set in the environment", "file": "saved in ~/.config/jobpilot",
                                     ".env": "in the workspace .env"}.get(src) or "not set (run `./jobpilot token set`)"))


def cmd_pause(ws, args):
    p = ws.data / "PAUSE"
    if args.action == "pause":
        util.atomic_write_text(p, (args.reason or "paused") + "\n")
        ws.event("system", "paid runs paused: %s" % (args.reason or ""))
        print("paid runs paused (data/PAUSE). `jobpilot unpause` lifts it.")
    else:
        if p.exists():
            p.unlink()
        ws.event("system", "paid runs resumed")
        print("paid runs allowed again")
    return 0


# ---------------------------------------------------------------- plan, collect, review, daily

def cmd_plan(ws, args):
    from . import plancheck, search
    if args.action == "show":
        data = [{"name": g["name"], "track": g["track"], "budget_usd": g["budget"], "filters": g["filters"]}
                for g in search.plan(ws.cfg)]
        text = "\n".join("%-24s %-8s $%.2f  %s" % (g["name"], g["track"], g["budget_usd"],
                                                   util.clip(json.dumps(g["filters"], ensure_ascii=False), 90))
                         for g in data)
        return out(args, data, text)
    report = plancheck.check(ws, examples=args.examples)
    out(args, report, plancheck.render(report))
    return 0 if report.get("ok") else 1


def cmd_collect(ws, args):
    from . import search
    client = None
    if args.dry_run and not util.offline():
        from .apify import Apify
        client = Apify()  # the input-schema check needs no token
    try:
        res = search.collect(ws, client=client, dry_run=args.dry_run, only=args.groups, again=args.again,
                             max_seconds=args.max_seconds, log=(lambda *_: None) if args.json else print)
    except util.JobPilotError as exc:
        print("collect: %s" % exc, file=sys.stderr)
        return 1
    return out(args, res, None if args.dry_run else
               "collected %(rows)d postings (%(new)d new) for $%(cost).3f" % res)


def cmd_review(ws, args):
    from . import review
    res = review.run(ws, max_batches=args.max_batches, ids=args.ids, rescore=not args.no_rescore,
                     log=(lambda *_: None) if args.json else print)
    out(args, res, "reviewed %(reviewed)d: %(ready)d ready, %(below)d below threshold, %(excluded)d excluded, "
                   "%(failed)d failed; %(batches)d batches" % res)
    return 0 if not res["errors"] or res["reviewed"] else 1


def cmd_daily(ws, args):
    from . import daily
    log = (lambda *_: None) if (args.quiet or args.json) else print
    res = daily.run(ws, do_collect=not args.no_collect, do_review=not args.no_review, log=log)
    return out(args, res, res["summary"] + ("\n" + "\n".join("! " + p for p in res["problems"]) if res["problems"]
                                            else ""))


# ---------------------------------------------------------------- pool and queue

def _row_line(r):
    return "%-12s %4s  %-6s %-28s %-40s %s" % (r["id"], "%.1f" % r["score"] if r.get("score") is not None else "-",
                                              r.get("track") or "", util.clip(r.get("company"), 28),
                                              util.clip(r.get("title"), 40), r.get("status"))


def cmd_queue(ws, args):
    rows = pool.queue(ws, limit=args.n, track=args.track)
    return out(args, [pool.public(r, cfg=ws.cfg) for r in rows],
               "\n".join(_row_line(r) + (" (practice)" if pool.is_practice(r, ws.cfg) else "") for r in rows)
               or "the queue is empty")


def cmd_pool(ws, args):
    if args.action == "list":
        where, params = "1=1", []
        if args.status:
            where += " AND status=?"
            params.append(args.status)
        if args.track:
            where += " AND track=?"
            params.append(args.track)
        rows = ws.db.find(where, params, order="score DESC, first_seen DESC", limit=args.limit)
        return out(args, [pool.public(r, cfg=ws.cfg) for r in rows], "\n".join(_row_line(r) for r in rows)
                   or "no postings")
    if args.action == "show":
        row = ws.db.get(args.id)
        if not row:
            print("no posting %s" % args.id, file=sys.stderr)
            return 1
        from . import search
        data = pool.public(row, full=True, cfg=ws.cfg)
        if args.jd:
            data["jd"] = search.read_jd(ws, row["id"])
        return out(args, data)
    if args.action == "mark":
        row = pool.mark(ws, args.id, args.status, note=args.note or "")
        return out(args, pool.public(row, cfg=ws.cfg), "%s -> %s" % (row["id"], row["status"]))
    if args.action == "add":
        from . import search
        jd = open(args.jd_file, encoding="utf-8").read() if args.jd_file else sys.stdin.read()
        if len(jd.strip()) < 200:
            print("the job description looks too short (paste the full text)", file=sys.stderr)
            return 1
        row = search.add_manual(ws, title=args.title, company=args.company, jd=jd, url=args.url or "",
                                location=args.location or "", track=args.track or "")
        if args.review:
            from . import review
            review.run(ws, ids=[row["id"]], log=(lambda *_: None) if args.json else print)
            row = ws.db.get(row["id"])
        return out(args, pool.public(row, full=True, cfg=ws.cfg), "added %s (%s at %s): %s" % (
            row["id"], row["title"], row["company"], row.get("status")))
    raise AssertionError(args.action)


def cmd_stats(ws, args):
    data = {"pool": pool.stats(ws), "funnel": ledger.funnel(ws), "budget": budget.summary(ws), "due": ledger.due(ws)}
    return out(args, data)


# ---------------------------------------------------------------- applications

def cmd_app(ws, args):
    if args.action == "add":
        posting = ws.db.get(args.posting) if args.posting else None
        if args.posting and not posting:
            print("no posting %s" % args.posting, file=sys.stderr)
            return 1
        company = args.company or (posting or {}).get("company")
        role = args.role or (posting or {}).get("title")
        app_id = ledger.create(ws, company, role, posting_id=args.posting, track=args.track or (posting or {}).get(
            "track") or "", url=args.url or (posting or {}).get("apply_url") or (posting or {}).get("url") or "",
            location=(posting or {}).get("location") or "", note=args.note or "")
        if args.applied:
            st = ledger.state(ws, app_id)
            if st["stage"] == "planned":
                ledger.record(ws, app_id, "applied", date=args.date or ws.today())
        if posting:
            pool.mark(ws, posting["id"], "applied" if args.applied else "hold",
                      note="application %s" % app_id, app_id=app_id)
        ws.event("app", "%s %s: %s — %s" % (app_id, "applied" if args.applied else "saved", company, role),
                 app_id=app_id)
        return out(args, ledger.state(ws, app_id), "%s %s — %s (%s)" % (app_id, company, role,
                                                                          ledger.state(ws, app_id)["stage"]))
    if args.action == "event":
        fields = {k: getattr(args, k) for k in ("date", "deadline", "time", "note", "referrer", "minutes", "rescheduled")
                  if getattr(args, k, None)}
        ledger.record(ws, args.app_id, args.event, **fields)
        st = ledger.state(ws, args.app_id)
        if args.event == "applied" and st.get("posting_id"):
            try:
                pool.mark(ws, st["posting_id"], "applied", note="application %s" % args.app_id, app_id=args.app_id)
            except util.JobPilotError:
                pass
        if args.event == "void" and st.get("posting_id"):
            try:
                pool.mark(ws, st["posting_id"], "ready", note="application %s voided" % args.app_id)
            except util.JobPilotError:
                pass
        ws.event("app", "%s %s: %s — %s" % (args.app_id, args.event, st["company"], st["role"]), app_id=args.app_id)
        ics.refresh(ws)
        return out(args, st, "%s is now %s" % (args.app_id, st["stage"]))
    if args.action == "list":
        sts = [s for s in ledger.states(ws).values() if not s["voided"] and (args.all or not s["closed"])]
        sts.sort(key=lambda s: s.get("last_date") or "", reverse=True)
        text = "\n".join("%s  %-10s %-28s %-40s %s" % (s["id"], s["stage"], util.clip(s["company"], 28),
                                                       util.clip(s["role"], 40), s.get("last_date") or "")
                         for s in sts)
        return out(args, sts, text or "no applications yet")
    if args.action == "show":
        st = ledger.state(ws, args.app_id)
        if not st:
            print("no application %s" % args.app_id, file=sys.stderr)
            return 1
        return out(args, st)
    if args.action == "due":
        return out(args, ledger.due(ws))
    if args.action == "ics":
        path, n = ics.write(ws, out=args.out)
        return out(args, {"path": str(path), "entries": n},
                   "%s: %d upcoming interview(s) and assessment deadline(s); import it into your calendar (importing a "
                   "newer file updates the same entries)" % (path, n))
    raise AssertionError(args.action)


# ---------------------------------------------------------------- reports, dashboard, schedule, demo

def cmd_brief(ws, args):
    from . import brief
    text, data = brief.build(ws, date=args.date)
    return out(args, data, text)


def cmd_events(ws, args):
    evs = util.jsonl_read(ws.events_path)[-args.n:]
    return out(args, evs, "\n".join("%s  %-8s %s" % (e.get("ts", "")[:16], e.get("kind"), e.get("text")) for e in evs))


def cmd_dashboard(ws, args):
    from .dashboard import server
    if args.export:
        print(server.export_static(ws, args.export))
        return 0
    return server.serve(ws, port=args.port, open_browser=not args.no_open)


def cmd_schedule(ws, args):
    from . import schedule
    if args.action == "install":
        res = schedule.install(ws, at=args.time)
    elif args.action == "remove":
        res = schedule.remove(ws)
    else:
        res = schedule.status(ws)
    return out(args, res, res.get("text"))


def cmd_demo(args):
    from . import demo
    ws = demo.create(args.dir, lang=args.lang or "en", force=args.force)
    print("demo workspace with fictional data: %s\nopen it: cd %s && ./jobpilot dashboard" % (ws.root, ws.root))
    if args.export:
        from .dashboard import server
        print("static demo page: %s" % server.export_static(ws, args.export))
    return 0


def cmd_version(args):
    print("jobpilot %s" % __version__)
    return 0


# ---------------------------------------------------------------- parser

def parser():
    p = argparse.ArgumentParser(prog="jobpilot", description="JobPilot: AI job-search copilot (see /jobpilot:help)")
    p.add_argument("--workspace", help="workspace folder (default: the nearest jobpilot.toml at or above here)")
    sub = p.add_subparsers(dest="command", required=True)

    def add(name, func, needs_ws=True, **kw):
        sp = sub.add_parser(name, **kw)
        sp.add_argument("--json", action="store_true", help="machine-readable output")
        sp.set_defaults(func=func, needs_ws=needs_ws)
        return sp

    sp = add("init", cmd_init, needs_ws=False, help="create a workspace")
    sp.add_argument("dir", nargs="?")
    sp.add_argument("--name")
    sp.add_argument("--lang", choices=["en", "zh"])
    add("doctor", cmd_doctor, help="check the installation and the workspace")
    sp = add("config", cmd_config, help="check or show jobpilot.toml")
    sp.add_argument("action", choices=["check", "show"])
    sp = add("token", cmd_token, needs_ws=False, help="Apify token: set (hidden prompt), status, clear")
    sp.add_argument("action", choices=["set", "status", "clear"])
    sp = add("pause", cmd_pause, help="refuse every paid run until `jobpilot unpause`")
    sp.add_argument("reason", nargs="?")
    sp.set_defaults(action="pause")
    sp = add("unpause", cmd_pause, help="allow paid runs again")
    sp.set_defaults(action="unpause", reason=None)

    sp = add("plan", cmd_plan, help="the search plan: show, or check it offline against example titles")
    sp.add_argument("action", choices=["show", "check"])
    sp.add_argument("--examples", help="examples JSON (default profile/plan-examples.json)")
    sp = add("collect", cmd_collect, help="run today's search on Apify (paid, capped)")
    sp.add_argument("--dry-run", action="store_true", help="print the inputs and caps; pay nothing")
    sp.add_argument("--groups", nargs="*", help="only these groups")
    sp.add_argument("--again", action="store_true", help="start an extra collection today")
    sp.add_argument("--max-seconds", type=int, default=1800)
    sp = add("review", cmd_review, help="coarse filter + AI review of new postings")
    sp.add_argument("--max-batches", type=int)
    sp.add_argument("--ids", nargs="*")
    sp.add_argument("--no-rescore", action="store_true")
    sp = add("daily", cmd_daily, help="collect, review, report and notify (what the schedule runs)")
    sp.add_argument("--no-collect", action="store_true")
    sp.add_argument("--no-review", action="store_true")
    sp.add_argument("--quiet", action="store_true")

    sp = add("queue", cmd_queue, help="postings ready to apply, best first")
    sp.add_argument("n", nargs="?", type=int, default=20)
    sp.add_argument("--track")
    sp = add("pool", cmd_pool, help="list, show, mark or add postings")
    psub = sp.add_subparsers(dest="action", required=True)
    for name in ("list", "show", "mark", "add"):
        q = psub.add_parser(name)
        q.add_argument("--json", action="store_true")
        if name == "list":
            q.add_argument("--status", choices=list(STATUSES))
            q.add_argument("--track")
            q.add_argument("--limit", type=int, default=50)
        elif name == "show":
            q.add_argument("id")
            q.add_argument("--jd", action="store_true", help="include the job description")
        elif name == "mark":
            q.add_argument("id")
            q.add_argument("status", choices=list(pool.MARKS))
            q.add_argument("--note")
        else:
            q.add_argument("--title", required=True)
            q.add_argument("--company", required=True)
            q.add_argument("--url")
            q.add_argument("--location")
            q.add_argument("--track")
            q.add_argument("--jd-file", help="the job description (default: stdin)")
            q.add_argument("--review", action="store_true", help="review it right away")
    add("stats", cmd_stats, help="pool, funnel, budget and due items")

    sp = add("app", cmd_app, help="applications: add, event, list, show, due, ics")
    asub = sp.add_subparsers(dest="action", required=True)
    q = asub.add_parser("add")
    q.add_argument("--posting", help="posting id from the pool")
    q.add_argument("--company")
    q.add_argument("--role")
    q.add_argument("--url")
    q.add_argument("--track")
    q.add_argument("--note")
    q.add_argument("--applied", action="store_true", help="you already applied")
    q.add_argument("--date")
    q = asub.add_parser("event")
    q.add_argument("app_id")
    q.add_argument("event", choices=[e for e in ledger.EVENTS if e != "created"])
    q.add_argument("--date")
    q.add_argument("--deadline", help="assessment deadline (YYYY-MM-DD or ISO time)")
    q.add_argument("--time", help="interview time (ISO, e.g. 2026-11-03T14:00)")
    q.add_argument("--minutes", type=int)
    q.add_argument("--rescheduled", action="store_true", help="the new time replaces the open interview's time")
    q.add_argument("--note")
    q.add_argument("--referrer", help="who referred you (initials are enough)")
    q = asub.add_parser("list")
    q.add_argument("--all", action="store_true", help="include closed applications")
    q = asub.add_parser("show")
    q.add_argument("app_id")
    asub.add_parser("due")
    q = asub.add_parser("ics", help="write upcoming interviews and assessment deadlines as an .ics calendar file")
    q.add_argument("--out", help="where to write it (default data/calendar.ics)")
    for q in asub.choices.values():
        q.add_argument("--json", action="store_true")

    sp = add("brief", cmd_brief, help="write today's report")
    sp.add_argument("--date")
    sp = add("events", cmd_events, help="the activity feed")
    sp.add_argument("-n", type=int, default=30)
    sp = add("dashboard", cmd_dashboard, help="start the local dashboard")
    sp.add_argument("--port", type=int)
    sp.add_argument("--no-open", action="store_true")
    sp.add_argument("--export", metavar="FILE", help="write a self-contained, read-only HTML snapshot and exit")
    sp = add("schedule", cmd_schedule, help="daily automation: install, remove, status")
    sp.add_argument("action", choices=["install", "remove", "status"])
    sp.add_argument("--time", help="HH:MM (default search.run_time)")
    sp = add("demo", cmd_demo, needs_ws=False, help="create a demo workspace with fictional data")
    sp.add_argument("dir")
    sp.add_argument("--lang", choices=["en", "zh"])
    sp.add_argument("--force", action="store_true")
    sp.add_argument("--export", metavar="FILE", help="also write a self-contained HTML page of the demo dashboard")
    add("version", cmd_version, needs_ws=False, help="print the version")

    for name in FEATURE_MODULES:
        try:
            importlib.import_module("jobpilot." + name).register(sub, add)
        except Exception as exc:  # noqa: BLE001 - a broken feature module must not take the core CLI down
            print("jobpilot: the %s commands are unavailable (%s)" % (name, exc), file=sys.stderr)
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        _sync(args)
        if args.needs_ws:
            return args.func(_ws(args), args) or 0
        return args.func(args) or 0
    except util.JobPilotError as exc:
        print("jobpilot: %s" % exc, file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    except BrokenPipeError:
        return 0
