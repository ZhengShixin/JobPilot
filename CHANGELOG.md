# Changelog

## 0.2.0 — unreleased

- Recruiter mail that asks you something is an action item: it shows in Activity (with a Gmail link) and as the
  first group of Today's Up next until the application moves on or you mark it answered, and one click records it
  as an interview invitation (`jobpilot inbox waiting|answered|invite`). A phone screen or an introductory call a
  recruiter asks to schedule now counts as an interview invitation.
- The inbox scan works like a secretary: it reads whole threads, your own replies included, and gives each new
  email a one- or two-sentence summary and a next step (shown in Activity, Up next and the application's
  timeline). An interview time both sides agreed (`interview_scheduled`) becomes the interview's time when the
  quoted sentence states it and it lies within a day before and 180 days after the email; your replies are recorded
  (`owner_reply`) and settle a waiting recruiter mail; an interview without a time says "You replied …; waiting for
  them to confirm a time" until it is agreed. New key `inbox.my_addresses`; `jobpilot app event … --rescheduled`
  replaces the open interview's time.
- Every referral card has a cold email (subject, body, a follow-up email) for an address you found yourself: the
  body says your resume is attached and ends with "Best regards,", JobPilot adds your signature
  (`referrals.email_signature`, else your name), and the usual checks apply (limits, claims, numbers, links,
  placeholders, sensitive topics). The person drawer has copy buttons and an "Open in Gmail" draft link (an address
  you type stays on the page); `jobpilot net email P0007 [--feedback ...]` adds the email to an older card (a new
  card version). Mark `email_sent`; the follow-up comes due after 7 days. Nothing is sent and no address is looked
  up.
- Practice roles: `candidate.min_salary_usd` stays the hard floor; with the new `candidate.target_salary_usd`, a role
  whose advertised annual maximum lies under the target is admitted as a practice role: tagged "Low pay · practice"
  / 「低薪·练手」 (the pool search finds it by those words), queued after the regular roles of its track and marked in
  the daily report. A role without an advertised salary stays a regular role.

### Fixed

- The dashboard's pause banner named `jobpilot resume` (the resume tools); paid runs come back with
  `jobpilot unpause`.

## 0.1.0 — 2026-10-19

First public release.

- `/jobpilot:setup`: resume → profile facts, claims policy, role tracks, per-track rubrics, Apify search plan with
  an offline check.
- Daily pipeline: budget-capped Apify collection with catch-up windows, free coarse filter, evidence-checked AI review
  in batches, ranked queue, daily report, optional notification; macOS LaunchAgent / Linux cron scheduling.
- Truthful resume tailoring on the user's own DOCX (standard-library engine), lint, claims audit, cover letters.
- Application ledger (append-only, hash-chained) with assessments, interviews, follow-ups and a funnel.
- Gmail progress tracking (read-only) and an alumni referral finder with drafted notes.
- Local bilingual dashboard (English / 简体中文) and a demo workspace with fictional data.
- Guard hook: no token reads, no direct ledger edits, no outgoing email, no LinkedIn automation.
