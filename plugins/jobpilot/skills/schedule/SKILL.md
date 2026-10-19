---
name: schedule
description: Turn JobPilot's daily automation on or off — a daily run (search, review, report, notification) at a set time via a macOS LaunchAgent or a Linux crontab line. Use when the user wants JobPilot to run every day automatically, change the time, or stop it.
argument-hint: "[install HH:MM | remove | status]"
allowed-tools: Bash(jobpilot *)
---

# Daily automation

Reply in the user's language.

1. `jobpilot schedule status --json` and `jobpilot doctor` (the token and the Claude login must work unattended).
2. Install: confirm the time (default `search.run_time`, e.g. 07:30 local time; early morning catches overnight
   postings), then `jobpilot schedule install --time HH:MM`. Explain:
   - macOS: a LaunchAgent; if the Mac is asleep at that time, the run starts when it wakes. The review uses the
     user's Claude Code login. Logs: `data/logs/daily.log`.
   - Linux: a crontab line; unattended `claude -p` needs `CLAUDE_CODE_OAUTH_TOKEN` (`claude setup-token`) or
     `ANTHROPIC_API_KEY` in the environment.
   - Every run is capped by `search.daily_budget_usd` and `search.monthly_budget_usd`; `jobpilot pause` stops all
     paid runs at once, `jobpilot unpause` lifts it.
   - Optional notification: set `[notify] command` in jobpilot.toml, e.g.
     `command = "osascript -e 'display notification {text} with title {title}'"` (macOS) or a `curl` to ntfy.sh.
3. Remove: `jobpilot schedule remove`. Status: `jobpilot schedule status`.
