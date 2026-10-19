# Designing the search plan (Apify LinkedIn jobs actor)

JobPilot searches LinkedIn through the Apify actor `fantastic-jobs/advanced-linkedin-job-search-api`. Each
`[search.groups.<name>]` table in `jobpilot.toml` becomes one actor run per day: its keys are passed to the actor as
they are, and JobPilot adds `timeRange`, `datePostedAfter` (the exact start of the window), `limit`, `descriptionType`
and, unless a group sets its own, `locationSearch` (from `candidate.locations`) and `aiWorkArrangementFilter` (from
`candidate.work_arrangements`). `search.common` is merged into every group.

A good plan returns almost every relevant posting (recall >= 90% per track) while paying for as few irrelevant ones as
possible: every returned row costs money (about $0.005 on Apify's free tier, $0.0015-0.0035 on paid tiers, plus about
$0.01 per run start on the free tier).

## How the actor matches text (verified on more than 50,000 returned rows)

The text filters are PostgreSQL "simple" full-text search:

- Lowercase, **no stemming**, no stop words. `Research` does not match `Researcher`; `Engineer` does not match
  `Engineers`; `Scientist` does not match `Science`. Use a prefix (`Research:*`) or list every variant.
- `word:*` is a prefix match (`Quant:*` matches Quant, Quants, Quantitative, and also Quantum and Quantity, so pair a
  prefix with exclusions).
- A multi-word term is a **phrase**: the words must be adjacent and in order. `Machine Learning Engineer` does not
  match `Engineer, Machine Learning` — add `Engineer Machine Learning` (punctuation is ignored) if you need both.
- Words joined by `/` form ONE token: `Trader/Researcher` matches neither `Trader` nor `Researcher`; a prefix on the
  first part (`Trader:*`) does match it.
