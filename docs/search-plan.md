# The search plan

The full guide — how the LinkedIn jobs actor matches text (no stemming, phrases, `:*` prefixes, exact industry
names), the structured filters and their exact values, design patterns, and starting templates for common role
families — lives next to the skill that uses it:

**[plugins/jobpilot/skills/setup/reference/search-plan-guide.md](../plugins/jobpilot/skills/setup/reference/search-plan-guide.md)**

Workflow:

1. `/jobpilot:setup` (or `/jobpilot:tune`) writes `[search.groups.*]` in `jobpilot.toml` and examples in
   `profile/plan-examples.json`.
2. `jobpilot plan check` replays the examples through an offline emulation of the actor's matching
   (`plugins/jobpilot/jobpilot/querymatch.py`) and reports recall per track, paid-for negatives, dead groups,
   stemming traps, unknown input keys and invalid enum values. Free.
3. `jobpilot collect --dry-run` shows the exact actor input of every group and its cap. Free.
4. `jobpilot collect` (or `jobpilot daily`) runs it. After a few days, `jobpilot plan check` also shows the real rows
   per day and cost per group, and `/jobpilot:tune` uses what was dropped or rejected to tighten or widen the plan.
