"""A demo workspace with fictional data (`jobpilot demo <dir>`): every screen of the dashboard filled in, nothing real.

The candidate, the companies, the people and the postings are invented, and posting links point at example.com.
Reviews are built the way the real pipeline builds them: every quote really occurs in its job description or in the
demo rubric, scoring.validate computes every score and review.apply_review stores it. Dates are relative to now, so
the demo always looks current. Paid actions (search, tailoring, referral searches) are turned off in it (data/DEMO).

    create(dir, lang="en", force=False) -> Workspace
"""
import json
import os
import random
import shutil
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import brief, config, ledger, review, scoring, util, workspace

CONFIG = """# JobPilot demo workspace. Every person, company and posting in it is fictional.

[candidate]
name = "Jordan Rivera"
resume = "profile/resume.md"
work_authorization = "permanent_resident"
seniority = ["entry", "mid"]
locations = ["United States"]
min_salary_usd = 110000
languages = ["English", "Spanish"]
schools = ["Lakeside State University", "Northfield Institute of Technology"]

[app]
language = "{lang}"
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
priority = 1
min_score = 5.0
description = "Statistical modeling, experimentation and forecasting that drive product and business decisions."
categories = ["modeling", "experimentation", "forecasting", "other_ds"]

[[tracks]]
id = "as"
name = "Applied Scientist"
priority = 2
min_score = 5.5
description = "Adapts and evaluates ML methods for a product: ranking, recommendations, language and vision models."
categories = ["ranking_recsys", "nlp_llm", "vision", "other_science"]

[search]
daily_budget_usd = 1.00
monthly_budget_usd = 25.00
run_time = "07:30"

[search.common]
aiEmploymentTypeFilter = ["FULL_TIME"]

[search.groups.mle-core]
track = "mle"
budget_usd = 0.35
titleSearch = ["Machine Learning Engineer", "ML Engineer", "MLOps Engineer", "Machine Learning Platform"]
titleExclusionSearch = ["Intern", "Internship", "Principal", "Staff", "Director", "Manager"]

[search.groups.mle-applied]
track = "mle"
budget_usd = 0.20
titleSearch = ["Applied ML", "Computer Vision Engineer", "NLP Engineer", "LLM Engineer"]
titleExclusionSearch = ["Intern", "Principal", "Director"]

[search.groups.ds-core]
track = "ds"
budget_usd = 0.25
titleSearch = ["Data Scientist", "Decision Scientist"]
titleExclusionSearch = ["Intern", "Director", "Principal"]
descriptionSearch = ["machine learning", "statistical model:*", "A/B test:*", "forecast:*", "causal inference"]

[search.groups.as-core]
track = "as"
budget_usd = 0.15
titleSearch = ["Applied Scientist", "Machine Learning Scientist", "Research Scientist"]
titleExclusionSearch = ["Intern", "Principal", "Clinical", "Chemist"]
descriptionSearch = ["recommendation:*", "ranking", "large language model:*", "computer vision"]

[filters]
exclude_title_words = ["Sales", "Recruiter", "Account Executive"]
protect_title_words = ["Machine Learning", "Data Scientist"]
drop_experience_levels = ["10+"]

[claims]
never_claim = ["Kubernetes", "Scala", "Spark"]

[referrals]
linkedin_plan = "free"
sign_name = "Jordan"
"""

PROFILE = """# Jordan Rivera — profile (fictional demo candidate)

The only fact source for every AI step. Every line here is invented for the JobPilot demo.

## Education
- M.S. Computer Science, Northfield Institute of Technology, 2021–2023. Thesis: calibrated ranking under position bias.
- B.S. Statistics, Lakeside State University, 2017–2021. Minor in mathematics.

## Experience
- Machine Learning Engineer, Brightline Analytics (Jun 2023 – present)
  - Trained gradient-boosted and transformer ranking models in PyTorch for a marketplace search product.
  - Built a Python and SQL feature pipeline serving 40 million rows a day.
  - Cut p95 inference latency by 38% with model distillation and batching.
  - Ran weekly A/B tests with the product team and wrote the analysis notebooks.
- Data Science Intern, Harbor Retail (May – Aug 2022)
  - Built demand-forecasting models for 1,200 stores in Python.
  - Analyzed A/B tests for checkout changes.

## Projects
- Fine-tuned a small transformer for support-ticket classification (course project, F1 0.91).
- Contributed learning-to-rank evaluation metrics to an open-source Python library.

## Skills
- Python, SQL, PyTorch, scikit-learn, LightGBM, pandas, Docker, AWS SageMaker, Airflow, Git.
- Learning to rank, gradient boosting, transformers, A/B testing, time-series forecasting, causal inference basics.

## Work authorization
- U.S. permanent resident; no sponsorship needed.
"""

RUBRIC = """# Scoring profile (demo, fictional candidate)

Every score in the demo is keyed to this file. Quote candidate facts only from the "Candidate facts" section.

## Rubrics by track (10 points each)

### Track `mle` — Machine Learning Engineer
- `duties_4` (0–4): training and shipping models end to end 3.5–4; ML systems and serving 2.5–3; applications on
  hosted models without training 2; software around ML without model work lower.
- `background_3` (0–3): production ML work like the candidate's ranking models 3; adjacent data work 2; distant 1.
- `skills_2` (0–2): Python and PyTorch plus the serving stack covered 2; partly covered 1–1.5.
- `seniority_1` (0–1): 0–3 years 1; 3–5 years 0.5; 5+ years 0.
- Fit caps: 7.5 for a required specialist skill not in the profile; 6.5 for 5+ years required; 5.5 for people
  management.
- Categories: model_training, ml_systems, applied_ai, other_ml.

### Track `ds` — Data Scientist
- `duties_4` (0–4): predictive modeling 3.5–4; experimentation and causal inference 3; forecasting 3.5; reporting 1.
- `background_3` (0–3): statistics plus applied modeling 3; analytics only 1.5.
- `skills_2` (0–2): Python and SQL 2.
- `seniority_1` (0–1): 0–3 years 1; 3–5 years 0.5; 5+ years 0.
- Fit caps: 7.5 for a required specialist skill not in the profile; 6.5 for 5+ years required.
- Categories: modeling, experimentation, forecasting, other_ds.

### Track `as` — Applied Scientist
- `duties_4` (0–4): ranking and recommendation science 4; language-model adaptation 3.5; vision 3.
- `background_3` (0–3): ranking models and learning-to-rank work 3; general ML 2; a PhD-only research record 1.
- `skills_2` (0–2): Python and PyTorch 2.
- `seniority_1` (0–1): open to M.S. graduates with 0–3 years 1; PhD required 0.
- Fit caps: 7.5 for a required specialist skill not in the profile; 6.5 for a PhD with publications required.
- Categories: ranking_recsys, nlp_llm, vision, other_science.

## Candidate facts (quote only from here)
- Machine Learning Engineer, Brightline Analytics (Jun 2023 - present): trained gradient-boosted and transformer ranking models in PyTorch for a marketplace search product.
- Built a Python and SQL feature pipeline serving 40 million rows a day.
- Cut p95 inference latency by 38% with model distillation and batching.
- Ran weekly A/B tests with the product team and wrote the analysis notebooks.
- Data Science Intern, Harbor Retail (2022): built demand-forecasting models for 1,200 stores in Python.
- Skills: Python, SQL, PyTorch, scikit-learn, LightGBM, pandas, Docker, AWS SageMaker, Airflow, Git.
- Methods: learning to rank, gradient boosting, transformers, A/B testing, time-series forecasting, causal inference basics.
- M.S. Computer Science, Northfield Institute of Technology, 2023; B.S. Statistics, Lakeside State University, 2021.
- About 2 years of full-time industry experience (since June 2023).
- Fine-tuned a small transformer for support-ticket classification (course project).

## Never credit
- Kubernetes, Scala, Spark, people management, a PhD, publications.
"""

CLAIMS = """# Claims policy (demo)
- Never claim Kubernetes, Scala or Spark.
- Keep the 38% latency figure and the 40 million rows exactly as stated.
- The transformer classifier was a course project, not production work.
"""

RESUME = """# Jordan Rivera
Austin, TX · jordan.rivera@example.com · (555) 010-0199 · github.com/example

## Experience
### Machine Learning Engineer — Brightline Analytics | Jun 2023 – Present
- **Ranking Models:** Trained gradient-boosted and transformer ranking models in PyTorch for a marketplace search product
- **Feature Pipeline:** Built a Python and SQL feature pipeline serving 40 million rows a day
- **Inference Latency:** Cut p95 inference latency by 38% with model distillation and batching
- **Experimentation:** Ran weekly A/B tests with the product team and wrote the analysis notebooks

### Data Science Intern — Harbor Retail | May 2022 – Aug 2022
- **Demand Forecasting:** Built demand-forecasting models for 1,200 stores in Python
- **Checkout Tests:** Analyzed A/B tests for checkout changes

## Education
### M.S. Computer Science — Northfield Institute of Technology | 2021 – 2023
### B.S. Statistics — Lakeside State University | 2017 – 2021

## Skills
- **Languages and tools:** Python, SQL, PyTorch, scikit-learn, LightGBM, pandas, Docker, AWS SageMaker, Airflow, Git
- **Methods:** Learning to rank, gradient boosting, transformers, A/B testing, time-series forecasting
"""

FACTS = {
    "rank": "trained gradient-boosted and transformer ranking models in PyTorch",
    "pipeline": "Built a Python and SQL feature pipeline serving 40 million rows a day",
    "latency": "Cut p95 inference latency by 38% with model distillation and batching",
    "ab": "Ran weekly A/B tests with the product team",
    "forecast": "built demand-forecasting models for 1,200 stores in Python",
    "skills_ml": "Python, SQL, PyTorch, scikit-learn",
    "skills_ops": "Docker, AWS SageMaker, Airflow, Git",
    "skills_ds": "Python, SQL, PyTorch, scikit-learn, LightGBM, pandas",
    "methods": "learning to rank, gradient boosting, transformers, A/B testing",
    "methods_ds": "A/B testing, time-series forecasting, causal inference basics",
    "degree": "M.S. Computer Science, Northfield Institute of Technology, 2023",
    "years": "About 2 years of full-time industry experience",
    "nlp": "Fine-tuned a small transformer for support-ticket classification",
}

