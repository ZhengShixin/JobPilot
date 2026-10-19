---
name: track
description: Record job-application progress in JobPilot's ledger from plain language — applied, received, online assessment (with deadline), interview (with time), offer, rejection, withdrawal, referral, notes — and list what is due. Use when the user reports any application update or asks what is due.
argument-hint: "<what happened, e.g. 'got an OA from Acme due Friday'>"
allowed-tools: Bash(jobpilot *)
---

# Record application progress

Reply in the user's language. The ledger is append-only; never edit data files directly.

1. Understand the update in `$ARGUMENTS` (or ask): which company/role, what happened, and when. Resolve relative
   dates ("Friday", "tomorrow 2pm") against today's date and the workspace time zone, and confirm them if ambiguous.
   Never invent a date or a time.
2. Find the application: `jobpilot app list --all --json`. If none matches, look for the posting
   (`jobpilot pool list --json --limit 200` filtered by company) and create it:
   `jobpilot app add --posting <id> [--applied --date YYYY-MM-DD]`, or without a posting:
   `jobpilot app add --company "<c>" --role "<r>" --url "<u>" [--applied]`. If two applications at the same company
   could match, ask which one.
3. Record the event with `jobpilot app event <A###> <event> [--date] [--deadline] [--time] [--note] [--referrer]`:
   applied, receipt, referral (--referrer initials), followup_sent, recruiter_reply, oa_invited (--deadline),
   oa_done, interview_invited, interview_scheduled (--time ISO, e.g. 2026-11-03T14:00), interview_done, offer,
   rejected, withdrawn, closed (no response), reopened, note, void (recorded by mistake; only before any progress).
4. Confirm in one line (application id, new stage). If asked what is due: `jobpilot app due --json` and list
   deadlines, interviews and follow-ups (applied N days ago, no answer) in date order.
