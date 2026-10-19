# Changelog

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