# name: (industry, size, city, arrangement)
COMPANIES = {
    "Northwind Analytics": ("Software Development", "201-500 employees", "Seattle, Washington", "Hybrid"),
    "Acme Robotics": ("Robotics Engineering", "501-1,000 employees", "Pittsburgh, Pennsylvania", "On-site"),
    "Bluefin Capital": ("Financial Services", "201-500 employees", "New York, New York", "Hybrid"),
    "Lumen Health AI": ("Hospitals and Health Care", "51-200 employees", "Boston, Massachusetts", "Hybrid"),
    "Vertex Mobility": ("Motor Vehicle Manufacturing", "1,001-5,000 employees", "Detroit, Michigan", "Hybrid"),
    "Cobalt Systems": ("Software Development", "1,001-5,000 employees", "Austin, Texas", "Hybrid"),
    "Harborview Bank": ("Banking", "5,001-10,000 employees", "Charlotte, North Carolina", "Hybrid"),
    "Pinecrest Insurance": ("Insurance", "1,001-5,000 employees", "Hartford, Connecticut", "Hybrid"),
    "Orbit Commerce": ("Retail", "1,001-5,000 employees", "San Francisco, California", "Hybrid"),
    "Aurora Climate": ("Environmental Services", "51-200 employees", "Denver, Colorado", "Remote OK"),
    "Summit Payments": ("Financial Services", "501-1,000 employees", "New York, New York", "Hybrid"),
    "Redwood Logistics": ("Transportation, Logistics, Supply Chain and Storage", "1,001-5,000 employees",
                          "Chicago, Illinois", "Hybrid"),
    "Silverline Media": ("Entertainment Providers", "201-500 employees", "Los Angeles, California", "Hybrid"),
    "Granite Health": ("Hospitals and Health Care", "10,001+ employees", "Nashville, Tennessee", "Hybrid"),
    "Atlas Mapping": ("Software Development", "501-1,000 employees", "San Jose, California", "Hybrid"),
    "Nimbus Cloud": ("IT Services and IT Consulting", "201-500 employees", "Remote", "Remote Solely"),
    "Juniper Travel": ("Travel Arrangements", "501-1,000 employees", "Miami, Florida", "Hybrid"),
    "Kestrel Security": ("Computer and Network Security", "201-500 employees", "Arlington, Virginia", "On-site"),
    "Polaris Retail": ("Retail", "10,001+ employees", "Minneapolis, Minnesota", "Hybrid"),
    "Lattice Labs": ("Research Services", "51-200 employees", "Cambridge, Massachusetts", "On-site"),
    "Copperleaf Foods": ("Food and Beverage Manufacturing", "1,001-5,000 employees", "Kansas City, Missouri", "Hybrid"),
    "Evergreen Credit": ("Financial Services", "501-1,000 employees", "Portland, Oregon", "Remote OK"),
    "Starling Ads": ("Advertising Services", "201-500 employees", "New York, New York", "Hybrid"),
    "Beacon Learning": ("E-Learning Providers", "51-200 employees", "Remote", "Remote Solely"),
    "Tidewater Energy": ("Utilities", "1,001-5,000 employees", "Houston, Texas", "Hybrid"),
    "Meridian Freight": ("Transportation, Logistics, Supply Chain and Storage", "501-1,000 employees",
                         "Memphis, Tennessee", "On-site"),
    "Brightwave Telecom": ("Telecommunications", "5,001-10,000 employees", "Dallas, Texas", "Hybrid"),
    "Sable Games": ("Computer Games", "201-500 employees", "Irvine, California", "Hybrid"),
    "Foxglove Therapeutics": ("Pharmaceutical Manufacturing", "201-500 employees", "Boston, Massachusetts", "On-site"),
    "Mosaic Talent Partners": ("Staffing and Recruiting", "11-50 employees", "Remote", "Remote Solely"),
    "Datagrid Tasks": ("Technology, Information and Internet", "11-50 employees", "Remote", "Remote Solely"),
    "Quarry Robotics": ("Robotics Engineering", "51-200 employees", "Boulder, Colorado", "On-site"),
}

DUTIES = {
    "mle": [
        ("Train, evaluate and deploy ranking models that power search and recommendations", "model_training", 4.0),
        ("Build model training pipelines in PyTorch and ship models to production", "model_training", 3.5),
        ("Partner with data scientists to productionize fraud detection models", "model_training", 3.5),
        ("Fine-tune and evaluate language models for customer-facing features", "applied_ai", 3.5),
        ("Design low-latency model serving and monitoring for real-time inference", "ml_systems", 3.0),
        ("Develop computer vision models for detection on edge devices", "model_training", 3.0),
        ("Own the feature store and model registry used by every ML team", "ml_systems", 2.5),
        ("Build retrieval-augmented generation services on top of hosted LLM APIs", "applied_ai", 2.0),
    ],
    "ds": [
        ("Build churn and lifetime-value models used by the growth team", "modeling", 4.0),
        ("Build statistical models of customer behavior that guide marketing spend", "modeling", 3.5),
        ("Forecast demand across regions and channels with time-series models", "forecasting", 3.5),
        ("Design and analyze A/B tests for product launches and pricing changes", "experimentation", 3.0),
        ("Run causal inference studies to measure the impact of new features", "experimentation", 3.0),
        ("Create dashboards and weekly business reporting for leadership", "other_ds", 1.0),
    ],
    "as": [
        ("Research and ship ranking and recommendation models for millions of users", "ranking_recsys", 4.0),
        ("Develop learning-to-rank methods for search relevance", "ranking_recsys", 4.0),
        ("Adapt large language models to query understanding and retrieval", "nlp_llm", 3.5),
        ("Run offline and online experiments to evaluate new modeling approaches", "ranking_recsys", 3.0),
        ("Develop vision-language models for product catalog understanding", "vision", 3.0),
    ],
}
SUPPORT = {
    "mle": ["Write clean, tested Python and review code with the team", "Monitor model quality and retrain on fresh data"],
    "ds": ["Present findings to product and business partners", "Write clear analyses that turn data into decisions"],
    "as": ["Present results to engineering and product partners", "Write design docs for new modeling approaches"],
}
REQ = {
    "years2": "2+ years of experience building machine learning models in production",
    "years1": "1-3 years of experience in data science or analytics",
    "years3": "3+ years of industry experience with Python and SQL",
    "years5": "5+ years of experience in machine learning engineering",
    "ms": "M.S. or Ph.D. in Computer Science, Statistics or a related field",
    "phd": "Ph.D. in Computer Science or a related field with publications at top venues",
    "py_torch": "Strong Python and PyTorch skills; experience with Docker and AWS",
    "py_sql": "Strong SQL and Python; experience with pandas and experimentation platforms",
    "airflow": "Experience with Airflow or similar orchestration tools",
    "k8s": "Production experience with Kubernetes and Spark is required",
    "scala": "Proficiency in Scala or Java for data processing",
    "stats": "Solid grounding in statistics, hypothesis testing and regression",
}
NICE = ["Experience with learning-to-rank or recommendation systems", "Familiarity with causal inference methods",
        "Contributions to open-source machine learning projects", "Experience with LightGBM or XGBoost"]

REASONS = {
    DUTIES["mle"][0][0]: "Trains and ships ranking models end to end, the same work as the candidate's search ranking.",
    DUTIES["mle"][1][0]: "Hands-on PyTorch training pipelines and production launches: core ML engineering.",
    DUTIES["mle"][2][0]: "Productionizing fraud models with data scientists; model work plus deployment.",
    DUTIES["mle"][3][0]: "Fine-tuning and evaluating language models for product features.",
    DUTIES["mle"][4][0]: "Model serving and monitoring; more systems than modeling.",
    DUTIES["mle"][5][0]: "Computer vision model training for edge devices; adjacent to the candidate's work.",
    DUTIES["mle"][6][0]: "Platform work on the feature store and registry, light on modeling.",
    DUTIES["mle"][7][0]: "Builds on hosted LLM APIs; little model training.",
    DUTIES["ds"][0][0]: "Predictive churn and lifetime-value models for the growth team.",
    DUTIES["ds"][1][0]: "Statistical models of customer behavior that drive spend decisions.",
    DUTIES["ds"][2][0]: "Time-series demand forecasting, close to the candidate's forecasting internship.",
    DUTIES["ds"][3][0]: "Experiment design and A/B test analysis for launches and pricing.",
    DUTIES["ds"][4][0]: "Causal inference studies of feature impact.",
    DUTIES["ds"][5][0]: "Mostly dashboards and reporting.",
    DUTIES["as"][0][0]: "Applied science on ranking and recommendations at scale.",
    DUTIES["as"][1][0]: "Learning-to-rank research for search, the candidate's core method.",
    DUTIES["as"][2][0]: "Adapting large language models to query understanding.",
    DUTIES["as"][3][0]: "Offline and online evaluation of new modeling approaches.",
    DUTIES["as"][4][0]: "Vision-language models for catalog understanding.",
}
YEARS = {"mle": {"high": "years2", "mid": "years3", "low": "years5", "capped": "years5", "k8s": "years2"},
         "ds": {"high": "years1", "mid": "years3", "low": "years5", "capped": "years5"},
         "as": {"high": "years2", "mid": "years3", "low": "phd", "capped": "phd"}}
TAILORED = {("Granite Health", "Machine Learning Engineer, Imaging"): 1, ("Cobalt Systems", "Applied Scientist"): 10}


