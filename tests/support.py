"""Shared test helpers: a temporary workspace for a fictional candidate, a fake `claude` binary and fake Apify rows.

Every test runs offline (JOBPILOT_OFFLINE=1) against temporary folders; nothing touches the network or a real account.
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PLUGIN = REPO / "plugins" / "jobpilot"
FAKES = Path(__file__).resolve().parent / "fakes"
if str(PLUGIN) not in sys.path:
    sys.path.insert(0, str(PLUGIN))

os.environ["JOBPILOT_OFFLINE"] = "1"

CONFIG = """
[candidate]
name = "Jordan Rivera"
resume = "profile/resume.docx"
work_authorization = "permanent_resident"
seniority = ["entry", "mid"]
locations = ["United States"]
languages = ["English", "Spanish"]
schools = ["Lakeside State University", "Northfield Institute of Technology"]
min_salary_usd = 90000

[app]
language = "en"
timezone = "America/New_York"

[[tracks]]
id = "mle"
name = "Machine Learning Engineer"
priority = 1
min_score = 5.0
description = "Builds, trains, evaluates and ships machine-learning models and the systems around them."
categories = ["model_training", "ml_systems", "applied_ai", "other_ml"]

[[tracks]]
id = "ds"
name = "Data Scientist"
priority = 2
min_score = 5.5
description = "Statistical modeling, experimentation and predictive analytics that drive product decisions."
categories = ["modeling", "experimentation", "analytics", "other_ds"]

[search]
daily_budget_usd = 1.00
monthly_budget_usd = 20.00
run_time = "07:30"

[search.common]
aiEmploymentTypeFilter = ["FULL_TIME"]

[search.groups.mle-core]
track = "mle"
budget_usd = 0.40
titleSearch = ["Machine Learning Engineer", "ML Engineer", "Applied Scientist"]
titleExclusionSearch = ["Intern", "Internship", "Principal", "Director"]

[search.groups.ds-core]
track = "ds"
budget_usd = 0.30
titleSearch = ["Data Scientist"]
titleExclusionSearch = ["Intern", "Director"]
descriptionSearch = ["machine learning", "statistical model:*", "A/B test:*"]

[filters]
exclude_title_words = ["Sales", "Recruiter"]
protect_title_words = ["Machine Learning"]
drop_experience_levels = ["10+"]

[claims]
never_claim = ["Kubernetes", "Scala"]
"""

PROFILE = """# Profile: Jordan Rivera (fictional test candidate)

## Education
- M.S. Computer Science, Northfield Institute of Technology, 2023.
- B.S. Statistics, Lakeside State University, 2021.

## Experience
- Machine Learning Engineer, Brightline Analytics (Jun 2023 - present): trained gradient-boosted and transformer
  ranking models in PyTorch; built a feature pipeline in Python and SQL; cut inference latency by 38%.
- Data Science Intern, Harbor Retail (Summer 2022): A/B test analysis and demand forecasting in Python.

## Skills
- Python, PyTorch, scikit-learn, SQL, Docker, AWS SageMaker, Git.
- Statistics: hypothesis testing, causal inference basics, time-series forecasting.
"""

RUBRIC = """# Scoring profile (test)

## Rubrics by track (10 points each)

### Track `mle` — Machine Learning Engineer
- `duties_4` (0-4): training and shipping models 3.5-4; ML infrastructure 2.5-3; analytics without models 1.
- `background_3` (0-3): production ML experience 3; adjacent data work 2.
- `skills_2` (0-2): Python and PyTorch covered 2.
- `seniority_1` (0-1): 0-3 years 1; 5+ years 0.
- Fit caps: 7.5 for a required specialist skill not in the profile; 6.5 for 5+ years required.
- Categories: model_training, ml_systems, applied_ai, other_ml.

### Track `ds` — Data Scientist
- `duties_4` (0-4): predictive modeling 3.5-4; experimentation 3; reporting 1.
- `background_3` (0-3): statistics plus applied modeling 3.
- `skills_2` (0-2): Python and SQL 2.
- `seniority_1` (0-1): 0-3 years 1.
- Categories: modeling, experimentation, analytics, other_ds.

