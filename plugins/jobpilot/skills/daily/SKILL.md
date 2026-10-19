---
name: daily
description: Run JobPilot's daily pipeline now — search new postings on Apify (paid, capped), drop ineligible ones for free, AI-review and score the rest, write the daily report. Use when the user asks to search for jobs now, refresh the pool, or run today's update.
argument-hint: "[--no-collect | --no-review]"
allowed-tools: Bash(jobpilot *), Read
---

# Run today's search and review

Reply in the user's language.

1. `jobpilot doctor --json`. If the config has errors, fix them first (or send the user to `/jobpilot:setup`). If the
   Apify token is missing, ask the user to run `./jobpilot token set` in their own terminal (never in this chat).
2. If the user passed `--no-collect` or only wants the backlog reviewed, skip to step 4.
3. Show what will be paid for: `jobpilot collect --dry-run --json` (groups, caps, `available_usd`). The caps are hard
   limits; if the user already confirmed spending in this session or scheduled runs exist, go ahead, otherwise ask
   once.
4. Run `jobpilot daily $ARGUMENTS` with a long timeout (up to 30 minutes; it prints progress). It collects, filters,
   reviews in batches with `claude -p`, and writes `reports/daily/<date>.md`.
5. Read the report and summarize briefly: new matches per track with the top 3 (score, company, title), anything due
   (assessments, interviews, follow-ups), spend today/month, problems (skipped or capped groups, review failures).
6. Offer next steps: open the dashboard (`/jobpilot:dashboard`), look at the best matches together (`/jobpilot:jobs`),
   tailor a resume (`/jobpilot:tailor <id>`). Never apply on the user's behalf.
