# Writing profile/rubric.md (the scoring profile)

The AI reviewer scores every included posting on its track's 10-point rubric, and JobPilot's code then checks the
answer: every JD quote must occur verbatim in the posting and every profile quote verbatim in `rubric.md`. So
rubric.md has two jobs: it defines what a good fit means **for this candidate on each track**, and it holds the only
candidate facts the reviewer may quote. Its sha256 keys every score: after an edit, older scores are marked stale and
rescored gradually (`review.rescore_per_run` per run).

## Structure

```markdown
# Scoring profile (v1, <date>)

## Rubrics by track (10 points each)

### Track `<id>` — <name>
- `duties_4` (0–4): what the role's core duties are, from the best fit (4) down: list 4-6 anchors with values.
- `background_3` (0–3): how directly the candidate's own experience matches the work and record the JD asks for.
- `skills_2` (0–2): the candidate's verified tools and methods against the JD's required stack.
- `seniority_1` (0–1): level fit (1 = the candidate's target level; 0.5 = a stretch; 0 = far above).
- Fit caps (optional, need a verbatim JD quote): 7.5 for a required specialist skill not in the profile; 6.5 for an
  explicit multi-year requirement the candidate does not meet; 5.5 for people management or executive scope.
- Categories: cat_a, cat_b, cat_c, other_<id>

(repeat per track)

## Candidate facts (verbatim; the reviewer may quote only these)
- short, self-contained sentences copied from profile.md: degree, field, school, year; each role with employer,
  dates and the 1-3 strongest verified results; tools grouped by category; years of experience; languages;
  certifications; publications if relevant.

## Never credit
- tools, domains, titles or claims the candidate must not be credited with (from claims.md), e.g. "Kubernetes",
  "people management", "production C++".
```

## Rules for good rubrics

- **Track-specific anchors.** The four component names are shared, but what they measure differs per track. For a
  Data Scientist track, `duties_4` might be: predictive modeling 4; experimentation/causal inference 3.5; analytics
  with some modeling 2.5; dashboards/reporting 1. For an ML Engineer track: training and shipping models 4; ML
  infrastructure 3; LLM application building on hosted APIs 2; software around ML 1.
- **Ground `background_3` in the candidate's actual work**: name their real areas ("ranking models in PyTorch",
  "A/B testing at scale") so the reviewer can tell near from far.
- **List verified tools exactly** under candidate facts and reference them in `skills_2`; unverified tools go to
  "Never credit" so they become gaps instead of points.
- **Seniority** should reflect `candidate.seniority`: e.g. entry/mid -> 1 for 0-3 years, 0.5 for 3-5, 0 for 5+ or
  staff/principal titles.
- **Categories**: 3-5 duty families per track plus `other_<id>`. Category ids are lowercase snake_case and must be
  copied into the track's `categories` list in jobpilot.toml (the review schema enforces them).
- **Quotable facts**: one fact per bullet, plain sentences, no markdown inside the sentence, numbers exactly as in
  the resume. Do not add contact details, addresses, salary history or identity data: rubric.md is sent with every
  review batch.
- **Never strengthen**: if the resume says "contributed to", the fact says "contributed to", not "led".

## Example (fictional candidate)

```markdown
### Track `mle` — Machine Learning Engineer
- `duties_4` (0–4): training, fine-tuning and shipping models end to end 3.5–4; building models into products with
  deployment 3; ML infrastructure (serving, pipelines, MLOps) 2.5; applications on hosted LLM APIs without model
  work 2; software around ML without model work 1 or less.
- `background_3` (0–3): production ranking or recommendation models 3; other supervised models in production 2.5;
  offline analysis or research prototypes 1.5; unrelated domains (robotics control, hardware) 1 or less.
- `skills_2` (0–2): Python, PyTorch, scikit-learn, SQL, Docker, AWS SageMaker cover the core stack 2; centers on
  Kubernetes, Spark, Scala or CUDA (not verified) 1 or less, listed as gaps.
- `seniority_1` (0–1): 0–3 years or "Engineer I/II" 1; 3–5 years 0.5; senior/staff/lead or 5+ years 0.
- Fit caps: 7.5 for a required specialist skill not in the profile (e.g. CUDA kernels); 6.5 for an explicit 5+ years
  requirement; 5.5 for people management.
- Categories: model_training, ml_systems, applied_ai, other_mle
```