@contextmanager
def _at(dt):
    """Write as if the clock read `dt` (ledgers stamp their own time; the demo backdates through JOBPILOT_NOW)."""
    old = os.environ.get("JOBPILOT_NOW")
    os.environ["JOBPILOT_NOW"] = dt.astimezone(timezone.utc).isoformat()
    try:
        yield
    finally:
        if old is None:
            os.environ.pop("JOBPILOT_NOW", None)
        else:
            os.environ["JOBPILOT_NOW"] = old


def _usd(n):
    return "${:,.0f}".format(n)


class Demo:
    def __init__(self, ws, lang):
        self.ws = ws
        self.lang = lang
        self.rnd = random.Random(20261019)
        self.now = util.now(ws.tz)
        self.rubric_text = RUBRIC
        self.n = 0
        self.rows = []
        self.by_key = {}

    def zh(self, en, zh):
        return zh if self.lang == "zh" else en

    def day(self, offset, hour=7, minute=31):
        """A time `offset` days ago at hour:minute local (never in the future)."""
        d = (self.now - timedelta(days=offset)).replace(hour=hour, minute=minute, second=0, microsecond=0)
        return min(d, self.now - timedelta(minutes=10))

    # ------------------------------------------------------------ postings
    def jd(self, company, title, duties, reqs, nice, extra=()):
        industry, size, city, arrangement = COMPANIES[company]
        lines = ["%s is hiring a %s." % (company, title), "", "What you will do"]
        lines += ["- %s." % d for d in duties]
        lines += ["", "What we are looking for"] + ["- %s." % r for r in reqs]
        if nice:
            lines += ["", "Nice to have"] + ["- %s." % x for x in nice]
        lines += [""] + list(extra)
        lines.append("Location: %s (%s). This is a full-time role." % (city if city != "Remote" else "Remote, United States",
                                                                      arrangement))
        return "\n".join(lines)

    def posting(self, company, title, track, *, first_seen, group, salary=(140000, 175000), experience="2-5",
                employment="FULL_TIME", seniority="Associate"):
        self.n += 1
        pid = "41%08d" % (27000000 + self.n * 1373)
        industry, size, city, arrangement = COMPANIES[company]
        slug = util.slug(company).lower()
        lo, hi = salary if salary else (None, None)
        posted = first_seen - timedelta(hours=self.rnd.randint(2, 30))
        row = {"id": pid, "source": "linkedin", "title": title, "company": company, "company_slug": slug + "-demo",
               "company_url": "", "company_size": size, "industry": industry, "agency": company == "Mosaic Talent Partners",
               "location": ("United States" if city == "Remote" else city + ", United States"),
               "work_arrangement": arrangement, "employment_type": employment, "seniority": seniority,
               "experience_level": experience, "salary": "USD %s-%s / year" % ("{:,}".format(lo), "{:,}".format(hi))
               if lo else "", "salary_max_usd": hi, "visa_sponsorship": None, "key_skills": [], "taxonomies": [],
               "summary": "", "posted_at": posted.astimezone(timezone.utc).isoformat(timespec="seconds"),
               "url": "https://jobs.example.com/%s/%s" % (slug, pid), "apply_url": "https://jobs.example.com/%s/%s/apply"
               % (slug, pid), "direct_apply": False, "recruiter": None, "groups": [group], "hint_tracks": [track],
               "first_seen": util.stamp(first_seen), "last_seen": util.stamp(first_seen), "stage": "new",
               "status": "pending", "demo": True}
        return row

    def write(self, row, jd):
        row["jd_sha"] = util.sha256_text(jd)
        row["jd_chars"] = len(jd)
        util.atomic_write_text(self.ws.jds / ("%s.txt" % row["id"]), jd + "\n")

    def comp(self, score, reason, jd_q, prof_q=None):
        return {"score": score, "reason": reason, "jd_evidence": [jd_q] if jd_q else [],
                "profile_evidence": [prof_q] if prof_q else []}

    def admit(self, company, title, track, quality, *, first_seen, group, status=None, note="", salary=None,
              duty=None):
        """A posting the review admits (or scores below the bar for quality 'low')."""
        r = self.rnd
        bank = DUTIES[track]
        if duty is None:
            pool_ = [d for d in bank if (d[2] >= 3.5 if quality == "high" else 2.5 <= d[2] <= 3.5 if quality == "mid"
                                         else d[2] <= 3.0)]
            duty = r.choice(pool_ or bank)
        others = [d for d in bank if d != duty]
        r.shuffle(others)
        duties = [duty[0], others[0][0], r.choice(SUPPORT[track])]
        years = YEARS[track][quality]
        if track == "ds":
            reqs = [REQ[years], REQ["py_sql"], REQ["stats"]]
        elif track == "as":
            reqs = [REQ[years], REQ["py_torch"]] + ([REQ["ms"]] if years != "phd" else [])
        else:
            reqs = [REQ[years], REQ["py_torch"], REQ["ms"]]
            if quality == "mid":
                reqs.append(REQ["airflow"])
            if quality == "k8s":
                reqs.insert(2, REQ["k8s"])
        nice = [r.choice(NICE)]
        salary = salary or (r.choice([(130000, 165000), (140000, 180000), (150000, 190000), (160000, 205000),
                                      (125000, 155000)]))
        exp = {"high": "0-2", "mid": "2-5", "low": "5-10", "capped": "5-10"}.get(quality, "2-5")
        row = self.posting(company, title, track, first_seen=first_seen, group=group, salary=salary, experience=exp,
                           seniority="Mid-Senior level" if quality in ("low", "capped") else "Associate")
        jd = self.jd(company, title, duties, reqs, nice)
        self.write(row, jd)
        low = quality == "low"
        d_score = min(duty[2], 2.0) if low else duty[2]
        cat = duty[1]
        soft = r.choice([0.0, 0.5, 0.5, 1.0]) if quality == "high" else 0.0   # spread among strong matches
        skill_soft = r.choice([0.0, 0.5, 0.5]) if quality == "high" else 0.0
        if quality == "high" and d_score >= 4.0 and r.random() < 0.5:
            d_score = 3.5
        if track == "mle":
            bg = (3.0 - soft if quality in ("high", "k8s") else 2.5 if quality in ("mid", "capped") else 1.5,
                  "Production ranking models at Brightline Analytics match the model work asked for."
                  if not low else "Some production ML, but far less than the depth this role expects.",
                  REQ[years], FACTS["rank"])
            sk = (2.0 - skill_soft if quality in ("high", "capped") else 1.5 if quality == "mid" else 1.0,
                  "Python, PyTorch, Docker and AWS SageMaker are all on the profile." if quality in ("high", "capped",
                                                                                                   "mid")
                  else "Core Python and PyTorch are covered; the platform stack is not.",
                  REQ["py_torch"], FACTS["skills_ops"] if quality == "high" else FACTS["skills_ml"])
        elif track == "ds":
            bg = (3.0 - soft if quality == "high" else 2.0 if quality in ("mid", "capped") else 1.5,
                  "A/B testing and forecasting work match the modeling this team does." if not low
                  else "Modeling experience is shorter than the role expects.",
                  REQ["stats"], FACTS["ab"] if cat == "experimentation" else FACTS["forecast"])
            sk = (2.0 - skill_soft if not low else 1.0, "Python, SQL and pandas are on the profile." if not low
                  else "Python and SQL are covered, but not the depth asked for.", REQ["py_sql"], FACTS["skills_ds"])
        else:
            bg = (3.0 - soft if quality == "high" else 2.0 if quality == "mid" else 1.0,
                  "Learning-to-rank work at Brightline Analytics is the same family of methods."
                  if not low else "The role wants a PhD research record the profile does not have.",
                  REQ[years], FACTS["rank"] if cat == "ranking_recsys" else FACTS["nlp"])
            sk = (2.0 if not low else 1.0, "Python and PyTorch are covered.", REQ["py_torch"], FACTS["skills_ml"])
        sen_score = {"years2": 1.0, "years1": 1.0, "years3": 0.5, "years5": 0.0, "phd": 0.0}[years]
        sen_reason = {1.0: "About 2 years of experience fits the requirement.",
                      0.5: "Asks for 3+ years; the profile shows about 2.",
                      0.0: "The requirement is well above the profile's experience."}[sen_score]
        comps = {
            "duties_4": self.comp(d_score, "The core duty is %s, close to the candidate's work." % cat.replace("_", " ")
                                  if d_score >= 3 else "Only part of the role is hands-on model work.", duty[0]),
            "background_3": self.comp(bg[0], bg[1], bg[2], bg[3]),
            "skills_2": self.comp(sk[0], sk[1], sk[2], sk[3]),
            "seniority_1": {"score": sen_score, "reason": sen_reason, "jd_evidence": [REQ[years]],
                            "profile_evidence": [FACTS["years"]]},
        }
        gaps, cap = [], None
        if years == "years5":
            gaps.append("5+ years of experience (the profile shows about 2)")
            if quality == "capped":
                cap = {"score": 6.5, "reason": "Explicit multi-year experience requirement.", "jd_evidence": [REQ[years]]}
        if years == "phd":
            gaps.append("PhD with publications")
        if years == "years3":
            gaps.append("3+ years of industry experience")
        if track == "mle" and quality == "mid":
            gaps.append("Airflow in production")
        if quality == "k8s":
            gaps.append("Kubernetes and Spark in production")
            cap = {"score": 7.5, "reason": "A required specialist skill (Kubernetes, Spark) is not in the profile.",
                   "jd_evidence": [REQ["k8s"]]}
        raw = {"listing_id": row["id"], "include": True, "track": track,
               "reason": REASONS.get(duty[0], "The core duties are %s, a fit for the %s track." % (
                   cat.replace("_", " "), config.track(self.ws.cfg, track)["name"])),
               "evidence": [duty[0]], "intermediary": False, "end_employer": "", "careers_url": "",
               "eligibility_flags": [], "injection_suspected": False,
               "assessment": {"category": cat, "gaps": gaps, "components": comps}}
        if cap:
            raw["assessment"]["fit_cap"] = cap
        self.review(row, raw, jd, status, note)
        return row

    def review(self, row, raw, jd, status=None, note=""):
        result = scoring.validate(raw, jd, self.rubric_text, self.ws.cfg)
        with _at(util.parse_time(row["first_seen"]) + timedelta(minutes=9)):
            review.apply_review(self.ws, row, raw, result, "sonnet")
            if status and status != row.get("status"):
                row["status"], row["status_note"] = status, note
        self.rows.append(row)
        self.by_key[(row["company"], row["title"])] = row
        return row

    def exclude(self, company, title, track_hint, *, first_seen, group, reason, quote, duties, flags=(), track="none",
                extra=()):
        row = self.posting(company, title, track_hint, first_seen=first_seen, group=group)
        jd = self.jd(company, title, duties, [REQ["years3"], REQ["py_sql"]], [], extra=extra)
        self.write(row, jd)
        raw = {"listing_id": row["id"], "include": False, "track": track, "reason": reason, "evidence": [quote],
               "intermediary": company == "Mosaic Talent Partners", "end_employer": "", "careers_url": "",
               "eligibility_flags": list(flags), "injection_suspected": False}
        return self.review(row, raw, jd)

    def drop(self, company, title, track_hint, *, first_seen, group, rule, detail, employment="FULL_TIME",
             experience="2-5", salary=(140000, 175000), extra=()):
        row = self.posting(company, title, track_hint, first_seen=first_seen, group=group, employment=employment,
                           experience=experience, salary=salary)
        duty = DUTIES[track_hint][1][0]
        jd = self.jd(company, title, [duty, SUPPORT[track_hint][0]], [REQ["py_sql"]], [], extra=extra)
        self.write(row, jd)
        row.update(stage="dropped", status="dropped", triage={"rule": rule, "detail": detail, "version": "1"})
        self.rows.append(row)
        return row

    def pending(self, company, title, track, *, first_seen, group):
        row = self.posting(company, title, track, first_seen=first_seen, group=group)
        jd = self.jd(company, title, [DUTIES[track][0][0], SUPPORT[track][1]], [REQ["years2"], REQ["py_torch"]], [])
        self.write(row, jd)
        self.rows.append(row)
        return row

    def save_rows(self):
        for row in self.rows:
            with _at(util.parse_time(row["first_seen"]) + timedelta(minutes=10)):
                self.ws.db.put(row)


