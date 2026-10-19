#!/usr/bin/env python3
"""JobPilot guard (PreToolUse hook). Defense in depth around the user's own Claude Code sessions.

Everywhere (the plugin is enabled):
  - never read the Apify token (~/.config/jobpilot, apify-token files) or a JobPilot workspace's .env.
Inside a JobPilot workspace (a folder with jobpilot.toml at or above the session's directory):
  - no direct edits of the ledgers and the database (data/*.jsonl, data/jobpilot.db): the CLI keeps their hash chains;
  - no email that leaves or changes the mailbox (send, draft, reply, forward, label, archive, trash, spam);
  - no browser automation on linkedin.com (JobPilot never sends invitations or messages for the user).
Standard library only and Python 3.8+ (it may run under the system python3). A bug never blocks the session: any
unexpected error allows the call and reports itself on stderr.
"""
import json
import os
import re
import sys

SECRET_PATH = re.compile(r"(\.config/jobpilot\b|apify[-_]token)", re.I)
TOKEN_CMD = re.compile(r"^\s*(\S*/)?jobpilot\s+token\s+(status|set|clear)\b")
DOTENV = re.compile(r"(^|[\s'\"=/])\.env(\b|$)")
READERS = re.compile(r"\b(cat|less|more|head|tail|grep|egrep|rg|sed|awk|source|strings|xxd|od|cp|mv|base64|python3?|"
                     r"perl|ruby|node|open|bat|nl|tac|curl|scp)\b|(^|[;&|]\s*)\.\s")
LEDGER = re.compile(r"data/(apps|budget|events|pool-events|claude-runs)\.jsonl|data/jobpilot\.db")
LEDGER_WRITE = re.compile(r"(>>?|\btee\b|\bsqlite3\b|\brm\b|\btruncate\b|\bsed\s+-i|\bmv\b|\bcp\b)[^|;&]*"
                          r"(data/(apps|budget|events|pool-events|claude-runs)\.jsonl|data/jobpilot\.db)")
MAIL_TOOL = re.compile(r"^mcp__.*(gmail|mail|outlook|email).*__", re.I)
MAIL_WRITE = re.compile(r"^(send|create|update|delete|trash|untrash|reply|forward|label|unlabel|apply|mark|unmark|"
                        r"modify|move|archive|remove|batch)", re.I)
BROWSER_TOOL = re.compile(r"^mcp__.*(chrome|browser|playwright|puppeteer|computer)", re.I)


def workspace(cwd):
    here = os.path.abspath(cwd or os.getcwd())
    while True:
        if os.path.isfile(os.path.join(here, "jobpilot.toml")):
            return here
        parent = os.path.dirname(here)
        if parent == here:
            return None
        here = parent


def deny(reason):
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                             "permissionDecisionReason": "JobPilot guard: " + reason}}))
    sys.exit(0)


def check(data):
    tool = data.get("tool_name") or ""
    inp = data.get("tool_input") or {}
    ws = workspace(data.get("cwd"))
    path = str(inp.get("file_path") or inp.get("notebook_path") or inp.get("path") or "")
    cmd = str(inp.get("command") or "")
    if tool in ("Read", "Grep", "Glob"):
        target = path or str(inp.get("pattern") or "")
        if SECRET_PATH.search(target):
            deny("the Apify token stays out of the conversation (JobPilot loads it itself).")
        if ws and os.path.basename(path) == ".env":
            deny("the workspace .env holds secrets; JobPilot reads it itself.")
    if tool == "Bash":
        if SECRET_PATH.search(cmd) and not TOKEN_CMD.match(cmd):
            deny("the Apify token stays out of the conversation; use `jobpilot token status`.")
        if ws and DOTENV.search(cmd) and READERS.search(cmd):
            deny("the workspace .env holds secrets; JobPilot reads it itself.")
        if ws and LEDGER_WRITE.search(cmd):
            deny("change applications and postings through the jobpilot CLI (`jobpilot app ...`, `jobpilot pool "
                 "mark ...`) so the ledgers keep their hash chains.")
    if not ws:
        return
    if tool in ("Edit", "Write", "MultiEdit", "NotebookEdit") and LEDGER.search(path.replace(os.sep, "/")):
        deny("change applications and postings through the jobpilot CLI so the ledgers keep their hash chains.")
    if MAIL_TOOL.match(tool) and MAIL_WRITE.search(tool.split("__")[-1]):
        deny("JobPilot only reads email; the user sends, labels and deletes mail themselves.")
    if BROWSER_TOOL.match(tool) and "linkedin.com" in json.dumps(inp).lower():
        deny("JobPilot never automates LinkedIn; the user sends invitations and messages personally.")


def main():
    try:
        data = json.load(sys.stdin)
    except ValueError:
        return 0
    try:
        check(data)
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 - never block a session because of a guard bug
        sys.stderr.write("JobPilot guard error (call allowed): %r\n" % (exc,))
    return 0


if __name__ == "__main__":
    sys.exit(main())
