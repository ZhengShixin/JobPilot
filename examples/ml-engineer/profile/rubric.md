# Scoring profile (v1, fictional example)

Every score is keyed to this file's sha256. Quote candidate facts only from the "Candidate facts" section, verbatim.

## Rubrics by track (10 points each)

### Track `mle` — Machine Learning Engineer
- `duties_4` (0–4): training, fine-tuning and shipping models end to end (ranking, recommendation, forecasting,
  NLP/LLM models) 3.5–4; building models into products with deployment and monitoring 3; ML infrastructure only
  (serving, pipelines, MLOps) 2.5; applications on hosted LLM APIs without model work 2; software around ML without
  model work 1 or less.
- `background_3` (0–3): production ranking or recommendation models 3; other supervised models in production 2.5;
  forecasting or experimentation work 2; research prototypes only 1.5; unrelated domains (robotics control, hardware,
  computer graphics) 1 or less.
- `skills_2` (0–2): the JD's core stack is covered by the verified tools (Python, PyTorch, scikit-learn, XGBoost,
  SQL, AWS SageMaker, Docker, Airflow) 2; partly covered 1; centers on unverified tools (Kubernetes, Spark, Scala,
  CUDA, C++) 0.5 or less, listed as gaps.
- `seniority_1` (0–1): 0–3 years, "Engineer I/II" or mid-level 1; 3–5 years required 0.5; senior/staff/lead titles or
  5+ years 0.
- Fit caps: 7.5 for a required specialist skill not in the profile (e.g. CUDA kernels, a robotics stack); 6.5 for an
  explicit 5+ years requirement; 5.5 for people management.
- Categories: model_training, ml_systems, applied_ai, other_mle

### Track `ds` — Data Scientist
- `duties_4` (0–4): predictive modeling that ships into decisions or products 3.5–4; experimentation and causal
  inference 3; analytics with some modeling 2.5; dashboards and reporting only 1.
- `background_3` (0–3): applied modeling in production plus A/B testing 3; A/B testing or forecasting only 2;
  coursework only 1.
- `skills_2` (0–2): Python and SQL plus the JD's modeling libraries covered 2; partly 1; centers on R, SAS, Tableau-only
  or Spark 0.5 or less.
- `seniority_1` (0–1): 0–3 years 1; 3–5 years 0.5; senior/staff/lead or 5+ years 0.
- Fit caps: 7.5 for a required domain specialty not in the profile (e.g. clinical trials, actuarial exams); 6.5 for
  an explicit 5+ years requirement; 5.5 for people management.
- Categories: predictive_modeling, experimentation, analytics, other_ds

## Candidate facts (verbatim; quote only from here)
- M.S. Computer Science, Northfield Institute of Technology, 2021 – 2023.
- B.S. Statistics, Lakeside State University, 2017 – 2021.
- Machine Learning Engineer, Brightline Analytics, Jun 2023 – Present.
- Trained gradient-boosted and transformer ranking models in PyTorch for a marketplace search page serving 2 million weekly users.
- Improved click-through rate by 6.1% in an online A/B test.
- Built a feature pipeline in Python and SQL on AWS that refreshes 140 features hourly.
- Cut model inference latency by 38% by distilling a ranking model and batching requests on AWS SageMaker endpoints.
- Designed drift and data-quality checks with Great Expectations.
- Data Science Intern, Harbor Retail, Jun 2022 – Aug 2022.
- Analyzed 12 A/B tests on checkout flows with Python and SQL.
- Built a weekly demand forecast for 300 stores with gradient-boosted trees.
- Skills: Python, SQL, Bash, PyTorch, scikit-learn, XGBoost, LightGBM, Hugging Face Transformers.
- Infrastructure: AWS (SageMaker, S3, Lambda), Docker, Airflow, Git.
- Methods: learning to rank, A/B testing, time-series forecasting, model monitoring.
- About 2.5 years of full-time industry experience, plus one internship.
- Spoken languages: English, Spanish.

## Never credit
- Kubernetes, Spark, Scala, CUDA, C++, R, people management, research publications.