# ---------------------------------------------------------------- building the workspace

def _postings(d):
    """About 80 postings across the three tracks, the last seven days and every status."""
    A = d.admit
    S = {"mle": "mle-core", "ds": "ds-core", "as": "as-core"}
    # ready, new today
    A("Northwind Analytics", "Machine Learning Engineer, Ranking", "mle", "high", first_seen=d.day(0), group=S["mle"],
      duty=DUTIES["mle"][0])
    A("Quarry Robotics", "Computer Vision Engineer", "mle", "mid", first_seen=d.day(0), group="mle-applied",
      duty=DUTIES["mle"][5])
    A("Evergreen Credit", "Machine Learning Engineer, Fraud", "mle", "high", first_seen=d.day(0), group=S["mle"],
      duty=DUTIES["mle"][2])
    A("Brightwave Telecom", "ML Platform Engineer", "mle", "mid", first_seen=d.day(0), group=S["mle"],
      duty=DUTIES["mle"][4])
    A("Orbit Commerce", "Data Scientist, Pricing", "ds", "high", first_seen=d.day(0), group=S["ds"], duty=DUTIES["ds"][1])
    A("Tidewater Energy", "Data Scientist, Forecasting", "ds", "high", first_seen=d.day(0), group=S["ds"],
      duty=DUTIES["ds"][2])
    A("Starling Ads", "Data Scientist, Experimentation", "ds", "mid", first_seen=d.day(0), group=S["ds"],
      duty=DUTIES["ds"][3])
    A("Polaris Retail", "Applied Scientist, Search Relevance", "as", "high", first_seen=d.day(0), group=S["as"],
      duty=DUTIES["as"][1])
    A("Sable Games", "Applied Scientist, Recommendations", "as", "mid", first_seen=d.day(0), group=S["as"],
      duty=DUTIES["as"][0])
    # ready, earlier this week
    A("Acme Robotics", "Machine Learning Engineer II", "mle", "high", first_seen=d.day(1), group=S["mle"])
    A("Bluefin Capital", "Machine Learning Engineer", "mle", "mid", first_seen=d.day(2), group=S["mle"])
    A("Beacon Learning", "LLM Engineer", "mle", "mid", first_seen=d.day(3), group="mle-applied", duty=DUTIES["mle"][3])
    A("Meridian Freight", "Senior Machine Learning Engineer", "mle", "capped", first_seen=d.day(4), group=S["mle"],
      duty=DUTIES["mle"][1])
    A("Lattice Labs", "Applied ML Engineer", "mle", "high", first_seen=d.day(5), group="mle-applied")
    A("Vertex Mobility", "Machine Learning Engineer, Autonomy", "mle", "k8s", first_seen=d.day(3, 7, 32),
      group="mle-core", duty=DUTIES["mle"][1])
    A("Harborview Bank", "Machine Learning Engineer, Risk Models", "mle", "mid", first_seen=d.day(6), group=S["mle"])
    A("Juniper Travel", "Data Scientist, Growth", "ds", "high", first_seen=d.day(1), group=S["ds"], duty=DUTIES["ds"][0])
    A("Summit Payments", "Decision Scientist", "ds", "mid", first_seen=d.day(2), group=S["ds"], duty=DUTIES["ds"][4])
    A("Kestrel Security", "Data Scientist II", "ds", "high", first_seen=d.day(3), group=S["ds"])
    A("Vertex Mobility", "Data Scientist, Demand Forecasting", "ds", "mid", first_seen=d.day(4), group=S["ds"],
      duty=DUTIES["ds"][2])
    A("Granite Health", "Senior Data Scientist", "ds", "capped", first_seen=d.day(5), group=S["ds"], duty=DUTIES["ds"][1])
    A("Atlas Mapping", "Applied Scientist II", "as", "high", first_seen=d.day(2), group=S["as"], duty=DUTIES["as"][0])
    A("Nimbus Cloud", "Machine Learning Scientist", "as", "mid", first_seen=d.day(3), group=S["as"], duty=DUTIES["as"][2])
    A("Copperleaf Foods", "Applied Scientist, Forecasting", "as", "high", first_seen=d.day(4), group=S["as"],
      duty=DUTIES["as"][3])
    A("Silverline Media", "Applied Scientist, Language Models", "as", "mid", first_seen=d.day(6), group=S["as"],
      duty=DUTIES["as"][2])
    A("Redwood Logistics", "Machine Learning Engineer, Routing", "mle", "high", first_seen=d.day(1), group=S["mle"],
      duty=DUTIES["mle"][1])
    # skipped and saved
    A("Lumen Health AI", "MLOps Engineer", "mle", "mid", first_seen=d.day(8), group=S["mle"], status="skipped",
      note=d.zh("Too much platform work for now", "现在不想做太多平台工作"), duty=DUTIES["mle"][6])
    A("Cobalt Systems", "Machine Learning Engineer, Search", "mle", "high", first_seen=d.day(9), group=S["mle"],
      status="skipped", note=d.zh("Applied to the Applied Scientist role there instead", "改投了同公司的 Applied Scientist"))
    A("Bluefin Capital", "Machine Learning Engineer, Signals", "mle", "mid", first_seen=d.day(11), group=S["mle"],
      status="skipped", note=d.zh("Relocation not possible", "无法搬迁"))
    A("Polaris Retail", "Data Scientist, Marketing Science", "ds", "mid", first_seen=d.day(10), group=S["ds"],
      status="skipped", note=d.zh("Similar to the Orbit Commerce role", "和 Orbit Commerce 的岗位重复"))
    A("Evergreen Credit", "Data Scientist, Credit Risk", "ds", "mid", first_seen=d.day(12), group=S["ds"],
      status="skipped")
    A("Northwind Analytics", "Applied Scientist, Ads", "as", "mid", first_seen=d.day(13), group=S["as"], status="skipped")
    # below the bar
    for company, title, track, off in (
            ("Acme Robotics", "Staff Machine Learning Engineer", "mle", 0), ("Vertex Mobility", "ML Infrastructure Engineer",
                                                                             "mle", 1),
            ("Summit Payments", "Machine Learning Engineer, GenAI Platform", "mle", 2),
            ("Harborview Bank", "Lead Data Scientist", "ds", 0), ("Tidewater Energy", "Principal Data Scientist", "ds", 3),
            ("Starling Ads", "Data Scientist, Measurement", "ds", 5), ("Pinecrest Insurance", "Data Scientist, Pricing",
                                                                      "ds", 6),
            ("Lattice Labs", "Research Scientist, Reinforcement Learning", "as", 1),
            ("Sable Games", "Senior Applied Scientist", "as", 2), ("Granite Health", "Applied Scientist, Clinical NLP",
                                                                   "as", 4),
            ("Brightwave Telecom", "Machine Learning Engineer, Networks", "mle", 5),
            ("Juniper Travel", "Applied Scientist, Pricing", "as", 6)):
        A(company, title, track, "low", first_seen=d.day(off, 7, 35), group=S[track])
    # excluded by the review
    d.exclude("Starling Ads", "Data Analyst, Marketing Reporting", "ds", first_seen=d.day(0, 7, 33), group="ds-core",
              reason="Reporting and dashboards with no modeling duties; it fits none of the tracks.",
              quote="Create dashboards and weekly business reporting for leadership",
              duties=["Create dashboards and weekly business reporting for leadership", "Maintain KPI definitions"])
    d.exclude("Foxglove Therapeutics", "Clinical Data Scientist", "ds", first_seen=d.day(1, 7, 33), group="ds-core",
              reason="Clinical trial statistics and regulatory submissions: a different discipline from the tracks.",
              quote="Analyze clinical trial data and prepare regulatory submissions",
              duties=["Analyze clinical trial data and prepare regulatory submissions", "Work with biostatisticians"])
    d.exclude("Kestrel Security", "Machine Learning Engineer, Threat Detection", "mle", first_seen=d.day(2, 7, 33),
              group="mle-core", reason="The role needs an active Top Secret clearance, which the profile does not show.",
              quote="Candidates need an active Top Secret clearance to start", track="mle",
              flags=["clearance_required"],
              duties=["Train anomaly detection models on network telemetry", "Deploy models to secure environments"],
              extra=["Candidates need an active Top Secret clearance to start."])
    d.exclude("Juniper Travel", "Data Scientist (Japanese bilingual)", "ds", first_seen=d.day(3, 7, 33), group="ds-core",
              reason="Business fluency in Japanese is required and the profile lists English and Spanish.",
              quote="Business fluency in Japanese is required", track="ds", flags=["language_required"],
              duties=["Build demand models for the Japan market", "Partner with the Tokyo office"],
              extra=["Business fluency in Japanese is required."])
    d.exclude("Brightwave Telecom", "Software Engineer, Backend", "mle", first_seen=d.day(4, 7, 33), group="mle-core",
              reason="Backend services work where machine learning is only mentioned in passing.",
              quote="Build and operate backend services in Go",
              duties=["Build and operate backend services in Go", "Improve API reliability and on-call tooling"])
    d.exclude("Mosaic Talent Partners", "AI Solutions Consultant", "mle", first_seen=d.day(5, 7, 33),
              group="mle-applied", reason="A pre-sales consulting role for a staffing agency's client, no model work.",
              quote="Lead client workshops and scope AI proofs of concept",
              duties=["Lead client workshops and scope AI proofs of concept", "Support sales with technical demos"])
    d.exclude("Lattice Labs", "Research Scientist, Computational Chemistry", "as", first_seen=d.day(6, 7, 33),
              group="as-core", reason="Chemistry simulation research, a field far from the candidate's work.",
              quote="Run molecular dynamics simulations for materials discovery",
              duties=["Run molecular dynamics simulations for materials discovery", "Publish in chemistry journals"])
    d.exclude("Meridian Freight", "Data Engineer", "ds", first_seen=d.day(2, 7, 34), group="ds-core",
              reason="Data pipelines and warehousing without modeling: not a data scientist role.",
              quote="Build and maintain ELT pipelines into the data warehouse",
              duties=["Build and maintain ELT pipelines into the data warehouse", "Own data quality checks"])
    d.exclude("Polaris Retail", "Business Intelligence Analyst", "ds", first_seen=d.day(5, 7, 34), group="ds-core",
              reason="Business intelligence reporting; it fits none of the tracks.",
              quote="Maintain business intelligence reports for store operations",
              duties=["Maintain business intelligence reports for store operations", "Answer ad hoc data requests"])
    d.exclude("Aurora Climate", "Climate Data Analyst", "ds", first_seen=d.day(3, 7, 34), group="ds-core",
              reason="Descriptive climate reporting without predictive modeling.",
              quote="Compile emissions inventories and quarterly climate reports",
              duties=["Compile emissions inventories and quarterly climate reports", "Coordinate with auditors"])
    # dropped by the coarse filter
    d.drop("Acme Robotics", "Machine Learning Engineer Intern", "mle", first_seen=d.day(0, 7, 32), group="mle-core",
           rule="employment", detail="employment type INTERN", employment="INTERN")
    d.drop("Sable Games", "Machine Learning Engineer Intern, Summer 2027", "mle", first_seen=d.day(2, 7, 32),
           group="mle-core", rule="employment", detail="employment type INTERN", employment="INTERN")
    d.drop("Orbit Commerce", "Data Science Intern", "ds", first_seen=d.day(1, 7, 32), group="ds-core",
           rule="employment", detail="title says 'Intern'")
    d.drop("Atlas Mapping", "Applied Scientist Intern", "as", first_seen=d.day(3, 7, 32), group="as-core",
           rule="employment", detail="employment type INTERN", employment="INTERN")
    d.drop("Pinecrest Insurance", "Data Scientist (6-month contract)", "ds", first_seen=d.day(4, 7, 32), group="ds-core",
           rule="employment", detail="employment type TEMPORARY", employment="TEMPORARY")
    d.drop("Beacon Learning", "Part-time Data Science Tutor", "ds", first_seen=d.day(5, 7, 32), group="ds-core",
           rule="employment", detail="employment type PART_TIME", employment="PART_TIME")
    d.drop("Kestrel Security", "ML Engineer, Defense Programs", "mle", first_seen=d.day(1, 7, 32), group="mle-core",
           rule="citizenship", detail="Must be a U.S. citizen", extra=["Must be a U.S. citizen."])
    d.drop("Harborview Bank", "Data Scientist, Federal Programs", "ds", first_seen=d.day(6, 7, 32), group="ds-core",
           rule="citizenship", detail="requires U.S. citizenship", extra=["This position requires U.S. citizenship."])
    d.drop("Datagrid Tasks", "AI Trainer, Machine Learning (flexible hours)", "mle", first_seen=d.day(0, 7, 32),
           group="mle-applied", rule="company", detail="AI-training gig platform Datagrid Tasks")
    d.drop("Northwind Analytics", "Principal Machine Learning Engineer", "mle", first_seen=d.day(2, 7, 33),
           group="mle-core", rule="experience", detail="experience band 10+ years", experience="10+")
    d.drop("Copperleaf Foods", "Junior Data Scientist", "ds", first_seen=d.day(3, 7, 33), group="ds-core",
           rule="salary", detail="advertised maximum $78,000 < $110,000", salary=(62000, 78000))
    d.drop("Cobalt Systems", "Sales Engineer, ML Platform", "mle", first_seen=d.day(4, 7, 33), group="mle-core",
           rule="title", detail="title word 'Sales'")
    # still waiting for the review (the batch limit)
    d.pending("Aurora Climate", "Machine Learning Engineer, Climate Risk", "mle", first_seen=d.day(0, 7, 36),
              group="mle-core")
    d.pending("Summit Payments", "Data Scientist, Fraud", "ds", first_seen=d.day(0, 7, 36), group="ds-core")


