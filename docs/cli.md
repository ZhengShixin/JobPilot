# CLI reference

Inside Claude Code the plugin puts `jobpilot` on the Bash tool's PATH; in a terminal use `./jobpilot` in your
workspace (or `pipx install` the package). Every command accepts `--json` for machine-readable output and
`--help` for its options; the global `--workspace DIR` picks a workspace other than the current folder.

## Setup and health

### `jobpilot init`

Create a workspace.

```text
jobpilot init [-h] [--json] [--name NAME] [--lang {en,zh}] [dir]
```

### `jobpilot doctor`

Check the installation and the workspace.

```text
jobpilot doctor [-h] [--json]
```

### `jobpilot config`

Check or show jobpilot.toml.

```text
jobpilot config [-h] [--json] {check,show}
```

### `jobpilot token`

Apify token: set (hidden prompt), status, clear.

```text
jobpilot token [-h] [--json] {set,status,clear}
```

### `jobpilot pause`

Refuse every paid run until `jobpilot unpause`.

```text
jobpilot pause [-h] [--json] [reason]
```

### `jobpilot unpause`

Allow paid runs again.

```text
jobpilot unpause [-h] [--json]
```

### `jobpilot version`

Print the version.

```text
jobpilot version [-h] [--json]
```

### `jobpilot demo`

Create a demo workspace with fictional data.

```text
jobpilot demo [-h] [--json] [--lang {en,zh}] [--force] [--export FILE] dir
```

## Search and review

### `jobpilot plan`

The search plan: show, or check it offline against example titles.

```text
jobpilot plan [-h] [--json] [--examples EXAMPLES] {show,check}
```

### `jobpilot collect`

Run today's search on Apify (paid, capped).

```text
jobpilot collect [-h] [--json] [--dry-run] [--groups [GROUPS ...]] [--again] [--max-seconds MAX_SECONDS]
```

### `jobpilot review`

Coarse filter + AI review of new postings.

```text
jobpilot review [-h] [--json] [--max-batches MAX_BATCHES] [--ids [IDS ...]] [--no-rescore]
```

### `jobpilot daily`

Collect, review, report and notify (what the schedule runs).

```text
jobpilot daily [-h] [--json] [--no-collect] [--no-review] [--quiet]
```

### `jobpilot brief`

Write today's report.

```text
jobpilot brief [-h] [--json] [--date DATE]
```

### `jobpilot events`

The activity feed.

```text
jobpilot events [-h] [--json] [-n N]
```

### `jobpilot stats`

Pool, funnel, budget and due items.

```text
jobpilot stats [-h] [--json]
```

## Pool and applications

### `jobpilot queue`

Postings ready to apply, best first.

```text
jobpilot queue [-h] [--json] [--track TRACK] [n]
```

### `jobpilot pool`

List, show, mark or add postings.

```text
jobpilot pool list [-h] [--json] [--status {pending,dropped,excluded,below_threshold,ready,applied,skipped,hold,stale}] [--track TRACK] [--limit LIMIT]
jobpilot pool show [-h] [--json] [--jd] id
jobpilot pool mark [-h] [--json] [--note NOTE] id {ready,skipped,hold,applied}
jobpilot pool add [-h] [--json] --title TITLE --company COMPANY [--url URL] [--location LOCATION] [--track TRACK] [--jd-file JD_FILE] [--review]
```

### `jobpilot app`

Applications: add, event, list, show, due.

```text
jobpilot app add [-h] [--posting POSTING] [--company COMPANY] [--role ROLE] [--url URL] [--track TRACK] [--note NOTE] [--applied] [--date DATE] [--json]
jobpilot app event [-h] [--date DATE] [--deadline DEADLINE] [--time TIME] [--minutes MINUTES] [--note NOTE] [--referrer REFERRER] [--json] app_id {tailored,applied,receipt,referral,followup_sent,recruiter_reply,oa_invited,oa_done,interview_invited,interview_scheduled,interview_done,offer,rejected,withdrawn,closed,reopened,edited,note,void}
jobpilot app list [-h] [--all] [--json]
jobpilot app show [-h] [--json] app_id
jobpilot app due [-h] [--json]
```

## Resumes

### `jobpilot resume`

Read resumes: text, paragraphs, build-docx.

```text
jobpilot resume text [-h] [--json] file
jobpilot resume paragraphs [-h] [--json] [file]
jobpilot resume build-docx [-h] [--kind {resume,letter}] [--json] markdown out
```

### `jobpilot tailor`

Tailored resumes: prepare, build, auto, audit, show, lint, list.

```text
jobpilot tailor prepare [-h] [--jd-file JD_FILE] [--company COMPANY] [--role ROLE] [--url URL] [--json] [posting]
jobpilot tailor build [-h] [--json] folder
jobpilot tailor auto [-h] [--cover-letter] [--model MODEL] [--json] posting
jobpilot tailor audit [-h] [--model MODEL] [--json] folder
jobpilot tailor show [-h] [--json] folder
jobpilot tailor lint [-h] [--json] file
jobpilot tailor list [-h] [--json]
```

## Referrals and inbox

### `jobpilot net`

Alumni referral finder: find, score, cards, run, list, show, mark, due.

```text
jobpilot net find [-h] [--json] [--url URL] [--n N] [--titles [TITLES ...]] [--dry-run] company
jobpilot net score [-h] [--json] company
jobpilot net cards [-h] [--json] [--n N] company
jobpilot net run [-h] [--json] [--url URL] [--n N] [--cards CARDS] [--titles [TITLES ...]] company
jobpilot net list [-h] [--json] [--all] [company]
jobpilot net companies [-h] [--json]
jobpilot net show [-h] [--json] pid
jobpilot net mark [-h] [--json] [--note NOTE] pid {connect_sent,accepted,message_sent,replied,chat_scheduled,chat_done,referred,declined,no_response,dropped,undo}
jobpilot net remove [-h] [--json] [--reason REASON] company
jobpilot net restore [-h] [--json] company
jobpilot net due [-h] [--json]
```

### `jobpilot inbox`

Gmail progress tracking: brief, apply, link, unmatched, status, scan.

```text
jobpilot inbox brief [-h] [--json]
jobpilot inbox apply [-h] [--json] file
jobpilot inbox link [-h] [--json] message_id app_id
jobpilot inbox dismiss [-h] [--json] message_id
jobpilot inbox unmatched [-h] [--json]
jobpilot inbox status [-h] [--json]
jobpilot inbox scan [-h] [--json] [--model MODEL]
```

## Dashboard and automation

### `jobpilot dashboard`

Start the local dashboard.

```text
jobpilot dashboard [-h] [--json] [--port PORT] [--no-open] [--export FILE]
```

### `jobpilot schedule`

Daily automation: install, remove, status.

```text
jobpilot schedule [-h] [--json] [--time TIME] {install,remove,status}
```
