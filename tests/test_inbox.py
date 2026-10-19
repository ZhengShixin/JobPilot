"""Gmail progress tracking: the brief for the skill, checked results becoming ledger events, duplicates, unmatched
emails, closed applications and invalid answers. Fictional and offline."""
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import support  # noqa: E402

from jobpilot import inbox, ledger, util  # noqa: E402

NOW = "2026-10-19T15:00:00Z"


def msg(mid, category, app_id=None, company="", subject="Your application", snippet=None, received="2026-10-18T14:00:00Z",
        deadline=None, action=False, sender="no-reply@example-ats.com", from_me=False, summary=None, next_step=None,
        interview=None):
    return {"message_id": mid, "thread_id": "t" + mid, "received_at": received, "sender": sender,
            "subject": subject, "snippet": snippet or "Thank you for applying to our team.", "category": category,
            "app_id": app_id, "company": company, "deadline": deadline, "action_required": action, "from_me": from_me,
            "summary": summary, "next_step": next_step, "interview": interview}


def agreed(time, sentence, minutes=None, rescheduled=False):
    return {"time": time, "minutes": minutes, "time_snippet": sentence, "rescheduled": rescheduled}


def results(*messages, covered="2026-10-19T15:00:00Z", complete=True):
    return {"messages": list(messages), "covered_until": covered, "complete": complete, "problems": []}


