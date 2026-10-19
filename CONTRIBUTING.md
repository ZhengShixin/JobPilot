# Contributing

Thanks for helping! A few rules keep JobPilot safe and easy to install:

- **Standard library only** in `plugins/jobpilot/` (Python 3.11+; `hooks/guard.py` stays 3.8-compatible). No new
  runtime dependencies.
- **Tests run offline.** `python3 -m unittest discover -s tests` must pass without network access or accounts. Use
  `apify.FakeApify`, the fake `claude` binary (`tests/support.fake_claude_env`) and `JOBPILOT_NOW`.
- **The model proposes, code checks.** New AI roles answer through a JSON schema, take external text inside
  `claude.untrusted(...)`, and have their output validated in code before anything is stored.
- **Money is capped.** Any new paid call reserves budget first, carries a hard cap, and is never retried blindly.
- **Never act for the user.** No submitting, sending, messaging or logging in.
- **Privacy.** Fixtures and examples use fictional people and companies only.
- Validate the plugin: `claude plugin validate ./plugins/jobpilot --strict` and `claude plugin validate . --strict`.
- Bump `version` in `plugins/jobpilot/.claude-plugin/plugin.json`, `.claude-plugin/marketplace.json`,
  `pyproject.toml` and `plugins/jobpilot/jobpilot/__init__.py` together for a release, and add a CHANGELOG entry.
- Landing page: `python3 site/build.py` writes `site/dist/jobpilot.html` (fictional demo data and the
  screenshots in `docs/images`, all inlined).
