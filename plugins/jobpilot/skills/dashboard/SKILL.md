---
name: dashboard
description: Start JobPilot's local web dashboard (http://127.0.0.1, token login) showing today's matches, the job pool with score breakdowns, the application pipeline, referrals, search spend and activity. Use when the user wants to see their dashboard, matches or pipeline visually.
allowed-tools: Bash(jobpilot *), Bash(./jobpilot *)
---

# Open the dashboard

Reply in the user's language.

1. Recommend running it in the user's own terminal so it keeps running after this session:
   `cd <workspace> && ./jobpilot dashboard` (it prints a one-time login link and opens the browser).
2. If they want it started from here, run `jobpilot dashboard --no-open` in the background (Bash with
   run_in_background), wait a few seconds, read its output for the `http://127.0.0.1:<port>/?key=...` link and give
   that link to the user. It stays up while this Claude Code session runs.
3. The dashboard binds to 127.0.0.1 only and needs the login link (the key is stored in data/dashboard.token). From
   it the user can mark postings applied or skipped, record interview steps, start resume tailoring and the daily
   run, and copy referral notes. Nothing there submits applications or sends messages.
4. For a demo, a screenshot or a video, use a demo workspace with fictional data (`jobpilot demo ~/jobpilot-demo`,
   then `./jobpilot dashboard` there). The real dashboard shows personal data: never publish it or an export of it.
