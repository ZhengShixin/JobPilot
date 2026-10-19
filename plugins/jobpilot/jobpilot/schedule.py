"""Daily automation: run `<workspace>/jobpilot daily` at search.run_time every day.

macOS: a per-user LaunchAgent (~/Library/LaunchAgents/com.jobpilot.daily.<id>.plist); a run missed while the Mac
slept starts when it wakes. Linux: one crontab line tagged `# jobpilot:<id>`. Output goes to data/logs/daily.log.
The headless review uses your Claude Code login (macOS keychain); on Linux set CLAUDE_CODE_OAUTH_TOKEN
(`claude setup-token`) or ANTHROPIC_API_KEY for cron.
JOBPILOT_SCHEDULE_DRY=1 writes nothing to the system (tests).
"""
import hashlib
import os
import platform
import re
import shutil
import subprocess
from pathlib import Path
from xml.sax.saxutils import escape

from . import claude, util


def _id(ws):
    return hashlib.sha256(str(ws.root).encode()).hexdigest()[:8]


def label(ws):
    return "com.jobpilot.daily.%s" % _id(ws)


def _time(ws, at=None):
    at = at or ws.cfg["search"].get("run_time") or "07:30"
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", str(at))
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        raise util.JobPilotError("time must look like 07:30")
    return int(m.group(1)), int(m.group(2))


def _path_env():
    dirs = []
    for exe in (claude.binary(), shutil.which("python3"), "/opt/homebrew/bin/python3", "/usr/local/bin/python3"):
        if exe and os.path.exists(exe):
            d = os.path.dirname(os.path.realpath(exe)) if exe.endswith("python3") else os.path.dirname(exe)
            if d not in dirs:
                dirs.append(d)
    for d in ("/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin", "/usr/sbin", "/sbin"):
        if d not in dirs:
            dirs.append(d)
    return ":".join(dirs)


def command(ws):
    log = ws.logs / "daily.log"
    return 'exec "%s" daily --quiet >> "%s" 2>&1' % (ws.root / "jobpilot", log)


def plist(ws, at=None):
    hour, minute = _time(ws, at)
    return """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>%s</string>
  <key>ProgramArguments</key>
  <array><string>/bin/sh</string><string>-c</string><string>%s</string></array>
  <key>StartCalendarInterval</key>
  <dict><key>Hour</key><integer>%d</integer><key>Minute</key><integer>%d</integer></dict>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key><string>%s</string>
    <key>JOBPILOT_WORKSPACE</key><string>%s</string>
  </dict>
  <key>WorkingDirectory</key><string>%s</string>
  <key>RunAtLoad</key><false/>
  <key>ProcessType</key><string>Background</string>
</dict>
</plist>
""" % (label(ws), escape(command(ws)), hour, minute, escape(_path_env()), escape(str(ws.root)), escape(str(ws.root)))


def cron_line(ws, at=None):
    hour, minute = _time(ws, at)
    return "%d %d * * * PATH=%s JOBPILOT_WORKSPACE=\"%s\" /bin/sh -c '%s' # jobpilot:%s" % (
        minute, hour, _path_env(), ws.root, command(ws).replace("'", ""), _id(ws))


def _dry():
    return os.environ.get("JOBPILOT_SCHEDULE_DRY") == "1"


def _plist_path(ws):
    return Path("~/Library/LaunchAgents").expanduser() / ("%s.plist" % label(ws))


