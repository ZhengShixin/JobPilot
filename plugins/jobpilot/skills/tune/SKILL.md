---
name: tune
description: Improve JobPilot's search plan, filters and scoring rubrics from real results and the user's feedback — find missed roles, cut paid-for noise, fix stemming traps, adjust thresholds — and re-check everything offline before it costs money. Use when the user says the matches are wrong, too few, too many, too senior, or wants a new track.
argument-hint: "[what is wrong, e.g. 'too many senior roles']"
allowed-tools: Bash(jobpilot *), Read, Write, Edit, AskUserQuestion
---

# Tune the search and the scoring

Reply in the user's language. Read `${CLAUDE_PLUGIN_ROOT}/skills/setup/reference/search-plan-guide.md` (matching
rules) and `${CLAUDE_PLUGIN_ROOT}/skills/setup/reference/rubric-guide.md` before changing anything.

1. Gather evidence: `jobpilot plan check --json` (recall on examples, rows/day and cost per group from real runs),
   `jobpilot stats --json`, and samples of what was dropped or rejected:
   `jobpilot pool list --status dropped --json --limit 40`, `--status excluded`, `--status below_threshold`, and the
   user's skips (`--status skipped`, their notes say why).
2. Diagnose against the user's complaint (`$ARGUMENTS`):
   - missing roles -> which titles/descriptions were not matched (add variants or a group; check stemming traps);
   - paid-for noise -> groups with many dropped/excluded rows (add title exclusions, narrow description terms,
     industry filters, or lower the group's budget);
   - wrong scores -> rubric anchors or thresholds (`min_score` per track); wrongly dropped -> loosen a filter rule.
3. Propose concrete edits (old -> new) and the expected effect on cost. Apply them after the user agrees: Edit
   jobpilot.toml / rubric.md, and add the new cases to `profile/plan-examples.json` (missed titles as positives,
   noise as negatives) so the check keeps them fixed.
4. `jobpilot plan check` and `jobpilot config check` until clean. Note that editing rubric.md makes existing scores
   stale: they are rescored gradually (`review.rescore_per_run` per day; `jobpilot review` to do it now, at the cost
   of Claude usage).
5. Summarize what changed and when it takes effect (the next `jobpilot daily`).