APPS = [
    # company, title, track, events [(days ago, event, extra)], posting?
    ("Northwind Analytics", "Machine Learning Engineer", "mle", [
        (26, "applied", {}), (26, "receipt", {}), (22, "oa_invited", {"deadline": 19}), (20, "oa_done", {}),
        (16, "interview_invited", {}), (16, "interview_scheduled", {"time": (14, 14)}), (14, "interview_done", {}),
        (11, "interview_scheduled", {"time": (9, 10)}), (9, "interview_done", {}), (2, "offer", {})]),
    ("Lumen Health AI", "Data Scientist", "ds", [(18, "applied", {})]),          # then by email: _inbox
    ("Vertex Mobility", "Machine Learning Engineer, Perception", "mle", [
        (14, "applied", {}), (10, "oa_invited", {"deadline": 7}), (8, "oa_done", {}), (5, "interview_invited", {}),
        (4, "interview_scheduled", {"time": (3, 11)}), (3, "interview_done", {})]),
    ("Cobalt Systems", "Applied Scientist", "as", [(9, "applied", {})]),
    ("Bluefin Capital", "Data Scientist", "ds", [
        (12, "applied", {}), (9, "oa_invited", {"deadline": 6}), (7, "oa_done", {})]),
    ("Harborview Bank", "Machine Learning Engineer", "mle", [
        (21, "applied", {}), (17, "interview_invited", {}), (16, "interview_scheduled", {"time": (15, 13)}),
        (15, "interview_done", {}), (10, "rejected", {})]),
    ("Pinecrest Insurance", "Data Scientist, Claims", "ds", [(20, "applied", {})]),
    ("Orbit Commerce", "Machine Learning Engineer, Recommendations", "mle", [(13, "applied", {})]),
    ("Aurora Climate", "Applied Scientist", "as", [(4, "applied", {}), (4, "receipt", {})]),
    ("Summit Payments", "Machine Learning Engineer", "mle", [(0, "applied", {})]),
    ("Redwood Logistics", "Data Scientist, Network Planning", "ds", [(16, "applied", {}), (6, "withdrawn", {})]),
    ("Silverline Media", "Data Scientist, Content", "ds", [(30, "applied", {}), (2, "closed", {})]),
    ("Granite Health", "Machine Learning Engineer, Imaging", "mle", []),
    ("Atlas Mapping", "Applied Scientist, Search Relevance", "as", [
        (7, "referral", {"referrer": "M.C."}), (6, "applied", {})]),
    ("Nimbus Cloud", "Machine Learning Engineer", "mle", []),
    ("Copperleaf Foods", "Data Scientist", "ds", []),
]