## Candidate facts (quote only from here)
- Machine Learning Engineer, Brightline Analytics (Jun 2023 - present): trained gradient-boosted and transformer ranking models in PyTorch.
- Built a feature pipeline in Python and SQL; cut inference latency by 38%.
- Data Science Intern, Harbor Retail (Summer 2022): A/B test analysis and demand forecasting in Python.
- Skills: Python, PyTorch, scikit-learn, SQL, Docker, AWS SageMaker, Git.
- M.S. Computer Science, Northfield Institute of Technology, 2023.
- About 2 years of industry experience.

## Never credit
- Kubernetes, Scala, Spark.
"""

CLAIMS = """# Claims policy
- Never claim Kubernetes, Scala or Spark.
- Keep the 38% latency figure exactly as stated.
"""


def make_ws(root=None, config_text=CONFIG):
    from jobpilot import workspace
    root = Path(root or tempfile.mkdtemp(prefix="jobpilot-test-"))
    ws, _ = workspace.init(root, name="Jordan Rivera")
    (ws.root / "jobpilot.toml").write_text(config_text)
    ws.profile_md.write_text(PROFILE)
    ws.rubric_md.write_text(RUBRIC)
    ws.claims_md.write_text(CLAIMS)
    ws.reload()
    return ws


JD_MLE = """About the role
We are hiring a Machine Learning Engineer to train, evaluate and deploy ranking models for our marketplace.
You will build model training pipelines in PyTorch and ship models to production with our platform team.
Requirements: 2+ years of experience building machine learning models in Python and PyTorch.
Experience with SQL and cloud ML platforms such as AWS SageMaker is a plus.
This is a full-time role based in New York, NY or remote within the United States.
"""

JD_DS = """Data Scientist, Growth
Design and analyze A/B tests, build statistical models of customer behavior and forecast demand.
You will partner with product managers and present findings. Requirements: strong SQL and Python,
experience with hypothesis testing and regression. 1-3 years of experience. Full-time, Chicago, IL.
"""

JD_INTERN = """Machine Learning Engineer Intern (Summer 2027). Must be currently enrolled in a degree program and
returning to school after the internship. Train models in PyTorch."""

JD_CITIZEN = """Machine Learning Engineer. Build computer-vision models for defense customers. Must be a U.S. citizen
and hold an active Secret clearance. Python, PyTorch, C++. Full-time role in Arlington, VA."""


def item(pid, title, company, jd, *, employment="FULL_TIME", experience="2-5", salary_max=None, slug=None,
         posted="2026-10-18T12:00:00", location="New York, New York, United States"):
    """One actor row in the fantastic-jobs output format."""
    row = {"id": int(pid) + 1, "linkedin_id": int(pid), "title": title, "organization": company,
           "organization_url": "https://www.linkedin.com/company/%s" % (slug or company.lower().replace(" ", "-")),
           "org_linkedin_slug": slug or company.lower().replace(" ", "-"), "date_posted": posted,
           "locations_derived": [location], "employment_type": [employment], "ai_employment_type": [employment],
           "ai_experience_level": experience, "description_text": jd, "url": "https://www.linkedin.com/jobs/view/%s" % pid,
           "org_linkedin_industry": "Technology, Information and Internet", "seniority": "Associate",
           "ai_work_arrangement": "Hybrid"}
    if salary_max:
        row.update(ai_salary_currency="USD", ai_salary_max_value=salary_max, ai_salary_min_value=salary_max * 0.8,
                   ai_salary_unit_text="YEAR")
    return row


def fake_claude_env(responses):
    """Environment for the fake `claude` binary: `responses` maps a role name to a Python file path or a dict."""
    d = Path(tempfile.mkdtemp(prefix="fake-claude-"))
    for role, value in responses.items():
        if isinstance(value, (str, Path)) and str(value).endswith(".py"):
            (d / ("%s.py" % role)).write_text(Path(value).read_text())
        elif isinstance(value, str):
            (d / ("%s.py" % role)).write_text(value)
        else:
            (d / ("%s.json" % role)).write_text(json.dumps(value))
    return {"JOBPILOT_CLAUDE": str(FAKES / "fake_claude.py"), "FAKE_CLAUDE_DIR": str(d)}


class EnvCase(unittest.TestCase):
    """A TestCase that restores os.environ after each test."""

    def setUp(self):
        self._env = dict(os.environ)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)

    def env(self, **kw):
        os.environ.update({k: str(v) for k, v in kw.items()})