class Inbox(support.EnvCase):
    def setUp(self):
        super().setUp()
        self.env(JOBPILOT_NOW=NOW)
        self.ws = support.make_ws()
        self.acme = ledger.create(self.ws, "Acme Robotics", "Machine Learning Engineer",
                                  url="https://acme.wd5.myworkdayjobs.com/careers/job/123")
        ledger.record(self.ws, self.acme, "applied", date="2026-10-10")
        self.north = ledger.create(self.ws, "Northwind Retail", "Data Scientist")    # saved, not applied yet

    def events(self, app):
        return [t["event"] for t in ledger.state(self.ws, app)["timeline"]]

    def test_brief(self):
        b = inbox.brief(self.ws)
        self.assertEqual([a["id"] for a in b["apps"]], [self.acme, self.north])
        self.assertEqual(b["apps"][0]["url_host"], "acme.wd5.myworkdayjobs.com")
        self.assertEqual(b["window"]["after_date"], "2026-10-05")       # lookback_days = 14
        self.assertIn("after:%d" % b["window"]["after_epoch"], b["queries"][0])
        self.assertIn("greenhouse.io", b["queries"][0])
        self.assertIn('"Acme Robotics"', b["queries"][1])
        self.assertEqual(b["known_message_ids"], [])
        self.assertIn("oa_invite", b["categories"])
        self.assertEqual(b["output_schema"], inbox.results_schema())

    def test_mapping_to_ledger_events(self):
        res = inbox.apply(self.ws, results(
            msg("m1", "receipt", self.acme),
            msg("m2", "oa_invite", self.acme, subject="Complete your assessment", deadline="2026-10-25",
                snippet="Please complete the coding assessment within 7 days.", received="2026-10-18T15:00:00Z"),
            msg("m3", "interview_scheduled", self.acme, received="2026-10-18T16:00:00Z",
                snippet="Your interview is confirmed for Wednesday, October 28 at 2:00 PM ET.",
                interview=agreed("2026-10-28T14:00:00-04:00",
                                 "Your interview is confirmed for Wednesday, October 28 at 2:00 PM ET.", 45)),
            msg("m4", "other", None, snippet="New jobs that match your search")))
        self.assertEqual((res["recorded"], res["ignored"], res["unmatched"]), (3, 1, 0))
        st = ledger.state(self.ws, self.acme)
        self.assertEqual(st["stage"], "interview")
        self.assertEqual(st["deadlines"], [{"kind": "oa", "due": "2026-10-25", "done": False}])
        self.assertEqual(st["interviews"][0], {"time": "2026-10-28T14:00-04:00", "minutes": 45, "done": False,
                                               "source": "gmail"})
        evs = [e for e in ledger.events(self.ws, self.acme) if e.get("source") == "gmail"]
        self.assertEqual([e["event"] for e in evs], ["receipt", "oa_invited", "interview_invited", "interview_scheduled"])
        self.assertEqual({e["message_id"] for e in evs}, {"m1", "m2", "m3"})
        self.assertEqual(evs[0]["date"], "2026-10-18")
        self.assertEqual(inbox.status(self.ws)["messages_recorded"], 3)
        self.assertEqual(inbox.status(self.ws)["covered_until"], "2026-10-19T15:00:00+00:00")

    def test_planned_application_counts_as_applied_on_a_receipt(self):
        inbox.apply(self.ws, results(msg("r1", "receipt", self.north)))
        st = ledger.state(self.ws, self.north)
        self.assertEqual(st["stage"], "applied")
        self.assertEqual(st["applied_date"], "2026-10-18")
        inbox.apply(self.ws, results(msg("r2", "recruiter_reply", None, company="Northwind Retail",
                                         snippet="Hi Jordan, I saw your application and would love to chat.")))
        self.assertEqual(self.events(self.north)[-1], "recruiter_reply")

    def test_recruiter_reply_alone_does_not_mean_applied(self):
        inbox.apply(self.ws, results(msg("r3", "recruiter_reply", self.north, snippet="Are you open to a new role?")))
        st = ledger.state(self.ws, self.north)
        self.assertEqual(st["stage"], "planned")
        self.assertIsNone(st["applied_date"])

    def test_duplicates_are_skipped(self):
        r = results(msg("d1", "rejection", self.acme, snippet="Unfortunately, we will not be moving forward."))
        first = inbox.apply(self.ws, r)
        second = inbox.apply(self.ws, r)
        self.assertEqual((first["recorded"], second["recorded"], second["duplicates"]), (1, 0, 1))
        self.assertEqual(self.events(self.acme).count("rejected"), 1)
        self.assertIn("d1", inbox.brief(self.ws)["known_message_ids"])

    def test_unmatched_then_linked(self):
        res = inbox.apply(self.ws, results(
            msg("u1", "interview_invite", "A999", snippet="Let us find a time to talk next week."),
            msg("u2", "offer", None, company="Unknown Labs", snippet="We are delighted to offer you the position.")))
        self.assertEqual(res["unmatched"], 2)
        rows = inbox.unmatched(self.ws)
        self.assertEqual([r["message_id"] for r in rows], ["u1", "u2"])
        self.assertEqual(inbox.status(self.ws)["unmatched"], 2)
        out = inbox.link(self.ws, "u1", self.acme)
        self.assertEqual(out["events"], ["interview_invited"])
        self.assertEqual([r["message_id"] for r in inbox.unmatched(self.ws)], ["u2"])
        with self.assertRaises(util.JobPilotError):
            inbox.link(self.ws, "u1", self.acme)
        inbox.dismiss(self.ws, "u2")
        self.assertEqual(inbox.unmatched(self.ws), [])

    def test_company_match_needs_exactly_one_application(self):
        second = ledger.create(self.ws, "Acme Robotics", "Research Scientist")
        ledger.record(self.ws, second, "applied")
        res = inbox.apply(self.ws, results(msg("c1", "rejection", None, company="Acme Robotics, Inc.",
                                               snippet="We have decided to move forward with other candidates.")))
        self.assertEqual(res["unmatched"], 1)
        self.assertNotIn("rejected", self.events(self.acme))

    def test_closed_application_gets_a_note_not_progress(self):
        ledger.record(self.ws, self.acme, "rejected")
        res = inbox.apply(self.ws, results(msg("x1", "interview_invite", self.acme,
                                               snippet="We would like to invite you to interview.")))
        st = ledger.state(self.ws, self.acme)
        self.assertEqual(st["stage"], "rejected")
        self.assertIn("after the application was closed", st["notes"][-1]["note"])
        self.assertEqual(len(res["attention"]), 1)
        res = inbox.apply(self.ws, results(msg("x2", "rejection", self.acme, snippet="Unfortunately we will not proceed.")))
        self.assertEqual(self.events(self.acme).count("rejected"), 1)
        self.assertEqual(res["recorded"], 0)

    def test_no_response_becomes_the_real_rejection(self):
        ledger.record(self.ws, self.acme, "closed")
        inbox.apply(self.ws, results(msg("y1", "rejection", self.acme, snippet="Unfortunately we will not proceed.")))
        self.assertEqual(ledger.state(self.ws, self.acme)["stage"], "rejected")

    def test_recruiter_mail_waits_until_answered_or_settled(self):
        inbox.apply(self.ws, results(
            msg("q1", "recruiter_reply", self.acme, subject="A question about your application",
                snippet="Could you tell me which of our two teams interests you more?", action=True),
            msg("q2", "recruiter_reply", self.acme, snippet="Thanks, I will pass your resume on.",
                received="2026-10-18T15:00:00Z")))
        rows = inbox.waiting(self.ws)
        self.assertEqual([(r["app_id"], r["message_id"], r["count"]) for r in rows], [(self.acme, "q1", 1)])
        self.assertEqual(rows[0]["subject"], "A question about your application")
        self.assertTrue(rows[0]["can_invite"])
        self.assertIsNone(rows[0]["mail"])                      # not a Gmail message id: no link
        feed = [e for e in util.jsonl_read(self.ws.events_path) if (e.get("data") or {}).get("message_id") == "q1"]
        self.assertTrue(feed[0]["data"]["needs_reply"])
        self.assertIn("a recruiter wrote — needs your reply", feed[0]["text"])
        self.assertEqual(inbox.status(self.ws)["waiting"], 1)
        self.assertEqual(inbox.answered(self.ws, self.acme)["answered"], 1)
        self.assertEqual(inbox.waiting(self.ws), [])
        self.assertIn("q1", util.read_json(self.ws.inbox / "mail-done.json"))
        inbox.apply(self.ws, results(msg("q3", "recruiter_reply", self.acme, received="2026-10-19T10:00:00Z",
                                         snippet="One more question: are you open to a hybrid schedule?", action=True)))
        self.assertEqual([r["message_id"] for r in inbox.waiting(self.ws)], ["q3"])
        ledger.record(self.ws, self.acme, "rejected")             # a later stage answers it too
        self.assertEqual(inbox.waiting(self.ws), [])
        self.assertEqual(inbox.waiting(self.ws, days=0), [])

    def test_waiting_mail_recorded_as_an_interview_invitation(self):
        gmail_id = "1a2b3c4d5e6f7a8b"
        inbox.apply(self.ws, results(msg(gmail_id, "recruiter_reply", self.north, action=True,
                                         snippet="Could we find a time to talk about the role?")))
        rows = inbox.waiting(self.ws)
        self.assertEqual(rows[0]["mail"], "https://mail.google.com/mail/u/0/#all/" + gmail_id)
        out = inbox.invite(self.ws, self.north, by="dashboard")
        self.assertEqual(out["events"], ["applied", "interview_invited"])    # it was only saved before
        st = ledger.state(self.ws, self.north)
        self.assertEqual((st["stage"], st["applied_date"]), ("interview", "2026-10-18"))
        ev = ledger.events(self.ws, self.north)[-1]
        self.assertEqual((ev["event"], ev["source"], ev["message_id"], ev["by"]),
                         ("interview_invited", "gmail", gmail_id, "dashboard"))
        self.assertEqual(inbox.waiting(self.ws), [])
        with self.assertRaises(util.JobPilotError):
            inbox.invite(self.ws, self.north)                     # nothing waits any more

    def test_categories_name_calls_as_invitations(self):
        cats = inbox.brief(self.ws)["categories"]
        self.assertIn("introductory call", cats["interview_invite"])
        self.assertIn("without inviting", cats["recruiter_reply"])

    def test_a_conversation_by_email(self):
        """Invitation -> your reply with times -> their confirmation: the agreed time becomes the interview's."""
        inbox.apply(self.ws, results(msg("c1", "interview_invite", self.acme, sender="Pat Lee <pat@acme.example>",
                                         snippet="Could you share a few times for a 30-minute phone screen next week?",
                                         summary="They ask for times for a 30-minute phone screen next week.",
                                         next_step="pick_time", action=True)))
        due = ledger.due(self.ws)
        self.assertEqual([u["app_id"] for u in due["unscheduled"]], [self.acme])
        self.assertEqual(inbox.mail_state(self.ws, self.acme)["gist"],
                         "They ask for times for a 30-minute phone screen next week.")
        inbox.apply(self.ws, results(msg("c2", "owner_reply", self.acme, sender="Jordan <jordan@example.com>",
                                         from_me=True, received="2026-10-18T18:00:00Z",
                                         snippet="I am free Tuesday or Wednesday afternoon next week.",
                                         summary="You offered Tuesday or Wednesday afternoon; waiting for them.",
                                         next_step="wait", action=True)))
        state = inbox.mail_state(self.ws, self.acme)
        self.assertEqual(state, {"gist": "They ask for times for a 30-minute phone screen next week.",
                                 "replied_at": "2026-10-18T18:00:00Z"})      # your own mail is the "replied" line
        self.assertEqual(ledger.events(self.ws, self.acme)[-1]["event"], "note")
        self.assertFalse(inbox._seen(self.ws)["c2"]["action_required"])     # your own mail never waits on you
        inbox.apply(self.ws, results(msg("c3", "interview_scheduled", self.acme, sender="Pat Lee <pat@acme.example>",
                                         received="2026-10-19T13:00:00Z",
                                         snippet="Great, let's do Tuesday, October 27 at 3:00 PM Eastern.",
                                         summary="Phone screen set for Tuesday 3 PM; prepare.", next_step="prepare",
                                         interview=agreed("2026-10-27T15:00:00-04:00",
                                                          "Great, let's do Tuesday, October 27 at 3:00 PM Eastern.",
                                                          30))))
        evs = [e["event"] for e in ledger.events(self.ws, self.acme)]
        self.assertEqual(evs[-2:], ["note", "interview_scheduled"])          # no second invitation
        st = ledger.state(self.ws, self.acme)
        self.assertEqual((st["interviews"][0]["time"], st["interviews"][0]["minutes"]), ("2026-10-27T15:00-04:00", 30))
        self.assertEqual(ledger.due(self.ws)["unscheduled"], [])
        self.assertEqual(ledger.due(self.ws, days_ahead=30)["interviews"][0]["source"], "gmail")
        feed = {e["data"]["message_id"]: e for e in util.jsonl_read(self.ws.events_path)
                if (e.get("data") or {}).get("message_id")}
        self.assertIn("interview time agreed by email: 2026-10-27 15:00", feed["c3"]["text"])
        self.assertTrue(feed["c2"]["data"]["minor"])
        self.assertEqual(feed["c1"]["data"]["gist"], "They ask for times for a 30-minute phone screen next week.")

    def test_an_agreed_time_must_be_stated(self):
        cases = (("s1", "Looking forward to speaking with you soon!", "2026-10-27T15:00:00-04:00"),   # no hour
                 ("s2", "Let's do Tuesday at 4:00 PM.", "2026-10-27T15:00:00-04:00"),              # another hour
                 ("s3", "Let's do Wednesday at 3:00 PM.", "2026-10-27T15:00:00-04:00"),            # another day
                 ("s4", "See you on October 27 at 3pm.", "2027-10-27T15:00:00-04:00"))             # a year away
        for mid, sentence, when in cases:
            res = inbox.apply(self.ws, results(msg(mid, "interview_scheduled", self.acme, snippet=sentence,
                                                   interview=agreed(when, sentence))))
            self.assertTrue(any(mid in p and "not an agreed time" in p for p in res["problems"]), res["problems"])
        st = ledger.state(self.ws, self.acme)
        self.assertEqual((st["stage"], st["interviews"]), ("interview", []))   # recorded as invitations only
        self.assertEqual(inbox._seen(self.ws)["s1"]["category"], "interview_invite")
        mine = inbox.apply(self.ws, results(msg("s5", "interview_scheduled", self.acme, from_me=True,
                                                snippet="Tuesday at 4pm works for me.",
                                                interview=agreed("2026-10-27T15:00:00-04:00", "Tuesday at 4pm works.",
                                                                 ))))
        self.assertEqual(mine["recorded"], 1)
        self.assertEqual(inbox._seen(self.ws)["s5"]["category"], "owner_reply")
        ok = inbox.apply(self.ws, results(msg("s6", "interview_scheduled", self.acme, from_me=True,
                                              snippet="Tuesday at 3pm works for me, talk then!",
                                              interview=agreed("2026-10-27T15:00", "Tuesday at 3pm works for me"))))
        self.assertEqual(ok["agreed"][0]["time"], "2026-10-27T15:00-04:00")    # no offset: the workspace's zone

    def test_messages_you_sent(self):
        res = inbox.apply(self.ws, results(
            msg("o1", "receipt", self.acme, from_me=True, snippet="Thanks for confirming, I look forward to it."),
            msg("o2", "owner_reply", self.acme, snippet="Thanks for your time on the call today!")))
        self.assertEqual((res["recorded"], res["rejected"]), (1, 1))
        self.assertEqual(inbox._seen(self.ws)["o1"]["category"], "owner_reply")
        self.assertNotIn("o2", inbox._seen(self.ws))
        self.assertTrue(any("owner_reply from someone else" in p for p in res["problems"]))
        cfg = support.CONFIG + '\n[inbox]\nmy_addresses = ["Jordan.Rivera@Example.com"]\n'
        (self.ws.root / "jobpilot.toml").write_text(cfg)
        self.ws.reload()
        self.assertEqual(inbox.brief(self.ws)["my_addresses"], ["jordan.rivera@example.com"])
        res = inbox.apply(self.ws, results(msg("o3", "recruiter_reply", self.acme, action=True,
                                               sender="Jordan Rivera <jordan.rivera@example.com>",
                                               snippet="Happy to share more details about my projects.")))
        seen = inbox._seen(self.ws)["o3"]
        self.assertEqual((seen["category"], seen["from_me"], seen["action_required"]), ("owner_reply", True, False))

    def test_your_email_reply_settles_a_waiting_mail(self):
        inbox.apply(self.ws, results(msg("w1", "recruiter_reply", self.acme, action=True,
                                         snippet="Could you tell me your preferred start date?")))
        self.assertEqual(len(inbox.waiting(self.ws)), 1)
        inbox.apply(self.ws, results(msg("w2", "owner_reply", self.acme, from_me=True, received="2026-10-18T20:00:00Z",
                                         snippet="I could start in early January.")))
        self.assertEqual(inbox.waiting(self.ws), [])

    def test_summaries_are_cleaned(self):
        self.assertEqual(inbox.clean_summary("  They ask for two times\nnext week. "), "They ask for two times next week.")
        for bad in ("See https://example.com/apply", "Write to pat@acme.example", "Base pay $120,000",
                    "Call 555 0199", "Code 123456 inside"):
            self.assertIsNone(inbox.clean_summary(bad), bad)
        res = inbox.apply(self.ws, results(msg("g1", "recruiter_reply", self.acme, snippet="Here is the salary band.",
                                               summary="They shared a pay range of $150k.")))
        self.assertIsNone(inbox._seen(self.ws)["g1"]["summary"])
        self.assertEqual(res["recorded"], 1)

    def test_a_rescheduled_time_replaces_the_booking(self):
        ledger.record(self.ws, self.acme, "interview_invited")
        ledger.record(self.ws, self.acme, "interview_scheduled", time="2026-10-27T15:00:00-04:00")
        ledger.record(self.ws, self.acme, "interview_scheduled", time="2026-10-27T15:00-04:00")   # the same start
        self.assertEqual(len(ledger.state(self.ws, self.acme)["interviews"]), 1)
        inbox.apply(self.ws, results(msg("r1", "interview_scheduled", self.acme,
                                         snippet="Could we move it to Thursday, October 29 at 10:30 AM?",
                                         interview=agreed("2026-10-29T10:30:00-04:00",
                                                          "Could we move it to Thursday, October 29 at 10:30 AM?",
                                                          rescheduled=True))))
        ivs = ledger.state(self.ws, self.acme)["interviews"]
        self.assertEqual([iv["time"] for iv in ivs], ["2026-10-29T10:30-04:00"])
        self.assertTrue(ledger.events(self.ws, self.acme)[-1]["rescheduled"])

    def test_brief_reads_sent_mail_and_names_the_summary_language(self):
        b = inbox.brief(self.ws)
        self.assertNotIn("-in:sent", " ".join(b["queries"]))
        self.assertEqual((b["summary_language"], b["timezone"], b["my_addresses"]),
                         ("English", "America/New_York", []))
        self.assertIn("owner_reply", b["categories"])
        self.assertIn("interview", b["output_schema"]["properties"]["messages"]["items"]["properties"])

    def test_invalid_results_record_nothing(self):
        bad = results(msg("z1", "receipt", self.acme))
        bad["messages"][0]["category"] = "celebration"
        with self.assertRaises(util.JobPilotError):
            inbox.apply(self.ws, bad)
        short = results(msg("z2", "receipt", self.acme, snippet="short"))
        with self.assertRaises(util.JobPilotError):
            inbox.apply(self.ws, short)
        when = results(msg("z3", "oa_invite", self.acme, deadline="next Friday"))
        with self.assertRaises(util.JobPilotError):
            inbox.apply(self.ws, when)
        self.assertEqual([e for e in ledger.events(self.ws) if e.get("source") == "gmail"], [])
        self.assertIsNone(inbox.status(self.ws)["last_scan"])

    def test_window_follows_coverage(self):
        inbox.apply(self.ws, results(msg("w1", "receipt", self.acme), covered="2026-10-17T12:00:00Z"))
        self.assertEqual(inbox.brief(self.ws)["window"]["after_date"], "2026-10-16")
        inbox.apply(self.ws, results(covered="2026-10-01T00:00:00Z"))      # an older window never moves it back
        self.assertEqual(inbox.status(self.ws)["covered_until"], "2026-10-17T12:00:00+00:00")

    def test_cli_apply_and_status(self):
        import argparse
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="command", required=True)

        def add(name, func, needs_ws=True, **kw):
            sp = sub.add_parser(name, **kw)
            sp.add_argument("--json", action="store_true")
            sp.set_defaults(func=func, needs_ws=needs_ws)
            return sp

        inbox.register(sub, add)
        path = os.path.join(tempfile.mkdtemp(), "results.json")
        with open(path, "w") as f:
            json.dump(results(msg("k1", "receipt", self.acme)), f)
        for argv, expect in ((["inbox", "apply", path], "1 emails recorded"), (["inbox", "status"], "1 recorded"),
                             (["inbox", "brief", "--json"], '"apps"')):
            args = parser.parse_args(argv)
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                self.assertEqual(args.func(self.ws, args), 0)
            self.assertIn(expect, buf.getvalue())

    def test_headless_scan_uses_read_only_tools(self):
        fake = os.path.join(tempfile.mkdtemp(), "claude")
        log = fake + ".args"
        answer = {"type": "result", "subtype": "success", "is_error": False, "total_cost_usd": 0.02,
                  "structured_output": results(msg("h1", "receipt", self.acme))}
        with open(fake, "w") as f:
            f.write("#!/usr/bin/env python3\nimport json, sys\nopen(%r, 'w').write(json.dumps(sys.argv[1:]))\n"
                    "print(json.dumps(%r))\n" % (log, answer))
        os.chmod(fake, 0o755)
        self.env(JOBPILOT_CLAUDE=fake)
        res = inbox.scan_headless(self.ws, log=lambda *_: None)
        self.assertEqual(res["recorded"], 1)
        with open(log) as f:
            argv = json.load(f)
        allowed = argv[argv.index("--allowedTools") + 1:argv.index("--disallowedTools")]
        self.assertEqual(set(allowed), set(inbox.GMAIL_READ))
        self.assertIn("mcp__claude_ai_Gmail__send_message", argv)
        self.assertEqual(argv[argv.index("--tools") + 1], "ToolSearch")
        self.assertEqual(argv[argv.index("--setting-sources") + 1], "user")
        runs = util.jsonl_read(self.ws.claude_runs_path)
        self.assertEqual((runs[-1]["role"], runs[-1]["ok"]), ("inbox-triage", True))


if __name__ == "__main__":
    unittest.main()
