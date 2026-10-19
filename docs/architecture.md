# Architecture

JobPilot is a deterministic core with one-shot AI roles around it. The core is plain Python (standard library only,
3.11+) in `plugins/jobpilot/jobpilot/`; the Claude Code plugin around it (`skills/`, `agents/`, `hooks/`) is how you
talk to it.

```
plugins/jobpilot/
├── .claude-plugin/plugin.json
├── bin/jobpilot            # on the Bash tool's PATH while the plugin is enabled
├── jobpilot/               # the core package
│   ├── workspace.py config.py db.py util.py schema.py
│   ├── apify.py budget.py search.py querymatch.py plancheck.py     # collection
│   ├── triage.py review.py scoring.py pool.py                      # filtering, review, the pool
│   ├── ledger.py brief.py daily.py schedule.py                     # applications, reports, automation
│   ├── docx.py tailor.py                                           # resume tailoring
│   ├── network.py inbox.py                                         # referrals, Gmail tracking
│   ├── claude.py                                                   # one-shot `claude -p` runs
│   ├── dashboard/  prompts/  data/
│   └── cli.py
├── skills/<name>/SKILL.md  # /jobpilot:<name>
├── agents/*.md             # claims-auditor, fit-analyst
└── hooks/hooks.json, guard.py
```

## Principles

1. **Code for what must be exact, AI for judgment.** Money, filters, scores, ledgers and file formats are code.
   Reading a job description, tailoring a bullet or writing a note is AI — and its output is checked by code.
2. **One-shot roles, no accumulating context.** Every AI step is a fresh `claude -p` run in an empty temporary
   directory with **no tools** (`--tools ""`, no MCP servers, no project settings) that can only answer through a JSON
   schema (`--json-schema`). Inputs are inlined inside `<untrusted>` tags. The same role prompt is reused across
   batches, so prompt caching keeps it cheap.
3. **The model proposes, code disposes.** `scoring.validate` accepts a review only if every JD quote occurs verbatim
   in the posting, every profile quote occurs verbatim in `rubric.md`, the four components are in range, the
   category belongs to the chosen track and any fit cap quotes its reason. The score is computed in code:
   `min(fit cap, duties_4 + background_3 + skills_2 + seniority_1)`.
4. **Scores are versioned.** Each score carries the sha256 of `rubric.md`; editing the rubric marks older scores
   stale and the review rescores them gradually.
5. **Every handoff is a file.** Manifests per collection, a SQLite pool, append-only JSONL ledgers with a sha256 hash
   chain, reports in Markdown. State such as an application's stage is derived from events, never stored.
6. **Fail safe with money.** Input keys are checked against the actor's input schema (free) before any paid run;
   every run carries `maxTotalChargeUsd`; spending is reserved before and settled after each run; a POST is never
   retried; an uncertain launch blocks further paid runs until it is reconciled by start time; `data/PAUSE` stops
   everything.
7. **The user acts; JobPilot prepares.** No auto-apply, no sending. A guard hook enforces it in the workspace.

## The daily run

`jobpilot daily` (what the schedule runs):

1. **Collect** (`search.collect`): window = from max(now − 7 days, min(now − 36 h, end of the last complete window −
   1 h)) to now; one Apify run per search group in track-priority order, at most `search.max_concurrent_runs` at a
   time, each capped by min(group budget, what today's cap has left). Today's cap = min(daily budget, the month's
   remaining budget / remaining days). New postings enter the pool as stage `new`.
2. **Coarse filter** (`triage.drop`): employment type and intern titles, "no current vacancy", citizenship (for
   non-citizens), "no sponsorship" (for those who need it), salary floor, experience band, excluded companies,
   recruitment agencies (optional), AI-training gig platforms, excluded title words. Reposts with the same employer,
   title and JD reuse the earlier review.
3. **AI review** (`review.run`): batches of `review.batch_size` postings, newest and highest-priority first; each
   entry validated separately, so one bad entry never costs the batch; failures retry up to `review.max_attempts`.
4. **Publish**: a checked review sets the status — `ready` (score ≥ the track's `min_score`), `below_threshold`,
   `excluded` (no track, or a hard eligibility flag), or `hold` (suspected prompt injection).
5. **Report** (`brief.build`) and an optional notification.

## The queue

`pool.queue`: ready postings, newest within `pool.stale_after_days`; track priority first; inside a track each
company's best posting comes before any company's second; then score; then posting date.

## Tailoring

`tailor.prepare` copies the job description into a posting folder; the plan (`plan.json`) is a list of declarative
edits against exact text of the master DOCX; `docx.apply` edits the document XML in place (text split across runs is
handled; everything else stays byte-identical); `tailor.lint` checks never-claim terms, placeholders, new numbers and
scope; the claims audit is an independent review. See `jobpilot/prompts/tailoring-guide.md`.

## Testing

`python3 -m unittest discover -s tests` runs everything offline: a fake `claude` binary (`tests/fakes/fake_claude.py`)
answers role runs from scripted responders, `apify.FakeApify` replaces the network, and `JOBPILOT_NOW` fixes the
clock.
