"""JobPilot: an AI job-search copilot for Claude Code.

A deterministic core (this package, standard library only) does everything that must be exact: paid searches under
budget caps, filtering, evidence checks on every AI score, the job pool, the application ledger and the dashboard.
The judgment calls (reading a job description, tailoring a resume, writing outreach) go to one-shot Claude runs whose
answers must fit a JSON schema and quote their evidence verbatim, or to the Claude Code session you are working in.
"""

__version__ = "0.1.0"
