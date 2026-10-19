---
name: help
description: Explain what JobPilot does, how its daily loop works, every /jobpilot command and CLI command, costs and privacy. Use when the user asks how JobPilot works or what they can do with it.
allowed-tools: Bash(jobpilot *)
---

# JobPilot help

Reply in the user's language with a short, friendly overview adapted to where they are (run `jobpilot doctor` to see
whether the workspace is set up). Cover:

**The loop** — `/jobpilot:setup` once (resume -> facts, tracks, rubrics, search plan); then every day (scheduled or
`/jobpilot:daily`): Apify search under budget caps -> free rule filter -> AI review with verbatim evidence -> pool ->
report. The user picks roles (`/jobpilot:jobs` or the dashboard), gets a truthful tailored resume
(`/jobpilot:tailor`), applies **themselves**, and records progress (`/jobpilot:track`, `/jobpilot:inbox`).
Referrals: `/jobpilot:referrals <company>`.

**Slash commands** — setup, daily, jobs, tailor, track, inbox, referrals, dashboard, tune, schedule, help.

**CLI** (`jobpilot <command>` inside Claude Code, `./jobpilot <command>` in a terminal): init, doctor, config check,
token set|status|clear, pause/unpause, plan show|check, collect [--dry-run], review, daily, queue, pool
list|show|mark|add, stats, app add|event|list|show|due, tailor prepare|build|auto|audit|lint|list, resume
text|paragraphs|build-docx, net find|score|cards|email|run|list|show|mark|due|remove|restore, inbox
brief|apply|link|dismiss|unmatched|waiting|answered|invite|status|scan, brief, events, dashboard, schedule
install|remove|status, demo <folder>, version. `jobpilot <command> --help` shows options.

**Costs** — Apify: the user's own account; about $0.005 per posting on the free tier (cheaper on paid plans), hard
caps per run, per day and per month. Claude: the reviews and tailoring run on the user's Claude Code plan
(`review.model`, default sonnet).

**Privacy and safety** — everything stays in the workspace folder; job descriptions and the scoring profile go to
Claude, search filters to Apify. JobPilot never submits applications, never sends email or LinkedIn messages, never
reads the Apify token into the conversation, and never puts a fact on a resume that profile.md does not contain.

**Try it without accounts** — `jobpilot demo ~/jobpilot-demo` then `cd ~/jobpilot-demo && ./jobpilot dashboard`.
