---
name: referrals
description: Find alumni of the user's schools at a target company (LinkedIn data through the user's Apify account), rank who is most likely to help, and draft personal connection notes, a cold email, follow-ups and coffee-chat questions that the user sends themselves. Use when the user wants referrals, alumni contacts or networking help for a company.
argument-hint: "<company name or LinkedIn company URL>"
allowed-tools: Bash(jobpilot *), Read
---

# Alumni referral finder

Reply in the user's language. JobPilot never logs into LinkedIn, never sends invitations, messages or email, never
looks up anyone's email address and keeps people's data in the local workspace only. The user reviews and sends
everything personally.

0. The first time in a session, say once: the people search uses a third-party Apify actor that collects public
   LinkedIn profile data; LinkedIn's terms restrict scraping, so the user decides whether to use it. Costs are capped
   (about $0.02 per search plus ~$0.008 per profile on Apify's free tier; `referrals.budget_usd_per_company`).
1. Check `candidate.schools` in jobpilot.toml (`jobpilot config show --json`). If empty, ask for the exact official
   school names and add them to jobpilot.toml.
2. Company: `$ARGUMENTS`. If it is a name, JobPilot finds its LinkedIn URL from postings in the pool; if that fails,
   ask the user for the company's LinkedIn URL (https://www.linkedin.com/company/<slug>).
3. Show the plan and cost: `jobpilot net find "<company>" --dry-run --json`. Ask before spending unless the user
   already said to go ahead.
4. Run `jobpilot net run "<company>" --cards <number of cards, default 5> --json` (search, free rule filter, AI
   scoring with verbatim evidence, then cards for the best people; `--n` sets how many profiles to fetch, `--titles`
   limits the search to job titles, `--url` gives the LinkedIn page). It can take a few minutes.
5. Present the people (score, role, shared school and years, why), then each card: the connection note (with its
   character count against the LinkedIn limit), the alumni-angle variant, the message after they accept, the
   follow-up, and the cold email (subject, body and a follow-up email). The email is for an address the user found
   themselves, sent from their own mailbox with the resume attached; JobPilot adds the signature after "Best
   regards," (`referrals.email_signature`, else the candidate's name). A card made before cards had an email gets
   one with `jobpilot net email <P####> --json`; `--feedback "..."` rewrites it. Offer to adjust wording.
6. When the user reports what they did, record it: `jobpilot net mark <P####> connect_sent|email_sent|accepted|
   message_sent|replied|chat_scheduled|chat_done|referred|declined|no_response|dropped [--note ...]` (`undo` reverts a
   mistake). A referral that leads to an application: also `jobpilot app event <A###> referral --referrer <initials>`.
7. `jobpilot net due --json` lists who needs a next step (send the card, follow up after 7 days, by email in the same
   thread when the first contact was an email, give up after 21).