def _applications(d):
    ws = d.ws
    notes = {
        "Silverline Media": d.zh("Found on the company site", "在公司官网上看到的"),
        "Redwood Logistics": d.zh("Withdrew: the team moved to on-site five days a week", "撤回：团队改成每周五天坐班"),
        "Northwind Analytics": d.zh("Offer: base within the posted range; decide by Friday", "拿到 offer，周五前答复"),
    }
    for company, title, track, evs, in [(a[0], a[1], a[2], a[3]) for a in APPS]:
        start = (evs[0][0] + 1) if evs else 1
        created_at = d.day(start, 20, 5)
        posting = None
        if company != "Silverline Media":
            quality = "high" if company in ("Northwind Analytics", "Atlas Mapping", "Granite Health") else "mid"
            posting = d.admit(company, title, track, quality, first_seen=d.day(start + 1, 7, 31),
                              group={"mle": "mle-core", "ds": "ds-core", "as": "as-core"}[track],
                              status="applied" if evs else "hold")
        with _at(created_at):
            app_id = ledger.create(ws, company, title, posting_id=posting["id"] if posting else None,
                                   url=(posting or {}).get("apply_url") or "https://careers.example.com/%s" %
                                   util.slug(company).lower(), track=track, location=(posting or {}).get("location"),
                                   date=created_at.date().isoformat(), by="user")
            ws.event("app", d.zh("%s saved: %s — %s", "%s 已保存：%s — %s") % (app_id, company, title), app_id=app_id)
        if posting:
            posting["app_id"] = app_id
            posting["status_note"] = "application %s" % app_id
        if (company, title) in TAILORED:
            when = d.day(TAILORED[(company, title)], 21, 10)
            with _at(when):
                ledger.record(ws, app_id, "tailored", date=when.date().isoformat(), by="user")
        for ago, event, extra in evs:
            when = d.day(ago, 18, 40)
            fields = {"date": when.date().isoformat(), "by": "user"}
            if "deadline" in extra:
                fields["deadline"] = (d.now - timedelta(days=extra["deadline"])).date().isoformat()
            if "time" in extra:
                days_ago, hour = extra["time"]
                t = (d.now - timedelta(days=days_ago)).replace(hour=hour, minute=0, second=0, microsecond=0)
                fields["time"] = t.isoformat(timespec="minutes")
                fields["minutes"] = 45
            if "referrer" in extra:
                fields["referrer"] = extra["referrer"]
            if event == "offer" and company in notes:
                fields["note"] = notes[company]
            if event in ("withdrawn", "closed") and company in notes:
                fields["note"] = notes[company]
            with _at(when):
                ledger.record(ws, app_id, event, **fields)
                ws.event("app", "%s %s: %s — %s" % (app_id, _event_word(d, event), company, title), app_id=app_id)


def _inbox(d):
    """Gmail tracking the way /jobpilot:inbox records it: fictional emails, checked and recorded by inbox.apply (an
    interview time agreed by email, your reply waiting for a confirmation, a recruiter's question waiting for you)."""
    try:
        from . import inbox
    except ImportError:
        return
    ws = d.ws
    ids = {st["company"]: st["id"] for st in ledger.states(ws).values()}
    me = "Jordan Rivera <jordan.rivera@example.com>"
    batches = {}

    def mail(n, company, category, ago, hour, sender, subject, snippet, summary, next_step, **extra):
        when = d.day(ago, hour, 20)
        msg = {"message_id": "19%014x" % (0x2a1b3c4d5e6f00 + n), "thread_id": "19%014x" % (0x2a1b3c4d5e0000 + n),
               "received_at": util.stamp(when), "sender": sender, "subject": subject, "snippet": snippet,
               "category": category, "app_id": ids[company], "company": company, "deadline": None,
               "action_required": False, "from_me": sender == me, "summary": summary, "next_step": next_step,
               "interview": None}
        msg.update(extra)
        batches.setdefault(ago, []).append(msg)

    # Lumen Health AI: a recruiter's note, an invitation, your reply with times, then their confirmation
    lumen = "Priya Shah <priya.shah@lumen-health.example>"
    mail(1, "Lumen Health AI", "recruiter_reply", 15, 11, lumen, "Your application: Data Scientist",
         "Your forecasting background caught our eye, and I have shared your resume with the hiring manager.",
         d.zh("The recruiter liked your forecasting work and passed your resume to the hiring manager; nothing to do "
              "yet.", "招聘方看好你的预测建模经历，已把简历转给用人经理；暂时不用做什么。"), "wait")
    mail(2, "Lumen Health AI", "interview_invite", 12, 10, lumen, "Interview: Data Scientist",
         "We would like to invite you to a 45-minute interview with the data science team. Could you send a few times "
         "that work for you?", d.zh("They invite you to a 45-minute interview with the data science team and ask "
                                    "for your times.", "对方邀请你和数据科学团队面试 45 分钟，请你给几个时间。"),
         "pick_time", action_required=True)
    mail(3, "Lumen Health AI", "owner_reply", 2, 9, me, "Re: Interview: Data Scientist",
         "Thank you for the invitation! I am available on Wednesday or Thursday afternoon this week.",
         d.zh("You offered Wednesday or Thursday afternoon; waiting for them to pick one.",
              "你给了周三或周四下午两个时间，等对方选。"), "wait")
    at = (d.now + timedelta(days=2)).replace(hour=14, minute=0, second=0, microsecond=0)
    sentence = "Confirmed: %s, %s %d at 2:00 PM Eastern, 45 minutes on video." % (
        at.strftime("%A"), at.strftime("%B"), at.day)
    mail(4, "Lumen Health AI", "interview_scheduled", 1, 15, lumen, "Re: Interview: Data Scientist", sentence,
         d.zh("Interview confirmed for %s 2:00 PM (45 minutes, video); prepare." % at.strftime("%A"),
              "面试定在周%s下午 2:00（45 分钟，视频）；准备一下。" % "一二三四五六日"[at.weekday()]),
         "prepare", interview={"time": at.isoformat(timespec="minutes"), "minutes": 45, "time_snippet": sentence,
                               "rescheduled": False})
    # Atlas Mapping: a phone-screen invitation; you replied with times and wait for a confirmation
    atlas = "Leo Park <leo.park@atlas-mapping.example>"
    mail(5, "Atlas Mapping", "interview_invite", 3, 13, atlas, "Applied Scientist: next steps",
         "Would you be available for a 30-minute phone screen next week? Please send a few times that work for you.",
         d.zh("They invite you to a 30-minute phone screen next week and ask for your availability.",
              "对方邀请你下周电话初筛 30 分钟，请你给几个时间。"), "pick_time", action_required=True)
    mail(6, "Atlas Mapping", "owner_reply", 1, 19, me, "Re: Applied Scientist: next steps",
         "Thank you, I would be glad to talk. I am free Tuesday or Wednesday afternoon next week, any time after "
         "1 PM Eastern.", d.zh("You offered Tuesday or Wednesday afternoon next week; waiting for them to confirm a "
                               "time.", "你给了下周二或周三下午的时间，等对方确认。"), "wait")
    # Aurora Climate: a recruiter's question that waits for your answer
    mail(7, "Aurora Climate", "recruiter_reply", 1, 16, "Dana Whitfield <dana.whitfield@aurora-climate.example>",
         "Quick question about your application",
         "Before I share your resume with the hiring manager, could you tell me which of our two science teams "
         "interests you more?", d.zh("The recruiter asks which of their two science teams interests you more before "
                                     "passing your resume on; reply.", "招聘方想知道你对两个科学团队中的哪个更感兴趣，"
                                                                      "再把简历转给用人经理；回复一下。"),
         "reply", action_required=True)
    # Cobalt Systems: an assessment with a deadline; Pinecrest Insurance: a rejection
    mail(8, "Cobalt Systems", "oa_invite", 1, 10, "Cobalt Systems Recruiting <no-reply@cobalt-careers.example>",
         "Next step: online assessment", "Please complete the 90-minute coding assessment within 4 days.",
         d.zh("A 90-minute coding assessment, due in four days; take it on your own.",
              "90 分钟的编程测评，四天内完成；请独立完成。"), "do_oa",
         deadline=(d.now + timedelta(days=3)).date().isoformat(), action_required=True)
    mail(9, "Pinecrest Insurance", "rejection", 11, 14, "Pinecrest Talent <no-reply@pinecrest-jobs.example>",
         "Your application to Pinecrest Insurance", "Unfortunately, we have decided to move forward with other "
         "candidates.", d.zh("They decided not to move forward; nothing to do.", "对方决定不推进；不用做什么。"), "none")
    for ago in sorted(batches, reverse=True):
        msgs = sorted(batches[ago], key=lambda m: m["received_at"])
        at = util.parse_time(msgs[-1]["received_at"]) + timedelta(minutes=25)
        with _at(min(at, d.now - timedelta(minutes=5))):
            inbox.apply(ws, {"messages": msgs, "covered_until": util.stamp(ws.now()), "complete": True,
                             "problems": []})


def _event_word(d, event):
    zh = {"applied": "已投递", "receipt": "已收到确认", "oa_invited": "收到测评", "oa_done": "完成测评",
          "interview_invited": "收到面试邀请", "interview_scheduled": "约定面试", "interview_done": "完成面试",
          "offer": "拿到 offer", "rejected": "被拒", "withdrawn": "已撤回", "closed": "已关闭（无回复）",
          "recruiter_reply": "招聘方回复", "referral": "获得内推"}
    en = {"oa_invited": "got an assessment", "oa_done": "finished the assessment",
          "interview_invited": "got an interview invite", "interview_scheduled": "scheduled an interview",
          "interview_done": "finished an interview", "offer": "got an offer", "rejected": "was rejected",
          "withdrawn": "withdrew", "closed": "closed (no response)", "recruiter_reply": "recruiter replied",
          "referral": "got a referral", "receipt": "application received"}
    return zh.get(event, event) if d.lang == "zh" else en.get(event, event.replace("_", " "))


