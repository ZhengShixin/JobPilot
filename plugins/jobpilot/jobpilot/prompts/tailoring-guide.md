# The JobPilot tailoring standard

How JobPilot (and you, in `/jobpilot:tailor`) adapts a master resume to one posting. The goal is a resume that
reads as written for this job and that you can defend word for word in an interview. Truth always wins over fit.

## 1. Truth before tailoring

- **Where facts come from**, in this order: `profile/claims.md` (your binding rules), `profile/profile.md` (your
  facts), the master resume's own wording. A posting, a sample resume or a mentor's example is a reference for
  structure and vocabulary only, never for facts.
- **Never invent or strengthen** responsibilities, ownership, results, tools, team sizes, titles, statuses or
  numbers. "Plausible" is not a source. If the profile does not say it, it does not go in.
- **No placeholders.** A resume with `XXX`, `[Company]` or `TODO` can end up in front of a recruiter. When a
  stronger bullet would need a fact you have not written down, keep the master wording and write the question to
  answer (`facts_to_confirm`). Add the answer to `profile.md` and the next tailoring can use it.

## 2. Scope: minor tailoring, not a rewrite

1. Read the posting and name its **2–4 priorities**: the duties it states first or repeats.
2. Pick the one or two experiences most relevant to those priorities.
3. Change the content of **about 3–4 bullets** (the default limit is 6): sharpen the weakest or least aligned
   bullets with truthful detail, and reorder bullets inside a section so the most relevant come first.
4. Reorder the skills line by relevance; never add a skill the profile does not show.
5. Dropping a less relevant bullet to keep the page count is fine. Adding a bullet is fine only for a fact the
   profile states that the master left out.

## 3. What never changes

Employers, job titles, dates, degrees, schools, GPA wording, certifications, publications (every citation exactly as
printed), contact details and links. Numbers are copied character for character.

## 4. The bullet standard

- One sentence: **action verb + what + how + result or deliverable**. A bold `Label:` lead-in only if the master
  uses them.
- About as long as the bullet it replaces, so the page count does not grow (most resumes: 15–30 words).
- Specific over generic: say what was built, analyzed, trained, measured, shipped or compared. Never use filler such
  as "leveraged synergies", "drove growth", "provided valuable insights" or anything about personal learning.
- Keep the master's punctuation habits (final periods or not) consistently across the page.
- Vary the verbs; avoid using the same verb twice within one role.

## 5. Ownership: the verb must match what you did

Use the verb that matches your real role: built, designed, implemented, trained, analyzed, evaluated, compared,
automated, documented, taught, supported, co-authored, contributed. "Led", "owned", "managed" and "launched" only
when the profile says so. Team work stays team work ("with two engineers"), never leadership.

## 6. Numbers

- Only numbers that appear in the master or the profile, copied exactly: no rounding, no "over", "nearly", "+" or
  "up to" added or removed.
- Do not force a metric into every bullet. A concrete deliverable ("5 dashboards", "3 models in production") is a
  valid result.
- Never count one result twice across bullets.

## 7. Transferable framing

Describe the real skill and why it matters for this job; never claim the target function itself.

- Right: "built time-series forecasting models in Python, the same methods the role's demand planning relies on".
- Wrong: "experienced demand planner" when you have never held that role.

## 8. Keywords and applicant tracking systems

- Use 2–4 of the posting's exact nouns per relevant experience, and only where they are accurate ("A/B testing",
  "feature store", "PyTorch").
- Every requirement you cannot truthfully claim goes into `not_claimed`. That list is useful: it tells you what to
  learn, or what to address honestly in an interview or cover letter.

## 9. Cover letters (optional)

250–350 words, three or four short paragraphs: why this team and role (from the posting's own content), two or three
pieces of evidence from the profile mapped to the posting's priorities, and a plain close. Every fact from the
profile; no placeholders; signed with your name.

## 10. Final check

Answer each question before using the resume; revise until all are "yes".

1. Is every claim backed by claims.md, profile.md or the master?
2. Does every changed bullet use the verb that matches your real role?
3. Is each impact specific rather than generic?
4. Is every number copied exactly?
5. Is target-role language accurate without implying experience you lack?
6. Does each bullet say what was built, analyzed, measured or delivered?
7. Is the style consistent with the rest of the page (length, punctuation, labels)?
8. Do employers, titles, dates and education match the master exactly?
9. Do the items most relevant to the posting's priorities come first in their sections?
10. Could you defend every word in an interview?

## 11. Learning from corrections

When you (or a mentor) correct a tailored bullet, write the rule into `profile/claims.md` (what was wrong, what is
right) or the fact into `profile/profile.md`. Every later tailoring reads both, so the same mistake is not made
twice.
