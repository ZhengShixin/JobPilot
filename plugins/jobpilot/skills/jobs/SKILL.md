---
name: jobs
description: Show and discuss the best-matching jobs in the JobPilot pool — the ranked queue, why each role scored as it did, its gaps — and help the user decide what to apply to, skip or save. Use when the user asks what to apply to, wants to see matches, or asks about a specific posting.
argument-hint: "[track or posting id]"
allowed-tools: Bash(jobpilot *), Read
---

# Browse the queue and decide

Reply in the user's language. JobPilot never applies for the user; it helps them choose and prepare.

1. If `$ARGUMENTS` is a posting id, go to step 3 for it. Otherwise run `jobpilot queue 15 --json` (add
   `--track <id>` when the user named a track) and show a compact table: #, score, track, company, title, location,
   posted (days ago), one-line reason. Mention how many more are in the queue (`jobpilot stats --json`).
2. Ask which ones to look at, or offer the top 3.
3. For a posting: `jobpilot pool show <id> --jd --json`. Explain the score from its four components (duties,
   background, skills, seniority) with the verbatim quotes, the gaps and any fit cap, and what in the JD matters
   most. Be honest about weak fits.
4. Act on the user's decision:
   - apply: offer `/jobpilot:tailor <id>`; when they have applied, `jobpilot app add --posting <id> --applied`;
   - save for later: `jobpilot app add --posting <id>` (stage "planned");
   - not interested: `jobpilot pool mark <id> skipped --note "<their reason>"` (the reason helps `/jobpilot:tune`);
   - unsure: `jobpilot pool mark <id> hold`.
5. A posting the user found elsewhere: ask for the URL and full text, then
   `jobpilot pool add --title ... --company ... --url ... --jd-file <saved text> --review` and report the result.