- A hyphenated word yields the compound and its parts: `Full-Stack` matches `Full Stack`, `Full-Stack` and `Stack`.
- Symbols are dropped: `C++` becomes `c` (which matches every lone "C") and `C#`/`.NET` misbehave. Avoid them.
- Terms inside one list are OR'ed; different fields are AND'ed.
- `descriptionSearch` searches the title and the description. It is expensive for the actor: keep at most about 13
  terms per group and always pair it with `titleSearch` (the actor's documentation asks for this).
- `descriptionExclusionSearch` showed no observable effect; do not rely on it (use `titleExclusionSearch`, JobPilot's
  coarse filters or the AI review instead).
- `industryFilter` / `industryExclusionFilter` match the company's LinkedIn industry **exactly** (e.g. "Financial
  Services", "Software Development", "Technology, Information and Internet", "IT Services and IT Consulting",
  "Hospitals and Health Care", "Investment Management", "Banking", "Insurance", "Higher Education", "Research
  Services", "Biotechnology Research", "Pharmaceutical Manufacturing", "Staffing and Recruiting"). A wrong name
  silently matches nothing.
- `organizationSearch` matches company names as phrases (`Google:*` also matches "Google Cloud").

`jobpilot plan check` replays these rules offline (`querymatch.py`) against `profile/plan-examples.json`, so you can
test a plan for free.

## Useful structured filters (exact values)

| key | values | use |
|---|---|---|
| `aiEmploymentTypeFilter` | FULL_TIME, PART_TIME, CONTRACTOR, TEMPORARY, INTERN, VOLUNTEER, PER_DIEM, OTHER | `["FULL_TIME"]` in `search.common` for most people |
| `seniorityFilter` | Associate, Director, Executive, Mid-Senior level, Entry level, Not Applicable, Internship | LinkedIn's own label, often missing or wrong: prefer title exclusions |
| `aiExperienceLevelFilter` | 0-2, 2-5, 5-10, 10+ | years the posting asks for (AI-derived); e.g. `["0-2", "2-5"]` for early career |
| `aiWorkArrangementFilter` | On-site, Hybrid, Remote OK, Remote Solely | set through `candidate.work_arrangements` |
| `aiVisaSponsorshipFilter` | true | only postings that mention sponsorship; too strict for most people (most postings say nothing) |
| `removeAgency` | true | drop staffing agencies and job boards |
| `excludeATSDuplicate` | true | drop LinkedIn copies of postings that also exist on company career sites |
| `organizationSizeFilter` | 1, 2-10, 11-50, 51-200, 201-500, 501-1000, 1001-5000, 5001-10000, 10001+ | company size |
| `aiTaxonomiesFilter` | Technology, Finance & Accounting, Data & Analytics, Software, Engineering, Science & Research, Healthcare, ... | broad job families (AI-derived) |
| `locationSearch` | "United States", "New York, New York, United States", "London, England, United Kingdom", "New:*" | full names, no abbreviations |

## Design patterns

1. **One to four groups per track.** Start with a "core titles" group (the exact titles of the track and their
   variants). Add a "generic title + description keywords" group only when a track hides behind generic titles
   (e.g. "Analyst" or "Researcher" roles that are only relevant when the JD mentions specific methods).
2. **Exclusions are cheap and powerful.** Every group should exclude internships (Intern, Internship, Interns, Summer,
   Co-op, Coop, Apprentice:*, Student) unless the candidate wants them, and levels far above the target (for an
   early-career search: Principal, Distinguished, Fellow, Director, Head, VP, Vice, Chief, Manager, Lead, and
   "Staff Engineer"-style phrases — avoid excluding the bare word "Staff" if "Staff Accountant"-style junior titles
   matter). Also exclude the obvious look-alikes of each title (for "Quant:*": Quantity, Quantum, Surveyor; for
   "Data Scientist": none usually; for "ML Engineer": Sales, Solutions, Support, Recruiter).
3. **Do not pay twice.** When two groups overlap (e.g. a generic "Research" group would also return every
   "Research Scientist"), exclude the other group's core titles from the broader group.
4. **Description keywords narrow broad titles**: pair a generic title list (Analyst:*, Associate, Researcher:*) with
   a handful of strong phrases that only real target roles mention ("machine learning", "PyTorch", "statistical
   model:*", "A/B test:*", "stochastic", "portfolio optimization"). At most ~13 terms.
5. **Budget each group.** `budget_usd` is the run's hard cap (`maxTotalChargeUsd`). Size it at roughly
   (expected rows per day x price x 1.5). The first real run shows the volumes; `jobpilot plan check` prints the
   observed rows/day afterwards. The daily cap (`search.daily_budget_usd`) and monthly cap are enforced on top.
6. **Order by preference.** Groups run in track-priority order, so if money runs out, the lower-priority tracks are
   the ones skipped.
7. **Keep the AI review in mind.** The plan only needs high recall with tolerable noise; the free coarse filter and
   the AI review remove the rest. Don't over-tighten a group to the point of losing real roles.

## Starting points by role family

These are templates; adapt every list to the candidate's resume, level and geography.

**Machine Learning Engineer**
```toml
[search.groups.mle-core]
track = "mle"
budget_usd = 0.40
titleSearch = ["Machine Learning Engineer", "Machine Learning Engineers", "ML Engineer", "ML Engineers",
               "Engineer Machine Learning", "Applied Machine Learning", "Deep Learning Engineer", "AI Engineer",
               "AI ML Engineer", "MLOps", "ML Platform", "Computer Vision Engineer", "NLP Engineer", "LLM Engineer"]
titleExclusionSearch = ["Intern", "Internship", "Interns", "Summer", "Co-op", "Student", "Principal", "Director",
                        "Head", "VP", "Manager", "Sales", "Solutions", "Support", "Recruiter", "Professor"]
```

**Data Scientist**
```toml
[search.groups.ds-core]
track = "ds"
budget_usd = 0.40
titleSearch = ["Data Scientist", "Data Scientists", "Data Science", "Decision Scientist", "Product Scientist",
               "Applied Scientist", "Research Scientist Data"]
titleExclusionSearch = ["Intern", "Internship", "Summer", "Principal", "Director", "Head", "VP", "Manager"]
```

**Software Engineer (new grad / early career)**
```toml
[search.groups.swe-early]
track = "swe"
budget_usd = 0.60
titleSearch = ["Software Engineer", "Software Engineers", "Software Developer", "Backend Engineer",
               "Back End Engineer", "Full Stack Engineer", "Full-Stack Engineer", "New Grad", "Engineer I",
               "Engineer II"]
titleExclusionSearch = ["Intern", "Internship", "Senior", "Sr", "Staff", "Principal", "Lead", "Manager", "Director",
                        "Test", "QA", "Sales", "Support"]
aiExperienceLevelFilter = ["0-2", "2-5"]
```

**Quantitative Researcher**
```toml
[search.groups.quant]
track = "qr"
budget_usd = 0.50
titleSearch = ["Quant:*"]
titleExclusionSearch = ["Quantity", "Quantities", "Quantum", "Surveyor", "Surveying", "Intern", "Internship",
                        "Summer", "Clinical", "UX", "Professor", "Recruiter"]

[search.groups.fin-research]
track = "qr"
budget_usd = 0.30
industryFilter = ["Financial Services", "Investment Management", "Investment Banking", "Capital Markets", "Banking"]
titleSearch = ["Research", "Researcher:*", "Analyst:*", "Strategist:*", "Portfolio"]
titleExclusionSearch = ["Quant:*", "Intern", "Internship", "Summer", "Equity Research", "Sales"]
descriptionSearch = ["quantitative research", "stochastic", "time series", "portfolio optimization",
                     "derivatives pricing", "statistical model:*", "machine learning", "PhD"]
```

**Product / Business / Data Analyst**
```toml
[search.groups.analyst]
track = "analyst"
budget_usd = 0.40
titleSearch = ["Data Analyst", "Business Analyst", "Product Analyst", "Analytics Engineer", "Business Intelligence"]
titleExclusionSearch = ["Intern", "Internship", "Senior", "Lead", "Manager", "Director", "Financial Analyst"]
```

## Examples file (profile/plan-examples.json)

```json
{
  "positive": [
    {"title": "Machine Learning Engineer II", "track": "mle"},
    {"title": "Senior Data Scientist, Growth", "track": "ds"},
    {"title": "Quantitative Researcher", "track": "qr", "industry": "Financial Services"},
    {"title": "Research Analyst", "track": "qr", "industry": "Investment Management",
     "description": "build statistical models of asset returns using time series methods"}
  ],
  "negative": [
    {"title": "Machine Learning Engineer Intern"},
    {"title": "Director of Data Science"},
    {"title": "Quantity Surveyor"},
    {"title": "Sales Engineer, AI Platform"}
  ]
}
```

Write 15-30 realistic positives per track (title variants, plurals, levels, word orders, synonyms the candidate
would accept) and 15-30 negatives (near misses that would waste money). Add `description`/`industry` to examples for
groups that use `descriptionSearch`/`industryFilter`. Then iterate with `jobpilot plan check` until every track's
recall is at least 90% and few negatives are returned.
