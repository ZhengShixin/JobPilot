---
name: tailor
description: Tailor the user's resume to one job posting without inventing anything — minimal, truthful edits to the master DOCX (reordered and reworded bullets, JD keywords only where true), a red-line lint, an independent claims audit, and optionally a cover letter. Use when the user wants a resume for a specific job or posting id.
argument-hint: "<posting id | job URL>"
allowed-tools: Bash(jobpilot *), Read, Write, Edit, Agent
---

# Truthful resume tailoring

Reply in the user's language; the resume itself stays in the language of the master (usually English).
Standard: `${CLAUDE_PLUGIN_ROOT}/jobpilot/prompts/tailoring-guide.md` — read it first, follow it exactly.

1. Identify the posting: a pool id in `$ARGUMENTS` -> `jobpilot tailor prepare <id> --json`. A URL or pasted text
   -> save the full JD to a file and run `jobpilot tailor prepare --jd-file <file> --company "<company>" --role
   "<title>" --url "<url>" --json`. This creates `tailored/<date>_<company>_<role>/` with `jd.md` and prints the
   master resume's numbered paragraphs.
2. Read `jd.md`, `profile/profile.md`, `profile/claims.md` and the master paragraphs. Write the 2-4 JD priorities.
3. Write `<folder>/plan.json` (schema: `jobpilot tailor prepare` output / the guide): `jd_priorities`, `edits`
   (ops `replace` {find, with}, `delete` {paragraph}, `move` {paragraph, before|after}, `insert_after` {anchor, text,
   label}; every `find`/`paragraph`/`anchor` must be text copied exactly from one master paragraph), `bullets_changed`,
   `facts_to_confirm` (questions instead of guesses), `not_claimed` (JD keywords the resume cannot truthfully claim),
   optional `cover_letter`. Keep to minor tailoring: about 3-4 bullets, never more than `tailor.max_changed_bullets`.
4. `jobpilot tailor build <folder> --json`. Fix every error (an edit whose text is not found or not unique, a lint
   error such as a never-claim term or a placeholder) and rebuild. Review the warnings (new numbers, page count).
5. Independent check: launch the `jobpilot:claims-auditor` agent with the folder path (it compares the tailored
   resume with profile.md and claims.md). Fix every blocker, rebuild, re-audit. (Headless alternative:
   `jobpilot tailor audit <folder>`; `jobpilot tailor show <folder>` prints the report.)
6. Show the user: the changed bullets (before -> after), the not-claimed keywords, open questions, and the paths of
   the DOCX/PDF. If they answer a fact question, add the fact to profile.md first, then tailor again.
7. Remind them that they submit the application themselves; afterwards record it with
   `jobpilot app add --posting <id> --applied` (or `/jobpilot:track`).

One-shot alternative without the conversation (also what the dashboard's "Tailor resume" button runs):
`jobpilot tailor auto <posting id> [--cover-letter]` (plan, build, audit; exit 1 if a lint error or an audit blocker
remains). A PDF-only master: write `profile/resume.md` and run `jobpilot resume build-docx profile/resume.md
profile/resume.docx`, then point `candidate.resume` at the .docx.