def _runs(d):
    """Seven daily collections: manifests, budget lines and feed events consistent with the postings."""
    ws = d.ws
    from . import search
    groups = search.plan(ws.cfg)
    for off in range(6, -1, -1):
        start = d.day(off, 7, 30)
        if off == 0:
            start = min(start, d.now - timedelta(minutes=25))   # before 07:30 the run happened "just now"
        day = start.date().isoformat()
        seen = [r for r in d.rows if (r.get("first_seen") or "")[:10] == day]
        m = {"id": day, "day": day, "created_at": util.stamp(start),
             "window_start": (start - timedelta(hours=36)).astimezone(timezone.utc).isoformat(),
             "window_end": start.astimezone(timezone.utc).isoformat(), "actor": ws.cfg["search"]["actor"],
             "groups": {}, "complete": True, "demo": True}
        total = new_total = 0
        cost_total = 0.0
        for g in groups:
            new = sum(1 for r in seen if g["name"] in (r.get("groups") or []))
            rows = new + d.rnd.randint(2, 9)
            truncated = off == 3 and g["name"] == "mle-core"
            cost = round(0.0001 + rows * 0.0035, 4)
            if truncated:
                cost = g["budget"]
            slot = "search:%s:%s" % (day, g["name"])
            entry = {"track": g["track"], "cap": g["budget"], "input": search.actor_input(ws.cfg, g, start),
                     "status": "SUCCEEDED", "slot": slot, "started_at": util.stamp(start),
                     "run_id": "demo%s%s" % (day.replace("-", ""), util.slug(g["name"])), "dataset_id": "demo",
                     "cost": cost, "rows": rows, "new": new, "finished_at": util.stamp(start + timedelta(minutes=4)),
                     "collected": True}
            if truncated:
                entry["truncated"] = "the run's charge reached its cap; raise budget_usd for this group if rows are missing"
                m["complete"] = False
            m["groups"][g["name"]] = entry
            with _at(start):
                util.jsonl_append(ws.budget_path, {"op": "reserve", "slot": slot, "usd": g["budget"], "day": day,
                                                   "kind": "search", "note": g["name"]})
            with _at(start + timedelta(minutes=4)):
                util.jsonl_append(ws.budget_path, {"op": "settle", "slot": slot, "usd": cost, "run_id": entry["run_id"]})
            total += rows
            new_total += new
            cost_total += cost
        m["finished_at"] = util.stamp(start + timedelta(minutes=5))
        util.atomic_write_json(ws.runs / day / "manifest.json", m)
        reviewed = [r for r in seen if r.get("stage") == "reviewed"]
        ready = sum(1 for r in reviewed if r.get("status") in ("ready", "hold", "applied", "skipped"))
        below = sum(1 for r in reviewed if r.get("status") == "below_threshold")
        excl = sum(1 for r in reviewed if r.get("status") == "excluded")
        dropped = sum(1 for r in seen if r.get("status") == "dropped")
        with _at(start + timedelta(minutes=5)):
            ws.event("collect", d.zh("collected %d postings (%d new) for $%.2f", "采集 %d 个岗位（新 %d 个），花费 $%.2f")
                     % (total, new_total, cost_total), manifest=day, new=new_total, cost=round(cost_total, 4))
        with _at(start + timedelta(minutes=11)):
            ws.event("review", d.zh("reviewed %d postings: %d ready, %d below threshold, %d excluded; %d filtered out "
                                    "for free", "评审 %d 个岗位：%d 个入库，%d 个低于门槛，%d 个排除；粗筛免费去掉 %d 个")
                     % (len(reviewed), ready, below, excl, dropped), ready=ready)
        with _at(start + timedelta(minutes=12)):
            ws.event("daily", d.zh("%d new matches today", "今日新入库 %d 个岗位") % ready)


def _referrals(d):
    ws = d.ws
    net = ws.network
    (net / "cards").mkdir(parents=True, exist_ok=True)
    LSU, NIT = "Lakeside State University", "Northfield Institute of Technology"
    people_spec = [
        # pid, company, first, last, title, school, degree, field, years, prior, triage, score, sub, stage, card
        ("Atlas Mapping", "Maya", "Chen", "Senior Applied Scientist", LSU, "B.S.", "Statistics", ("2012", "2016"),
         ("Data Scientist", "Orbit Commerce", "2016-07", "2021-03"), "kept", 92, (5, 5, 4, 4, 5), "referred", True),
        ("Atlas Mapping", "Daniel", "Okafor", "Machine Learning Engineer", NIT, "M.S.", "Computer Science",
         ("2018", "2020"), ("Software Engineer", "Brightwave Telecom", "2020-07", "2022-05"), "kept", 85,
         (5, 4, 4, 4, 4), "replied", True),
        ("Atlas Mapping", "Priya", "Natarajan", "Senior Data Scientist", LSU, "B.S.", "Mathematics", ("2010", "2014"),
         ("Analyst", "Pinecrest Insurance", "2014-08", "2018-01"), "kept", 71, (3, 4, 3, 4, 3), "connect_sent", True),
        ("Atlas Mapping", "Lucas", "Moreau", "Technical Recruiter, AI and ML", NIT, "B.A.", "Psychology",
         ("2013", "2017"), ("Recruiter", "Mosaic Talent Partners", "2017-06", "2020-10"), "recruiter", 64,
         (3, 3, 2, 4, 4), "new", False),
        ("Atlas Mapping", "Ethan", "Brooks", "Sales Engineer", LSU, "B.S.", "Computer Science", ("2011", "2015"),
         ("Solutions Consultant", "Nimbus Cloud", "2015-07", "2019-02"), "skip", None, None, "new", False),
        ("Cobalt Systems", "Sofia", "Alvarez", "Machine Learning Engineer II", NIT, "M.S.", "Computer Science",
         ("2020", "2022"), ("Data Scientist", "Harbor Retail", "2019-06", "2020-08"), "kept", 88, (5, 5, 4, 4, 4),
         "card_ready", True),
        ("Cobalt Systems", "Hannah", "Kim", "Engineering Manager, ML Platform", LSU, "B.S.", "Computer Science",
         ("2008", "2012"), ("Senior Software Engineer", "Northwind Analytics", "2012-07", "2018-04"), "kept", 79,
         (4, 4, 3, 3, 5), "card_ready", True),
        ("Cobalt Systems", "Omar", "Haddad", "Data Scientist", NIT, "M.S.", "Statistics", ("2017", "2019"),
         ("Data Analyst", "Copperleaf Foods", "2019-07", "2021-09"), "kept", 74, (3, 4, 4, 4, 3), "accepted", True),
        ("Cobalt Systems", "Grace", "Liu", "PhD Intern, Applied Science", NIT, "Ph.D.", "Computer Science",
         ("2023", "2028"), None, "skip", None, None, "new", False),
        ("Lumen Health AI", "Noah", "Fischer", "Applied Scientist", LSU, "B.S.", "Statistics", ("2014", "2018"),
         ("Machine Learning Engineer", "Granite Health", "2018-07", "2022-01"), "kept", 90, (5, 5, 4, 4, 4),
         "chat_scheduled", True),
        ("Lumen Health AI", "Aisha", "Rahman", "Data Science Manager", NIT, "M.S.", "Computer Science",
         ("2013", "2015"), ("Data Scientist", "Evergreen Credit", "2015-06", "2019-11"), "kept", 77, (4, 4, 3, 3, 5),
         "connect_sent", True),
        ("Lumen Health AI", "Mateo", "Silva", "University Recruiter", LSU, "B.A.", "Communications", ("2015", "2019"),
         ("Talent Coordinator", "Beacon Learning", "2019-08", "2021-12"), "recruiter", 58, (2, 4, 2, 4, 3), "new",
         False),
    ]
    stage_history = {
        "connect_sent": ["connect_sent"], "accepted": ["connect_sent", "accepted"],
        "replied": ["connect_sent", "accepted", "message_sent", "replied"],
        "chat_scheduled": ["connect_sent", "accepted", "message_sent", "replied", "chat_scheduled"],
        "referred": ["connect_sent", "accepted", "message_sent", "replied", "chat_scheduled", "chat_done", "referred"],
    }
    try:
        from . import network as netmod
        event_stage = dict(getattr(netmod, "EVENT_STAGE", {}))
    except ImportError:
        event_stage = {}
    event_stage.setdefault("message_sent", "messaged")
    people, companies = {}, {}
    weights = {"role": 0.30, "alumni": 0.25, "path": 0.15, "reply": 0.15, "help": 0.15}
    for n, spec in enumerate(people_spec, 1):
        (company, first, last, title, school, degree, field, years, prior, triage, score, sub, stage, has_card) = spec
        key = util.company_key(company)
        pid = "P%04d" % n
        found = d.day(15 if company == "Atlas Mapping" else 6 if company == "Cobalt Systems" else 9, 20, 15)
        current = [{"title": title, "company": company, "company_slug": util.slug(company).lower() + "-demo",
                    "start": "2021-04" if "Senior" in title or "Manager" in title else "2022-06", "end": "present",
                    "duration": "3 yrs" if "Senior" in title or "Manager" in title else "2 yrs"}]
        if "Intern" in title:
            current[0].update(start="2026-06", duration="5 mos")
        exp = [dict(current[0])]
        if prior:
            exp.append({"title": prior[0], "company": prior[1], "company_slug": "", "start": prior[2], "end": prior[3],
                        "duration": ""})
        p = {"pid": pid, "first_name": first, "last_name": last, "name": "%s %s" % (first, last), "linkedin_url": "",
             "public_id": "demo-%s-%s" % (first.lower(), last.lower()), "headline": "%s at %s" % (title, company),
             "location": COMPANIES[company][2] + ", United States" if COMPANIES[company][2] != "Remote"
             else "United States", "country": "US", "current": current, "experience": exp,
             "education": [{"school": school, "degree": degree, "field": field, "start": years[0], "end": years[1]}],
             "company_key": key, "company": company, "triage": triage, "title": title,
             "triage_reason": {"skip": "unrelated function: Sales" if "Sales" in title else
                               "still studying (%s, until %s)" % (school, years[1]), "recruiter":
                               "recruiting or university relations"}.get(triage, ""),
             "schools": [school], "school_link": "degree", "stage": stage, "found_at": util.stamp(found),
             "updated_at": util.stamp(found), "history": [], "demo": True}
        if score is not None:
            keys = ("role", "alumni", "path", "reply", "help")
            p["sub"] = dict(zip(keys, sub))
            p["score"] = int(round(100 * sum(weights[k] * p["sub"][k] for k in keys) / 5))
            p["evidence"] = {"role": title, "alumni": school, "path": prior[0] if prior else title,
                             "reply": "since %s" % current[0]["start"], "help": company}
            p["why"] = _why(d, first, title, school, degree, field, company)
            p["scored_at"] = util.stamp(found + timedelta(minutes=3))
        t = found + timedelta(hours=2)
        for ev in stage_history.get(stage, []):
            t = t + timedelta(days=1, hours=d.rnd.randint(1, 6))
            if t > d.now:
                t = d.now - timedelta(hours=1)
            p["history"].append({"event": ev, "from": p["history"][-1]["to"] if p["history"] else "card_ready",
                                 "to": event_stage.get(ev, ev), "at": util.stamp(t),
                                 "note": ""})
            with _at(t):
                util.jsonl_append(net / "events.jsonl", {"pid": pid, "event": ev, "note": "", "by": "user"})
                ws.event("net", "%s %s (%s)" % (p["name"], _net_word(d, ev), company), pid=pid)
        if p["history"]:
            p["updated_at"] = p["history"][-1]["at"]
        if has_card:
            card_at = util.stamp(found + timedelta(minutes=8))
            p["card_at"] = card_at
            util.atomic_write_json(net / "cards" / ("%s.json" % pid), _card(d, p, school, degree, field, card_at))
        people[pid] = p
        c = companies.setdefault(key, {"key": key, "name": company, "slug": util.slug(company).lower() + "-demo",
                                       "url": "", "searches": [], "removed": False})
        if not c["searches"]:
            c["searches"].append({"at": util.stamp(found), "schools": [LSU, NIT], "profiles": 0, "kept": 0,
                                  "cost": 0.0, "run_id": "demo-net-%s" % key, "status": "SUCCEEDED"})
        s = c["searches"][0]
        s["profiles"] += 1
        s["kept"] += 1 if triage in ("kept", "recruiter") else 0
        s["cost"] = round(0.02 + s["profiles"] * 0.008, 3)
    util.atomic_write_json(net / "people.json", people, mode=0o600)
    util.atomic_write_json(net / "companies.json", companies, mode=0o600)
    for key, c in companies.items():
        s = c["searches"][0]
        day = s["at"][:10]
        slot = "referrals:%s:%s" % (key, day)
        with _at(util.parse_time(s["at"])):
            util.jsonl_append(ws.budget_path, {"op": "reserve", "slot": slot, "usd": 0.4, "day": day,
                                               "kind": "referrals", "note": c["name"]})
            util.jsonl_append(ws.budget_path, {"op": "settle", "slot": slot, "usd": s["cost"], "run_id": s["run_id"]})
            ws.event("net", d.zh("%s: %d alumni found, %d kept", "%s：找到 %d 位校友，保留 %d 位") % (
                c["name"], s["profiles"], s["kept"]), company=key)


