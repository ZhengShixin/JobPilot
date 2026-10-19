# Writing profile/profile.md and profile/claims.md

## profile.md — the only fact source

Every AI step that writes about the candidate (tailoring, outreach, interview prep) may use only what profile.md
says. It is built from the resume plus the candidate's own answers, never from guesses.

- Copy facts **verbatim or near-verbatim** from the resume: employers, titles, dates, degrees, schools, results and
  numbers exactly as written. Do not round, upgrade ("helped" -> "led") or merge two results into one.
- Keep one section per kind of fact: Summary (only if the resume has one), Education, Experience (one subsection per
  role with its bullets), Projects, Publications, Skills (grouped as the resume groups them), Certifications,
  Languages, Awards, Other.
- Add a "Confirmed by the candidate" section for facts the candidate told you in this conversation (e.g. "Comfortable
  with Bash and Linux") with the date.
- Add a "To confirm" section for anything ambiguous on the resume (an unclear date, an acronym, a number without
  context). Ask the candidate rather than guessing; move answers into the right section.
- Leave out what the job search does not need: full street address, date of birth, ID numbers, salary history.
  Contact lines are optional (they are not needed for scoring or tailoring).
- Work authorization belongs in jobpilot.toml (`candidate.work_authorization`), not in materials.

## claims.md — what must never be claimed

A short policy the tailoring and outreach steps obey, plus its machine-checkable twin in jobpilot.toml (`[claims]
never_claim = [...]`, checked on every tailored resume):

- Tools and skills that target postings often ask for but the resume does not show (e.g. "Kubernetes", "Spark",
  "people management"): list them so they are reported as gaps, never claimed.
- Wording rules for real facts that are easy to overstate (a co-authored paper stays co-authored; a project "in
  progress" stays in progress; a contract role stays a contract role).
- Anything the candidate asked to keep out of materials.
- Numbers that must stay exactly as written.