def install(ws, at=None):
    ws.logs.mkdir(parents=True, exist_ok=True)
    workspace_wrapper = ws.root / "jobpilot"
    if not workspace_wrapper.exists():
        raise util.JobPilotError("missing %s; run `jobpilot init %s` first" % (workspace_wrapper, ws.root))
    hour, minute = _time(ws, at)
    when = "%02d:%02d" % (hour, minute)
    if platform.system() == "Darwin":
        text = plist(ws, at)
        path = _plist_path(ws)
        if _dry():
            return {"ok": True, "system": "launchd", "path": str(path), "plist": text, "text": "dry run"}
        path.parent.mkdir(parents=True, exist_ok=True)
        util.atomic_write_text(path, text)
        domain = "gui/%d" % os.getuid()
        subprocess.run(["launchctl", "bootout", domain, str(path)], capture_output=True)
        res = subprocess.run(["launchctl", "bootstrap", domain, str(path)], capture_output=True, text=True)
        if res.returncode != 0:
            raise util.JobPilotError("launchctl bootstrap failed: %s" % (res.stderr or res.stdout).strip()[:300])
        ws.event("system", "daily run scheduled at %s (launchd)" % when)
        return {"ok": True, "system": "launchd", "path": str(path), "time": when,
                "text": "scheduled: every day at %s (LaunchAgent %s); logs in data/logs/daily.log" % (when, label(ws))}
    line = cron_line(ws, at)
    if _dry():
        return {"ok": True, "system": "cron", "line": line, "text": "dry run"}
    current = _crontab()
    keep = [ln for ln in current.splitlines() if ("# jobpilot:%s" % _id(ws)) not in ln]
    _write_crontab("\n".join(keep + [line]) + "\n")
    ws.event("system", "daily run scheduled at %s (cron)" % when)
    return {"ok": True, "system": "cron", "line": line, "time": when,
            "text": "scheduled: every day at %s (crontab); logs in data/logs/daily.log" % when}


def remove(ws):
    if platform.system() == "Darwin":
        path = _plist_path(ws)
        if _dry():
            return {"ok": True, "text": "dry run"}
        if path.exists():
            subprocess.run(["launchctl", "bootout", "gui/%d" % os.getuid(), str(path)], capture_output=True)
            path.unlink()
            ws.event("system", "daily schedule removed")
            return {"ok": True, "text": "removed %s" % path.name}
        return {"ok": True, "text": "no schedule installed"}
    if _dry():
        return {"ok": True, "text": "dry run"}
    current = _crontab()
    keep = [ln for ln in current.splitlines() if ("# jobpilot:%s" % _id(ws)) not in ln]
    if len(keep) == len(current.splitlines()):
        return {"ok": True, "text": "no schedule installed"}
    _write_crontab("\n".join(keep) + ("\n" if keep else ""))
    ws.event("system", "daily schedule removed")
    return {"ok": True, "text": "removed the crontab line"}


def status(ws):
    if platform.system() == "Darwin":
        path = _plist_path(ws)
        if not path.exists():
            return {"installed": False, "text": "not scheduled (`jobpilot schedule install`)"}
        m = re.search(r"<key>Hour</key><integer>(\d+)</integer><key>Minute</key><integer>(\d+)</integer>",
                      path.read_text())
        when = "%02d:%02d" % (int(m.group(1)), int(m.group(2))) if m else "?"
        loaded = None
        if not _dry():
            res = subprocess.run(["launchctl", "print", "gui/%d/%s" % (os.getuid(), label(ws))], capture_output=True)
            loaded = res.returncode == 0
        return {"installed": True, "time": when, "loaded": loaded,
                "text": "daily at %s%s" % (when, "" if loaded in (None, True) else " (plist present but not loaded)")}
    line = next((ln for ln in _crontab().splitlines() if ("# jobpilot:%s" % _id(ws)) in ln), None)
    if not line:
        return {"installed": False, "text": "not scheduled (`jobpilot schedule install`)"}
    parts = line.split()
    return {"installed": True, "time": "%02d:%02d" % (int(parts[1]), int(parts[0])),
            "text": "daily at %02d:%02d (cron)" % (int(parts[1]), int(parts[0]))}


def _crontab():
    if _dry() or not shutil.which("crontab"):
        return ""
    res = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    return res.stdout if res.returncode == 0 else ""


def _write_crontab(text):
    if not shutil.which("crontab"):
        raise util.JobPilotError("crontab is not available; schedule `%s daily` with your system's scheduler" % "jobpilot")
    subprocess.run(["crontab", "-"], input=text, text=True, check=True)
