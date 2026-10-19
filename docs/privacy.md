# Privacy, safety and terms

JobPilot runs on your machine, in a folder you choose (the *workspace*). This page lists exactly what goes where.

## What is stored, and where

| Data | Where | Notes |
|---|---|---|
| Your resume, `profile.md`, `claims.md`, `rubric.md` | `profile/` | written by you and `/jobpilot:setup` |
| Configuration | `jobpilot.toml` | no secrets |
| Postings, job descriptions, reviews and scores | `data/jobpilot.db`, `data/jds/` | SQLite + text files |
| Raw search results | `data/runs/<date>/` | gzip JSON, as Apify returned them |
| Applications, budget, activity | `data/apps.jsonl`, `data/budget.jsonl`, `data/events.jsonl` | append-only, hash-chained |
| Referral search results (people) | `data/network/` | whitelisted public profile fields only |
| Email updates | `data/inbox/` | message ids, dates, a short verbatim snippet and a one- or two-sentence summary; never full bodies |
| Upcoming interviews and assessment deadlines | `data/calendar.ics` | rewritten after every update, for you to import into a calendar app |
| Tailored resumes | `tailored/<date>_<company>_<role>/` | |
| Apify token | `~/.config/jobpilot/apify-token` (mode 600), `$APIFY_TOKEN` or the workspace `.env` | never printed or logged |
| Dashboard login key | `data/dashboard.token` (mode 600) | the dashboard binds to 127.0.0.1 only |

The workspace contains personal data. Keep it private: its `.gitignore` excludes `data/` and `.env`, but do not push
the folder to a public repository and do not publish screenshots of your real dashboard (use `jobpilot demo` for
demos).

## What leaves your machine

- **Apify** receives your search filters (titles, keywords, locations) and, for the referral finder, a company URL
  and your school names. It returns public postings and public profile data.
- **Anthropic (Claude)** receives, per review batch, the job descriptions and your scoring profile (`rubric.md`); for
  tailoring, your profile, claims policy, resume text and the job description; for outreach, the person's public
  profile lines and your profile; for the inbox scan (interactive), the emails your Gmail connector returns. The
  headless runs use your Claude Code login or API key.
- Nothing else: no analytics, no telemetry, no other servers.

## What JobPilot never does

- submit an application, fill a form, or log in anywhere for you;
- send, draft, reply to, forward, label, archive or delete email;
- send LinkedIn invitations or messages, or drive a browser on linkedin.com;
- read the Apify token into the conversation;
- put a fact about you into a resume or message that `profile.md` does not contain.

The cold email on a referral card is a draft for an address you found yourself: its "Open in Gmail" button only
opens Gmail's compose window in your browser with the text filled in (an address you type goes into that link and
nowhere else), and you decide whether to send it.

Inside a workspace, the plugin's guard hook (`plugins/jobpilot/hooks/guard.py`) enforces the email, LinkedIn,
ledger and token rules for every Claude Code tool call.

## Prompt injection

Job descriptions, LinkedIn profiles and emails are untrusted text. JobPilot wraps them in `<untrusted>` tags, tells
every role to ignore instructions inside them, runs the review roles with **no tools at all**, and validates every
answer with code. A posting the reviewer flags as carrying instructions is put on hold for you to check.

## Terms of service

- Job search results come from the Apify actor `fantastic-jobs/advanced-linkedin-job-search-api`, a third-party
  index of public job postings.
- The referral finder uses the Apify actor `harvestapi/linkedin-company-employees`, which collects public LinkedIn
  profile data. LinkedIn's User Agreement restricts scraping. Using it is your decision; JobPilot only runs it when
  you ask (`/jobpilot:referrals`), caps its cost, keeps the results local and never contacts anyone.
- You are responsible for complying with the terms of the services you use, the employers' application rules, and
  applicable privacy law when you store other people's public profile data.
