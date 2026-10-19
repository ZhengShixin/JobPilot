"""Background tasks started from the dashboard: a daily run, a tailoring run, a referral search, outreach cards and
their cold emails.

Each task runs `<workspace>/jobpilot <args>` in its own session, detached from the dashboard, through a tiny runner
that records its pid and exit code in data/tasks/<id>.json, so a task's outcome survives a dashboard restart.
Output goes to data/logs/task-<id>.log. One task per kind and target runs at a time.

    start(ws, "tailor", ["tailor", "auto", pid], target=pid) -> task dict
    list(ws, n=20) / running(ws) / get(ws, task_id)
"""
import json
import os
import re
import secrets
import subprocess
import sys
import time

from .. import util
from ..workspace import PLUGIN_ROOT

RUNNER = r"""
import datetime, json, os, subprocess, sys
path, argv = sys.argv[1], sys.argv[2:]
def load():
    with open(path, encoding="utf-8") as f:
        return json.load(f)
def save(d):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)
d = load()
d["pid"] = os.getpid()
save(d)
with open(d["log"], "ab") as log:
    try:
        rc = subprocess.call(argv, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
    except OSError as exc:
        log.write(("could not start: %s\n" % exc).encode())
        rc = 127
d = load()
d.update(status="done" if rc == 0 else "failed", exit=rc,
         finished=datetime.datetime.now(datetime.timezone.utc).astimezone().isoformat(timespec="seconds"))
save(d)
"""
ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_PROCS = {}
KINDS = ("daily", "tailor", "net-find", "net-cards", "net-email", "review", "collect")


def _dir(ws):
    path = ws.data / "tasks"
    path.mkdir(parents=True, exist_ok=True)
    return path


def command(ws, argv):
    """The command line for `jobpilot <argv>` in this workspace: its ./jobpilot wrapper, else this package."""
    wrapper = ws.root / "jobpilot"
    if wrapper.exists() and os.access(wrapper, os.X_OK) and (ws.runtime / "bin" / "jobpilot").exists():
        return [str(wrapper), *argv]
    boot = "import sys; sys.path.insert(0, sys.argv[1]); from jobpilot.cli import main; sys.exit(main(sys.argv[2:]))"
    return [sys.executable, "-I", "-c", boot, str(PLUGIN_ROOT), "--workspace", str(ws.root), *argv]


def _env():
    env = dict(os.environ)
    for key in ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT"):
        env.pop(key, None)
    return env


def _read(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _alive(pid):
    try:
        os.kill(int(pid), 0)
    except (ProcessLookupError, ValueError, TypeError):
        return False
    except PermissionError:
        return True
    return True


def _tail(path, n=1500):
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - n))
            text = f.read().decode("utf-8", "replace")
    except OSError:
        return ""
    return ANSI.sub("", text)[-n:]


def _refresh(path, rec):
    """A task whose process is gone without recording an exit code ended abnormally."""
    proc = _PROCS.get(rec.get("id"))
    if proc is not None and proc.poll() is not None:
        _PROCS.pop(rec["id"], None)
    if rec.get("status") != "running":
        return rec
    started = util.parse_time(rec.get("started"))
    young = started and (util.now() - started).total_seconds() < 15
    if rec.get("pid") and _alive(rec["pid"]):
        return rec
    if not rec.get("pid") and young:
        return rec  # the runner has not recorded its pid yet
    again = _read(path) or rec
    if again.get("status") != "running":
        return again
    if again.get("pid") and _alive(again["pid"]):
        return again
    again.update(status="failed", exit=None, finished=util.stamp(), note="the process ended without a result")
    util.atomic_write_json(path, again)
    return again


def public(rec, log=True):
    out = {k: rec.get(k) for k in ("id", "kind", "target", "status", "started", "finished", "exit", "note", "argv")}
    if log:
        out["log_tail"] = _tail(rec.get("log") or "")
    return out


def list(ws, n=20, log=True):  # noqa: A001 - the module's public name
    folder = ws.data / "tasks"
    if not folder.exists():
        return []
    out = []
    for path in sorted(folder.glob("T*.json"), reverse=True)[: max(1, int(n))]:
        rec = _read(path)
        if rec:
            out.append(public(_refresh(path, rec), log=log))
    return out


def running(ws):
    return [t for t in list(ws, 50, log=False) if t["status"] == "running"]


def get(ws, task_id):
    if not re.fullmatch(r"T[0-9A-Za-z-]{6,40}", task_id or ""):
        return None
    path = _dir(ws) / ("%s.json" % task_id)
    rec = _read(path)
    return public(_refresh(path, rec)) if rec else None


def start(ws, kind, argv, target=None):
    if kind not in KINDS:
        raise util.JobPilotError("unknown task kind %r" % kind)
    key = "%s:%s" % (kind, target or "")
    for t in list(ws, 50, log=False):
        if t["status"] == "running" and "%s:%s" % (t["kind"], t.get("target") or "") == key:
            raise util.JobPilotError("this is already running (task %s)" % t["id"])
    tid = "T%s-%s" % (time.strftime("%Y%m%d-%H%M%S"), secrets.token_hex(2))
    ws.logs.mkdir(parents=True, exist_ok=True)
    log = ws.logs / ("task-%s.log" % tid)
    path = _dir(ws) / ("%s.json" % tid)
    rec = {"id": tid, "kind": kind, "target": target, "argv": [str(a) for a in argv], "status": "running",
           "started": util.stamp(ws.now()), "log": str(log)}
    util.atomic_write_json(path, rec)
    proc = subprocess.Popen([sys.executable, "-I", "-c", RUNNER, str(path), *command(ws, rec["argv"])],
                            cwd=str(ws.root), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, start_new_session=True, env=_env())
    _PROCS[tid] = proc
    return public(rec, log=False)
