# Configuration reference (`jobpilot.toml`)

`/jobpilot:setup` writes this file from your resume and answers; you can edit it by hand at any time
(`jobpilot config check` validates it, `jobpilot plan check` replays the search plan offline). Defaults live in
`plugins/jobpilot/jobpilot/config.py`; any key you leave out takes its default.

## `[candidate]`

| key | default | meaning |
|---|---|---|
| `name` | "" | your name (resume file names, outreach sign-off) |
| `resume` | "" | the master resume, e.g. `profile/resume.docx` |
| `work_authorization` | "" | `us_citizen`, `permanent_resident`, `work_visa`, `needs_sponsorship` or `other`; drives the citizenship and sponsorship rules |
| `seniority` | [] | target levels: `entry`, `mid`, `senior`, `lead` |
| `locations` | ["United States"] | Apify `locationSearch` values ("New York, New York, United States", "London, England, United Kingdom", ...) |
| `work_arrangements` | [] | any of `On-site`, `Hybrid`, `Remote OK`, `Remote Solely` ([] = any) |
| `min_salary_usd` | 0 | drop postings whose advertised annual maximum is lower (0 = off) |
| `languages` | ["English"] | languages you work in (others required by a JD exclude it) |
| `schools` | [] | official school names, for the referral finder |
| `open_to_internships`, `open_to_contract` | false | keep internships / contract roles |

## `[app]`

`language` = `en` | `zh` (dashboard and reports), `timezone` = IANA name (default: this machine's).

## `[[tracks]]` (one block per role family)

| key | meaning |
|---|---|
| `id` | short lowercase id (`mle`, `ds`, `swe`, `qr`, ...); also the rubric's section name |
| `name` | display name |
| `priority` | 1 = first choice; equal numbers rank equal; the queue shows lower numbers first |
| `min_score` | admission threshold on that track's 10-point rubric (default 5.0) |
| `description` | one sentence: the core duties that put a role on this track (the reviewer uses it) |
| `categories` | duty categories of the track (must match `rubric.md`) |

## `[search]`

| key | default | meaning |
|---|---|---|
| `actor` | `fantastic-jobs/advanced-linkedin-job-search-api` | the Apify actor |
| `daily_budget_usd`, `monthly_budget_usd` | 1.00, 25.00 | hard caps; today's cap = min(daily, month left / days left) |
| `run_time` | "07:30" | time of the scheduled daily run |
| `lookback_hours`, `first_lookback_hours`, `max_lookback_days` | 36, 72, 7 | the search window (catch-up after missed days) |
| `max_concurrent_runs` | 3 | Apify runs at the same time |
| `limit` | 1000 | maximum rows per run (a run that hits it is flagged as truncated) |
| `run_timeout_seconds`, `memory_mb` | 900, 1024 | actor run options |
| `time_range` | "7d" | the actor's index range; `datePostedAfter` narrows it to the window |

`[search.common]` — actor filters merged into every group (e.g. `aiEmploymentTypeFilter = ["FULL_TIME"]`).

`[search.groups.<name>]` — one Apify run per group per day:

| key | meaning |
|---|---|
| `track` | the track this group searches for |
| `budget_usd` | the run's `maxTotalChargeUsd` |
| `enabled` | false skips the group |
| any actor input | `titleSearch`, `titleExclusionSearch`, `descriptionSearch`, `industryFilter`, `industryExclusionFilter`, `organizationSearch`, `aiExperienceLevelFilter`, `seniorityFilter`, `removeAgency`, ... — see [search-plan.md](search-plan.md) |

## `[filters]` (the free coarse filter)

| key | default | meaning |
|---|---|---|
| `exclude_employment_types` | INTERN, PART_TIME, TEMPORARY, VOLUNTEER, PER_DIEM | dropped employment types |
| `exclude_title_words` | [] | whole words/phrases (`word:*` = prefix) that drop a title... |
| `protect_title_words` | [] | ...unless the title also has one of these |
| `exclude_companies` | [] | companies you never want |
| `drop_experience_levels` | [] | provider experience bands to drop: `0-2`, `2-5`, `5-10`, `10+` |
| `skip_training_gigs` | true | drop AI-training gig platforms |
| `skip_agencies` | false | drop recruitment agencies |
| `drop_citizenship_required`, `drop_no_sponsorship` | auto | override what `work_authorization` implies |

## `[review]`

`model` ("sonnet"), `effort` (""), `batch_size` (8, max 10), `max_batches` per run (25), `timeout_seconds` (600),
`max_attempts` (3), `max_jd_chars` (14000), `rescore_per_run` (40 stale scores rescored per run).

## `[pool]`

`stale_after_days` (45): older postings leave the queue.

## `[tailor]` and `[claims]`

`tailor.master` (default: `candidate.resume`), `tailor.model` ("opus", headless tailoring), `tailor.audit_model`
("" = the same as `tailor.model`, the claims audit), `max_changed_bullets` (6), `pdf` (true, needs LibreOffice),
`timeout_seconds` (900).
`claims.never_claim` — terms that must not appear in a tailored resume unless the master already has them;
`claims.banned_phrases` — phrases that are never allowed.

## `[referrals]`

`actor` (`harvestapi/linkedin-company-employees`), `scraper_mode` ("Full ($8 per 1k)"), `locations`,
`profiles_per_company` (30), `cards_per_company` (5), `budget_usd_per_company` (0.40), `linkedin_plan` (`free`:
200-character notes, `premium`: 300), `message_limit` (700), `sign_name`, `model`, `writer_model`.

## `[inbox]`, `[followup]`, `[dashboard]`, `[notify]`

`inbox.lookback_days` (14) · `followup.after_days` (10: an application without an answer after this many days is
suggested for a follow-up) · `dashboard.port` (8765) · `notify.command` (a shell command run after each daily run;
`{title}` and `{text}` are replaced, shell-quoted), e.g.
`command = "osascript -e 'display notification {text} with title {title}'"` or
`command = "curl -s -d {text} ntfy.sh/<your-topic>"`.
