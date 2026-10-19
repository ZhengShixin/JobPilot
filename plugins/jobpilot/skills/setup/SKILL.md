---
name: setup
description: Set up JobPilot from the user's resume. Reads the resume, extracts verified facts, proposes target role tracks, writes per-track scoring rubrics and an Apify LinkedIn search plan, checks the plan offline, and gets the first search ready. Use when the user wants to start with JobPilot, gives a resume, or wants to redesign their search from scratch.
argument-hint: "[path to resume: PDF, DOCX or Markdown]"
allowed-tools: Bash(jobpilot *), Bash(cp *), Bash(mkdir *), Bash(ls *), Read, Write, Edit, Glob, AskUserQuestion
---

# JobPilot setup: from a resume to a working daily search

You turn one resume into a complete, truthful JobPilot workspace. Reply in the user's language. Be concise between
steps. Never invent a fact about the user; when the resume is unclear, ask.

Resume given: `$ARGUMENTS` (if empty, ask for the path; the user can drag the file into the terminal).

References (read them when you reach the step that needs them):
- `${CLAUDE_PLUGIN_ROOT}/skills/setup/reference/profile-guide.md` — profile.md and claims.md
- `${CLAUDE_PLUGIN_ROOT}/skills/setup/reference/rubric-guide.md` — rubric.md
- `${CLAUDE_PLUGIN_ROOT}/skills/setup/reference/search-plan-guide.md` — the Apify search plan (matching rules!)

## 1. Workspace

Run `jobpilot config check --json` to see whether a workspace exists here.
- None yet: the workspace is the current folder unless it is the home folder or an unrelated project; then propose
  `~/job-search` (create it with `mkdir -p`) and use `--workspace <dir>` on every later command (tell the user to
  start Claude Code in that folder next time). Run `jobpilot init <dir> --name "<full name>" --lang <en|zh>` (zh if
  the user writes Chinese).
- Already set up: ask whether to update it (keep data, rewrite profile/rubric/plan) or stop. Never delete data/.

## 2. Read the resume

Copy the file into `<workspace>/profile/` as `resume.<ext>` (keep the original too). Read it:
PDF with the Read tool; DOCX with `jobpilot resume text profile/resume.docx`; Markdown/TXT with Read.
If it is a PDF only, tailoring later needs a DOCX: ask whether they have the Word version; otherwise write
`profile/resume.md` (the same content as structured Markdown) and run `jobpilot resume build-docx profile/resume.md
profile/resume.docx` to create a clean master (show the user; they may prefer their own Word file).

## 3. profile.md and claims.md

Follow profile-guide.md. Write `profile/profile.md` (facts verbatim, sections, "To confirm" list) and
`profile/claims.md`. Then ask about anything in "To confirm" that matters for the search.

## 4. Ask what only the user can decide

One AskUserQuestion call (up to 4 questions):
1. Work authorization for the target country: U.S. citizen / permanent resident (green card) / work visa now
   (e.g. OPT, H-1B), may need sponsorship later / needs visa sponsorship / other. Maps to
   `candidate.work_authorization`: us_citizen | permanent_resident | work_visa | needs_sponsorship | other.
2. Target level: entry / mid / senior / lead (multi-select) -> `candidate.seniority`.
3. Where: locations (country or cities) and remote/hybrid/on-site preference -> `candidate.locations`,
   `candidate.work_arrangements` (exact values in search-plan-guide.md).
4. Monthly Apify budget: about $10 (narrow search), $25 (recommended), $50 (broad). -> `search.monthly_budget_usd`,
   and `search.daily_budget_usd` ≈ monthly / 25.

Then propose 2-4 **tracks** (role families) the resume supports, each with a one-line reason that cites the resume,
ordered by fit, and ask the user to pick and rank them (AskUserQuestion, multiSelect; "Other" lets them add one).
Ask for a minimum salary only if they care (`candidate.min_salary_usd`, 0 = off), companies to avoid
(`filters.exclude_companies`) and their schools for the referral finder (`candidate.schools`, prefilled from the
resume: confirm the exact official names).

## 5. rubric.md

Follow rubric-guide.md: one rubric per chosen track with anchors grounded in this resume, fit caps, categories; then
"Candidate facts" (short verbatim sentences from profile.md) and "Never credit" (from claims.md).

## 6. jobpilot.toml

Edit the file (keep its comments). Fill `[candidate]` (name, resume = "profile/resume.docx" or the master you made,
work_authorization, seniority, locations, work_arrangements, min_salary_usd, languages, schools), `[app]`, and add:
- `[[tracks]]` per track: id (short, lowercase: e.g. "mle", "ds", "swe", "qr"), name, priority (1 = first choice;
  equal numbers rank equal), min_score (5.0 default; 5.5-6 for a fallback track), description (one sentence: the
  core duties that put a role on this track), categories (exactly the rubric's list).
- `[search]` budgets and `run_time`; `[search.common]` with `aiEmploymentTypeFilter = ["FULL_TIME"]` (unless they
  want internships or contracts) and, for early-career searches, consider `aiExperienceLevelFilter`.
- `[search.groups.<name>]`: 1-4 groups per track following search-plan-guide.md (matching rules: no stemming,
  phrases, `:*` prefixes, exclusions, at most ~13 description terms, exact industry names, a `budget_usd` cap each).
- `[filters]`: `exclude_title_words` (titles the candidate never wants: e.g. Sales, Recruiter, Nurse),
  `protect_title_words` (words that override them), `drop_experience_levels` (entry: ["5-10", "10+"]; mid: ["10+"];
  senior: []), `exclude_companies`.
- `[claims] never_claim = [...]` from claims.md; `[referrals] sign_name = "<first name>"`.

## 7. Check the plan for free

Write `profile/plan-examples.json` (format in search-plan-guide.md): 15-30 realistic positive titles per track and
15-30 negatives (near misses). Run `jobpilot plan check`. Fix until every track's recall is at least 90%, stemming
traps are handled and few negatives match; explain notable changes. Then `jobpilot config check` and
`jobpilot doctor`.

## 8. Apify token and the first run

If `jobpilot token status` says no token: explain that searches run on the user's own Apify account (free plan:
$5 of monthly credit; the actor costs about $0.005 per posting on the free tier) and ask them to:
1. create a token at https://console.apify.com/settings/integrations;
2. run in **their own terminal** (not here): `cd <workspace> && ./jobpilot token set` (hidden prompt; saved with
   mode 600 in ~/.config/jobpilot). Never ask them to paste the token into the chat, and never read token files.

Show `jobpilot collect --dry-run` (groups, caps, today's budget). Ask before spending: "Run the first search now
(at most $<daily cap>)?" If yes, run `jobpilot daily` (it can take 5-20 minutes; use a long timeout) and summarize
the report (`reports/daily/<date>.md`). The review step uses the user's Claude Code login.

## 9. Wrap up

Summarize in a few lines: tracks and priorities, number of search groups, budget caps, files written. Next steps:
`/jobpilot:dashboard` (see the matches), `/jobpilot:schedule` (run daily automatically), `/jobpilot:tailor <id>`
(a truthful tailored resume), `/jobpilot:tune` (improve the search after a few days).