def _net_word(d, ev):
    zh = {"connect_sent": "已发送好友请求", "accepted": "已通过", "message_sent": "已发消息", "replied": "已回复",
          "chat_scheduled": "已约咖啡聊", "chat_done": "已聊完", "referred": "已内推"}
    return zh.get(ev, ev) if d.lang == "zh" else ev.replace("_", " ")


def _why(d, first, title, school, degree, field, company):
    if d.lang == "zh":
        return "和你同为 %s 校友（%s %s）；现在是 %s 的 %s，正好在你想去的方向。" % (school, degree, field, company, title)
    return "Fellow %s alum (%s %s); now %s at %s, the kind of team you are targeting." % (
        school, degree, field, title, company)


def _card(d, p, school, degree, field, card_at):
    first = p["first_name"]
    short = school.split()[0]
    note = ("Hi %s, I'm Jordan, a fellow %s grad now building ranking models at Brightline Analytics. I'd love to "
            "hear how you got into your work at %s." % (first, short, p["company"]))
    note_alumni = ("Hi %s! %s %s alum here. I build ML ranking models and I'm exploring roles at %s; would you be "
                   "open to connecting?" % (first, short, degree, p["company"]))
    message = ("Thanks for connecting, %s! I'm a machine learning engineer at Brightline Analytics, where I train "
               "ranking models in PyTorch and cut inference latency by 38%%. I'm interested in %s and would value "
               "15 minutes of your perspective on the team and what makes someone successful there. Would a short call "
               "next week work? Either way, thank you. Jordan" % (first, p["company"]))
    followup = ("Hi %s, just bumping this in case it got buried. Happy to work around your schedule, and no worries "
                "if now is busy. Jordan" % first)
    questions = [
        "What does a typical week look like for you as %s?" % p["title"],
        "How did your %s %s background carry over into your current work?" % (short, field),
        "How do teams at %s decide which models to ship and how to measure them?" % p["company"],
        "What do strong new hires on your team do in their first three months?",
        "Is there anyone else you would suggest I talk to about these roles?",
    ]
    if d.lang == "zh":
        why = "你们同为 %s 校友，对方的职位和你投的方向一致。" % school
        hooks = ["同校同专业：%s %s" % (degree, field), "对方之前在 %s" % (p["experience"][-1]["company"])]
        avoid = ["别一上来就要内推", "不要提签证或身份"]
    else:
        why = "Shared school (%s) and a role on the team you are applying to." % school
        hooks = ["Same program: %s %s" % (degree, field), "Previously at %s" % p["experience"][-1]["company"]]
        avoid = ["Do not ask for a referral in the first note", "Keep visas and work authorization out of it"]
    return {"pid": p["pid"], "note": note, "note_alumni": note_alumni, "message": message, "followup": followup,
            "questions": questions, "why": why, "hooks": hooks, "avoid": avoid, "warnings": [], "created_at": card_at,
            "model": "demo", "note_limit": 200, "sign_name": "Jordan"}


def _tailored(d):
    ws = d.ws
    try:
        from . import docx as docxmod
    except ImportError:
        docxmod = None
    for (company, title), ago in TAILORED.items():
        row = d.by_key.get((company, title))
        when = d.day(ago, 21, 10)
        folder = ws.tailored / ("%s_%s_%s" % (when.date().isoformat(), util.slug(company), util.slug(title)))
        folder.mkdir(parents=True, exist_ok=True)
        jd = (ws.jds / ("%s.txt" % row["id"])).read_text() if row else ""
        util.atomic_write_text(folder / "jd.md", "# %s — %s\n\nSource: %s\n\n%s" % (
            company, title, (row or {}).get("url", ""), jd))
        md = RESUME.replace("**Ranking Models:** Trained gradient-boosted",
                            "**Ranking Models:** Trained and evaluated gradient-boosted")
        files = {}
        if docxmod is not None and hasattr(docxmod, "from_markdown"):
            try:
                out = folder / "Jordan_Rivera_Resume.docx"
                docxmod.from_markdown(md, out, kind="resume", title="Jordan Rivera — Resume", author="Jordan Rivera")
                files["docx"] = ws.rel(out)
            except Exception:  # noqa: BLE001 - the demo still works without a document
                files = {}
        report = {"folder": ws.rel(folder), "posting_id": (row or {}).get("id"), "company": company, "role": title,
                  "created_at": util.stamp(when), "files": files, "master": "profile/resume.md",
                  "jd_priorities": ["Train and evaluate models end to end", "Ship models to production",
                                    "Work with product partners"],
                  "changed": [{"label": "Ranking Models", "kind": "reworded"},
                              {"label": "Experimentation", "kind": "reordered"}],
                  "lint": {"errors": [], "warnings": [d.zh("1 bullet is longer than 36 words",
                                                           "有 1 条要点超过 36 个词")]},
                  "facts_to_confirm": [d.zh("Did the ranking work include online A/B evaluation?",
                                            "排序模型的工作是否包含线上 A/B 评估？")],
                  "audit": {"pass": True, "findings": []}, "demo": True}
        util.atomic_write_json(folder / "report.json", report)
        with _at(when):
            ws.event("tailor", d.zh("tailored resume ready: %s — %s", "定制简历已生成：%s — %s") % (company, title),
                     id=(row or {}).get("id"))


def _reports(d):
    ws = d.ws
    for off in range(6, -1, -1):
        when = d.day(off, 7, 45)
        problems = []
        if off == 3:
            problems = [d.zh("capped search groups (may miss postings): mle-core",
                             "搜索组达到费用上限（可能漏掉岗位）：mle-core")]
        with _at(when):
            brief.build(ws, date=when.date().isoformat(), problems=problems)


def create(target, lang="en", force=False):
    """Build the demo workspace in `target`. A non-empty folder is refused, except an earlier demo with force."""
    root = Path(target).expanduser().resolve()
    lang = "zh" if str(lang).lower().startswith("zh") else "en"
    if root.exists() and any(root.iterdir()):
        if not (force and (root / "data" / "DEMO").exists()):
            raise util.JobPilotError("%s is not empty. Pick a new folder%s." % (
                root, "" if not (root / "data" / "DEMO").exists() else ", or pass --force to rebuild this demo"))
        for child in root.iterdir():
            if child.is_dir() and not child.is_symlink():
                shutil.rmtree(child)
            else:
                child.unlink()
    ws, _ = workspace.init(root, name="Jordan Rivera", language=lang, timezone="America/New_York")
    util.atomic_write_text(ws.config_path, CONFIG.replace("{lang}", lang))
    util.atomic_write_text(ws.profile_md, PROFILE)
    util.atomic_write_text(ws.rubric_md, RUBRIC)
    util.atomic_write_text(ws.claims_md, CLAIMS)
    util.atomic_write_text(ws.profile / "resume.md", RESUME)
    util.atomic_write_text(ws.data / "DEMO", "fictional demo data (jobpilot demo); paid actions are turned off\n")
    ws.reload()
    d = Demo(ws, lang)
    _postings(d)
    _applications(d)
    _inbox(d)
    d.save_rows()
    _runs(d)
    _referrals(d)
    _tailored(d)
    _reports(d)
    with _at(d.now - timedelta(minutes=2)):
        ws.event("system", d.zh("demo workspace ready: every company, person and posting is fictional",
                                 "演示工作区已生成：所有公司、人物和岗位都是虚构的"))
    return ws


def summary(ws):
    """Counts for tests and the CLI message."""
    return {"postings": ws.db.count(), "ready": ws.db.count("status='ready'"),
            "apps": len(ledger.states(ws)), "reports": len(brief.dates(ws))}


if __name__ == "__main__":
    import sys
    w = create(sys.argv[1], lang=(sys.argv[2] if len(sys.argv) > 2 else "en"), force="--force" in sys.argv)
    print(json.dumps(summary(w)))
