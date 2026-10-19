# Troubleshooting

Start with `jobpilot doctor`: it checks Python, the workspace, the config, the Claude CLI, the Apify token (without
showing it), LibreOffice, the ledgers' hash chains, the schedule and the pause switch.

**"no JobPilot workspace here"** — run commands inside your workspace folder (or pass `--workspace <dir>`, or set
`JOBPILOT_WORKSPACE`). Start Claude Code in that folder so the slash commands find it.

**"JobPilot needs Python 3.11 or newer"** — macOS ships Python 3.9. Install a newer one (`brew install python@3.12`
or `uv python install 3.12`) or point `JOBPILOT_PYTHON` at it.

**No Apify token** — create one at https://console.apify.com/settings/integrations and run `./jobpilot token set` in
your own terminal (hidden prompt). Or export `APIFY_TOKEN`, or put `APIFY_TOKEN=...` in the workspace `.env`.

**A group is "skipped (budget)"** — today's cap is spent; it is min(`daily_budget_usd`, what the month has left
divided by the days left). Raise the budgets or lower the groups' `budget_usd`.

**"launch outcome unknown"** — a network error hit while starting a paid run. JobPilot never retries that request
(it might have started a run). The next `jobpilot collect` adopts the real run by its start time, or after 30
minutes records it as not started; until then no other paid run starts. Check the Apify console if in doubt.

**A group is "truncated"** — it returned as many rows as `search.limit` or spent its whole `budget_usd`. Narrow the
group (exclusions, description terms) or raise its budget.

**Review batches fail** — `jobpilot review` prints the reason. `claude_not_found`: install Claude Code and log in.
`claude_error`/`timeout`: usage limits or a slow model; it retries in the next run (`review.max_attempts`).
`rejected: ... quote not found verbatim`: the model paraphrased evidence; the posting is retried.

**Scores marked stale after editing rubric.md** — expected: scores are keyed to the rubric's hash. `jobpilot review`
rescores up to `review.rescore_per_run` per run.

**Tailoring: "find text not found / not unique"** — every edit must quote one master paragraph exactly; run
`jobpilot resume paragraphs` to copy the text. **No PDF** — install LibreOffice, or export the DOCX yourself.

**Dashboard asks for a key** — open the link `jobpilot dashboard` prints (`http://127.0.0.1:<port>/?key=...`); the
key is in `data/dashboard.token`.

**Scheduled runs do nothing** — `jobpilot schedule status`, then `data/logs/daily.log`. On Linux, cron needs
`CLAUDE_CODE_OAUTH_TOKEN` or `ANTHROPIC_API_KEY` for the review; `claude` and `python3` must be on the PATH written
into the crontab line (reinstall after moving them).

**Stop everything now** — `jobpilot pause` (no paid runs until `jobpilot unpause`); `jobpilot schedule remove` turns
off the daily run.
