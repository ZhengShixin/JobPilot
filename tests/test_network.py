"""Alumni referral finder: triage rules, the paid search with a fake Apify, AI scores and cards with the fake claude,
marks and next steps. Everything is fictional and offline."""
import contextlib
import io
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import support  # noqa: E402

from jobpilot import apify, budget, ledger, network, util  # noqa: E402

NOW = "2026-10-19T15:00:00Z"
URL = "https://www.linkedin.com/company/acme-robotics"


def emp(first, last, *, title="Machine Learning Engineer", company="Acme Robotics", slug="acme-robotics",
        school="Lakeside State University", degree="Master of Science", field="Computer Science", edu=(2019, 2021),
        start=(2023, 1), country="US", headline=None, education=None, experience=None, public=None):
    """One actor item in the harvestapi/linkedin-company-employees output format."""
    public = public or ("%s-%s" % (first, last)).lower()
    edu_rows = education if education is not None else [
        {"schoolName": school, "schoolId": "999", "degree": degree, "fieldOfStudy": field,
         "startDate": {"year": edu[0]}, "endDate": {"year": edu[1]}}]
    return {"firstName": first, "lastName": last, "publicIdentifier": public,
            "linkedinUrl": "https://www.linkedin.com/in/%s" % public,
            "headline": headline or "%s at %s" % (title, company), "emails": ["hidden@example.com"],
            "photo": "https://example.com/p.jpg", "about": "Long text that must not be stored",
            "location": {"linkedinText": "New York, New York, United States",
                         "parsed": {"text": "New York, NY", "countryCode": country}},
            "currentPosition": [{"position": title, "companyName": company, "companyUniversalName": slug,
                                 "startDate": {"year": start[0], "month": start[1]}, "duration": "2 yrs"}],
            "experience": experience or [{"position": title, "companyName": company, "companyUniversalName": slug,
                                          "startDate": {"year": start[0], "month": start[1]}}],
            "education": edu_rows}


def run_cli(ws, *argv):
    """The `net` commands through a parser built the way cli.parser() builds feature commands."""
    import argparse
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)

    def add(name, func, needs_ws=True, **kw):
        sp = sub.add_parser(name, **kw)
        sp.add_argument("--json", action="store_true")
        sp.set_defaults(func=func, needs_ws=needs_ws)
        return sp

    network.register(sub, add)
    args = parser.parse_args(list(argv))
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = args.func(ws, args)
    return code, buf.getvalue()


TARGET = {"key": "acmerobotics", "name": "Acme Robotics", "slug": "acme-robotics", "url": URL}
SCHOOLS = ["Lakeside State University", "Northfield Institute of Technology"]


def triage(item, **kw):
    p = network.whitelist(item)
    return network.triage(p, TARGET, kw.pop("schools", SCHOOLS), kw.pop("today", "2026-10-19"), **kw)


class Triage(unittest.TestCase):
    def test_kept_with_a_shared_school(self):
        self.assertEqual(triage(emp("Ana", "Lopez")), ("kept", ""))

    def test_look_alike_schools_do_not_match(self):
        self.assertEqual(triage(emp("Ben", "Ode", school="Lakeside State University Online"))[0], "skip")
        self.assertEqual(triage(emp("Ben", "Ode", school="Lakeside State University, Eastport"))[0], "skip")
        self.assertEqual(triage(emp("Ben", "Ode", school="Lakeside State"))[0], "skip")

    def test_sub_school_separator_and_case_match(self):
        self.assertEqual(triage(emp("Cy", "Ng", school="Lakeside State University - College of Engineering"))[0], "kept")
        self.assertEqual(triage(emp("Cy", "Ng", school="THE LAKESIDE STATE UNIVERSITY"))[0], "kept")
        self.assertEqual(triage(emp("Cy", "Ng", school="Northfield Institute of Technology (NIT)"))[0], "kept")

    def test_alias(self):
        item = emp("Di", "Ray", school="LSU")
        self.assertEqual(triage(item)[0], "skip")
        self.assertEqual(triage(item, aliases={"Lakeside State University": ["LSU"]})[0], "kept")
        p = network.whitelist(item)
        names = network.school_names(SCHOOLS, {"Lakeside State University": ["LSU"]})
        self.assertEqual(network.matched_schools(p, names), (["Lakeside State University"], []))

    def test_worked_at_school_without_degree(self):
        item = emp("Eve", "Kim", school="Other College", experience=[
            {"position": "Research Assistant", "companyName": "Lakeside State University", "startDate": {"year": 2018}},
            {"position": "Machine Learning Engineer", "companyName": "Acme Robotics", "startDate": {"year": 2023}}])
        status, reason = triage(item)
        self.assertEqual(status, "kept")
        self.assertIn("worked at Lakeside State University", reason)

    def test_current_student_and_part_time_degree(self):
        self.assertEqual(triage(emp("Fay", "Wu", edu=(2024, 2028), start=(2026, 6)))[0], "skip")
        self.assertIn("still studying", triage(emp("Fay", "Wu", edu=(2024, 2028), start=(2026, 6)))[1])
        self.assertEqual(triage(emp("Fay", "Wu", edu=(2024, 2028), start=(2024, 9)))[0], "kept")   # 25 months in job
        self.assertEqual(triage(emp("Gus", "Bo", edu=(2022, 2026), start=(2026, 7)))[0], "kept")   # graduated in June

    def test_intern_recruiter_and_unrelated(self):
        self.assertEqual(triage(emp("Hal", "Ito", title="Machine Learning Intern"))[0], "skip")
        self.assertEqual(triage(emp("Ira", "Moe", title="Technical Recruiter"))[0], "recruiter")
        self.assertEqual(triage(emp("Ira", "Moe", title="University Relations Partner"))[0], "recruiter")
        self.assertEqual(triage(emp("Jo", "Pak", title="Senior Legal Counsel"))[0], "skip")
        self.assertEqual(triage(emp("Jo", "Pak", title="Senior Legal Counsel"), track_words="legal operations")[0],
                         "kept")

    def test_not_at_company_or_outside_country(self):
        moved = emp("Kai", "Lee", company="Other Co", slug="other-co")
        self.assertEqual(triage(moved), ("skip", "not at Acme Robotics now"))
        abroad = emp("Lu", "Xu", country="DE")
        self.assertEqual(triage(abroad, locations=["United States"])[0], "skip")
        self.assertEqual(triage(abroad, locations=["San Francisco"])[0], "kept")   # unknown country name: no check

    def test_whitelist_drops_everything_else(self):
        p = network.whitelist(emp("Mo", "Tan"))
        self.assertNotIn("emails", p)
        self.assertNotIn("about", p)
        self.assertNotIn("photo", json.dumps(p))
        self.assertEqual(p["education"][0]["end"], "2021")
        self.assertEqual(p["current"][0]["start"], "2023-01")


class Base(support.EnvCase):
    def setUp(self):
        super().setUp()
        self.env(JOBPILOT_NOW=NOW)
        self.ws = support.make_ws()

    def fake(self, rows, **kw):
        return apify.FakeApify(datasets={"acmerobotics": rows}, **kw)

    def find(self, rows, **kw):
        client = kw.pop("client", None) or self.fake(rows)
        res = network.find(self.ws, "Acme Robotics", url=URL, client=client, log=lambda *_: None, poll_seconds=0, **kw)
        return res, client

    def people(self):
        return {p["name"]: p for p in network.people(self.ws, "acmerobotics")}


ROWS = [emp("Ana", "Lopez"), emp("Ira", "Moe", title="Technical Recruiter"),
        emp("Ben", "Ode", school="Lakeside State University Online")]


class Find(Base):
    def test_schema_check_refuses_before_paying(self):
        client = self.fake(ROWS, schema={"companies": {}, "maxItems": {}, "profileScraperMode": {}})
        with self.assertRaises(util.JobPilotError) as ctx:
            self.find(ROWS, client=client)
        self.assertIn("schools", str(ctx.exception))
        self.assertEqual(client.started, [])
        self.assertEqual(util.jsonl_read(self.ws.budget_path), [])

    def test_find_stores_raw_merges_people_and_records_the_search(self):
        res, client = self.find(ROWS)
        self.assertEqual((res["profiles"], res["kept"], res["recruiters"], res["skipped"]), (3, 1, 1, 1))
        inp = client.started[0]["input"]
        self.assertEqual(inp["companies"], [URL])
        self.assertEqual(inp["schools"], SCHOOLS)
        self.assertEqual(inp["locations"], ["United States"])
        self.assertEqual(inp["profileScraperMode"], "Full ($8 per 1k)")
        self.assertEqual(inp["maxItems"], 30)
        self.assertAlmostEqual(client.started[0]["cap"], 0.40)
        raw = list((self.ws.network / "raw").glob("acme-robotics-*.json"))
        self.assertEqual(len(raw), 1)
        self.assertEqual(len(json.loads(raw[0].read_text())), 3)
        ppl = self.people()
        self.assertEqual(ppl["Ana Lopez"]["triage"], "kept")
        self.assertEqual(ppl["Ana Lopez"]["schools"], ["Lakeside State University"])
        self.assertEqual(ppl["Ira Moe"]["triage"], "recruiter")
        self.assertNotIn("emails", json.dumps(ppl))
        comp = network.companies(self.ws)[0]
        self.assertEqual((comp["name"], comp["slug"], comp["url"]), ("Acme Robotics", "acme-robotics", URL))
        self.assertEqual(comp["searches"][0]["profiles"], 3)
        self.assertEqual(comp["counts"]["kept"], 1)
        spent = budget.spent(self.ws, kind="referrals")
        self.assertAlmostEqual(spent, res["cost"])
        self.assertGreater(spent, 0)

    def test_second_search_keeps_marks_and_pids(self):
        _, client = self.find(ROWS)
        ana = self.people()["Ana Lopez"]
        network.mark(self.ws, ana["pid"], "connect_sent")
        changed = emp("Ana", "Lopez", title="Senior Machine Learning Engineer")
        client.datasets["acmerobotics"] = [changed, emp("Nia", "Cole", school="Northfield Institute of Technology")]
        res, _ = self.find([], client=client)
        self.assertEqual(res["new"], 1)
        ppl = self.people()
        self.assertEqual(ppl["Ana Lopez"]["pid"], ana["pid"])
        self.assertEqual(ppl["Ana Lopez"]["stage"], "connect_sent")
        self.assertEqual(ppl["Ana Lopez"]["title"], "Senior Machine Learning Engineer")
        self.assertEqual(ppl["Nia Cole"]["pid"], "P0004")
        self.assertEqual(len(network.companies(self.ws)[0]["searches"]), 2)

    def test_cap_follows_the_budget_left_today(self):
        cfg = support.CONFIG.replace("daily_budget_usd = 1.00", "daily_budget_usd = 0.10")
        self.ws = support.make_ws(config_text=cfg)
        res, client = self.find(ROWS)
        self.assertAlmostEqual(client.started[0]["cap"], 0.10)
        self.assertEqual(client.started[0]["input"]["maxItems"], 10)   # (0.10 - start fee) / $0.008

    def test_refuses_when_too_little_budget_is_left(self):
        cfg = support.CONFIG.replace("daily_budget_usd = 1.00", "daily_budget_usd = 0.04")
        self.ws = support.make_ws(config_text=cfg)
        client = self.fake(ROWS)
        with self.assertRaises(util.JobPilotError):
            self.find(ROWS, client=client)
        self.assertEqual(client.started, [])

    def test_definitive_rejection_releases_the_reservation(self):
        client = apify.FakeApify(datasets={}, fail={"acmerobotics": 402})
        with self.assertRaises(util.JobPilotError):
            self.find(ROWS, client=client)
        self.assertEqual(budget.spent(self.ws), 0)

    def test_company_url_comes_from_the_pool(self):
        self.ws.db.put({"id": "4000000001", "title": "ML Engineer", "company": "Acme Robotics",
                        "company_slug": "acme-robotics", "status": "ready", "stage": "reviewed"})
        client = self.fake(ROWS)
        network.find(self.ws, "Acme Robotics", client=client, log=lambda *_: None, poll_seconds=0)
        self.assertEqual(client.started[0]["input"]["companies"], [URL])

    def test_unknown_company_and_missing_schools(self):
        with self.assertRaises(util.JobPilotError) as ctx:
            network.find(self.ws, "Nowhere Inc", client=self.fake([]), log=lambda *_: None)
        self.assertIn("--url", str(ctx.exception))
        self.ws = support.make_ws(config_text=support.CONFIG.replace(
            'schools = ["Lakeside State University", "Northfield Institute of Technology"]', "schools = []"))
        with self.assertRaises(util.JobPilotError) as ctx:
            self.find(ROWS)
        self.assertIn("candidate.schools", str(ctx.exception))

    def test_email_mode_and_dry_run(self):
        cfg = support.CONFIG + '\n[referrals]\nscraper_mode = "Full + email search ($12 per 1k)"\n'
        self.ws = support.make_ws(config_text=cfg)
        with self.assertRaises(util.JobPilotError):
            self.find(ROWS)
        self.ws = support.make_ws()
        client = self.fake(ROWS)
        res = network.find(self.ws, "Acme Robotics", url=URL, client=client, dry_run=True)
        self.assertTrue(res["dry_run"])
        self.assertEqual(client.started, [])
        self.assertAlmostEqual(res["estimate_usd"], 0.26)


REVIEW = r'''
import re
def respond(task, system, schema):
    out = []
    for pid, body in re.findall(r'<untrusted source="linkedin" pid="(P\d+)">\n(.*?)\n</untrusted>', task, re.S):
        lines = body.splitlines()
        now = next(l for l in lines if l.startswith("now: "))
        edu = next((l for l in lines if l.startswith("education: ")), lines[0])
        bad = "Moe" in body
        out.append({"pid": pid, "role": 4, "alumni": 5, "path": 3, "reply": 5, "help": 3,
                    "evidence": {"role": now, "alumni": "an invented quote" if bad else edu, "path": edu,
                                 "reply": now, "help": now},
                    "why": "Shares the candidate's school and works on ML.", "team_hint": "",
                    "injection_suspected": False})
    return {"people": out}
'''

WRITER = r'''
def respond(task, system, schema):
    if "note" not in schema.get("properties", {}):          # write_email: the cold email fields only
        out = dict(EMAIL)
        if "shorter subject please" in task:
            out["email_subject"] = "Lakeside State alum with a question for you"
        return out
    fixed = "previous draft broke these checks" in task
    note = ("Hi Ana, I am a fellow Lakeside State graduate working on ranking models and would value connecting "
            "with you. Jordan")
    if not fixed:
        note = "Hi Ana, could you help with my visa? " + "x" * 220
    return {"note": note,
            "note_alumni": "Hi Ana, fellow Lakeside State alum here: I would love to hear how you moved into ML at "
                           "Acme Robotics. Jordan",
            "message": "Thanks for connecting, Ana. I build ranking models in PyTorch at Brightline Analytics and I am "
                       "exploring ML engineering roles at Acme Robotics. Would you have 15 minutes for advice?",
            "followup": "Hi Ana, just following up on my note from last week. No pressure at all. Jordan",
            "questions": ["What does a typical week look like on your team at Acme Robotics?",
                          "How did your degree at Lakeside State prepare you for this role?",
                          "Which skills matter most for new ML engineers here?",
                          "How do teams decide which models go to production?",
                          "How did you join Acme Robotics, and do referrals help new grads?"],
            "why": "Same school and the same function as the open role.",
            "hooks": ["Lakeside State M.S. Computer Science"], "avoid": [], **EMAIL}
'''

EMAIL = {"email_subject": "Lakeside State alum curious about ML engineering at Acme Robotics",
         "email_body": ("Hi Ana,\n\nI noticed we both studied at Lakeside State, and your work on machine learning at "
                        "Acme Robotics is close to what I hope to do next. I build ranking models in PyTorch at "
                        "Brightline Analytics and I am exploring ML engineering roles at Acme Robotics.\n\nHow does "
                        "your team decide which models go to production? Would you have 15 minutes for a short call in "
                        "the next week or two? I have attached my resume in case it is useful. Thank you for "
                        "considering it.\n\nBest regards,"),
         "email_followup": ("Hi Ana,\n\nI wanted to follow up on my note from last week in case it got buried. A few "
                            "lines of advice would mean a lot.\n\nBest regards,")}
EMAIL_TEXT = "EMAIL = %r\n" % (EMAIL,)
WRITER = EMAIL_TEXT + WRITER


class ScoreCards(Base):
    def setUp(self):
        super().setUp()
        self.env(**support.fake_claude_env({"net-review": REVIEW, "net-writer": WRITER}))
        self.find(ROWS)

    def calls(self):
        path = os.path.join(os.environ["FAKE_CLAUDE_DIR"], "calls.jsonl")
        if not os.path.exists(path):
            return []
        with open(path) as f:
            return [json.loads(x) for x in f]

    def test_score_checks_quotes_and_computes_totals(self):
        res = network.score(self.ws, "Acme Robotics", log=lambda *_: None)
        self.assertEqual((res["candidates"], res["scored"], res["failed"]), (2, 1, 1))
        ppl = self.people()
        ana = ppl["Ana Lopez"]
        self.assertEqual(ana["sub"], {"role": 4, "alumni": 5, "path": 3, "reply": 5, "help": 3})
        self.assertEqual(ana["score"], round(100 * (.3 * 4 + .25 * 5 + .15 * 3 + .15 * 5 + .15 * 3) / 5))
        self.assertIsNone(ppl["Ira Moe"].get("score"))
        self.assertIn("alumni", ppl["Ira Moe"]["score_error"])
        self.assertEqual(ppl["Ira Moe"]["score_attempts"], 1)
        self.assertIsNone(ppl["Ben Ode"].get("score"))   # skipped by the rules: never sent to the AI
        self.assertEqual(self.calls()[0]["role"], "net-review")

    def test_cards_are_checked_and_regenerated_once(self):
        network.score(self.ws, "Acme Robotics", log=lambda *_: None)
        res = network.cards(self.ws, "Acme Robotics", log=lambda *_: None)
        self.assertEqual((res["cards"], res["warnings"]), (1, 0))
        writer_calls = [c for c in self.calls() if c["role"] == "net-writer"]
        self.assertEqual(len(writer_calls), 2)
        ana = network.person(self.ws, self.people()["Ana Lopez"]["pid"])
        self.assertEqual(ana["stage"], "card_ready")
        self.assertEqual(ana["card"]["warnings"], [])
        self.assertNotIn("visa", ana["card"]["note"])
        self.assertLessEqual(len(ana["card"]["note"]), 200)
        self.assertEqual(len(ana["card"]["questions"]), 5)

    def test_a_card_over_the_limit_twice_is_not_stored(self):
        network.score(self.ws, "Acme Robotics", log=lambda *_: None)
        self.env(**support.fake_claude_env({"net-writer": WRITER.replace('"previous draft broke these checks"',
                                                                         '"never present"')}))
        res = network.cards(self.ws, "Acme Robotics", log=lambda *_: None)
        self.assertEqual((res["cards"], res["failed"]), (0, 1))   # the schema caps the note at 200 characters
        ana = self.people()["Ana Lopez"]
        self.assertEqual(ana["stage"], "new")
        self.assertTrue(ana["card_error"])

    def test_a_card_that_keeps_breaking_a_rule_is_stored_with_warnings(self):
        network.score(self.ws, "Acme Robotics", log=lambda *_: None)
        sticky = WRITER.replace('"Hi Ana, could you help with my visa? " + "x" * 220',
                                '"Hi Ana, could you help with my visa? Jordan"').replace(
            '"previous draft broke these checks"', '"never present"')
        self.env(**support.fake_claude_env({"net-writer": sticky}))
        res = network.cards(self.ws, "Acme Robotics", log=lambda *_: None)
        self.assertEqual((res["cards"], res["warnings"]), (1, 1))
        ana = network.person(self.ws, self.people()["Ana Lopez"]["pid"])
        self.assertTrue(any("sensitive" in w for w in ana["card"]["warnings"]))

    def test_lint_finds_problems(self):
        p = self.people()["Ana Lopez"]
        good = {"note": "Hi Ana, fellow Lakeside State alum here. Jordan", "note_alumni": "Hello Ana, I study ML. Jordan",
                "message": "m" * 90, "followup": "Hi Ana, following up on my note. Jordan",
                "questions": ["What does your team at Acme Robotics work on?"], **EMAIL}
        self.assertEqual(network.lint_card(self.ws, good, p, "", support.PROFILE), [])
        bad = dict(good, note="Hi Ana, I need visa sponsorship, see https://x.io and [Company]. I improved revenue "
                              "by 4,500 units using Scala.")
        problems = " | ".join(network.lint_card(self.ws, bad, p, "", support.PROFILE))
        for word in ("sensitive", "link", "placeholder", "4,500", "Scala"):
            self.assertIn(word, problems)
        self.assertNotIn("38", problems)
        same = dict(good, note_alumni="Hi Ana, fellow Lakeside State alum here! Jordan")
        self.assertIn("note and note_alumni say the same thing", network.lint_card(self.ws, same, p, "", support.PROFILE))


    def test_lint_checks_the_cold_email(self):
        p = self.people()["Ana Lopez"]
        lint = lambda **kw: " | ".join(network.lint_card(self.ws, dict(EMAIL, **kw), p, "", support.PROFILE,  # noqa
                                                          email_only=True))
        self.assertEqual(lint(), "")
        problems = lint(email_body="Hi Ana, could we talk sometime next week? Thanks so much for your time!")
        self.assertIn("must end with 'Best regards,'", problems)
        self.assertIn("does not say the resume is attached", problems)
        self.assertIn("email_subject: missing", lint(email_subject=""))
        self.assertIn("link", lint(email_followup="See https://example.com for more.\n\nBest regards,"))
        self.assertIn("sensitive", lint(email_subject="Lakeside State alum asking about visa sponsorship"))

    def test_cards_carry_a_cold_email(self):
        network.score(self.ws, "Acme Robotics", log=lambda *_: None)
        network.cards(self.ws, "Acme Robotics", log=lambda *_: None)
        c = network.person(self.ws, self.people()["Ana Lopez"]["pid"])["card"]
        self.assertEqual((c["version"], c["email_subject"]), (1, EMAIL["email_subject"]))
        self.assertTrue(c["email_body"].endswith("Best regards,"))
        self.assertEqual(network.email_signature(self.ws), ["Jordan Rivera"])

    def test_write_email_for_an_older_card(self):
        network.score(self.ws, "Acme Robotics", log=lambda *_: None)
        network.cards(self.ws, "Acme Robotics", log=lambda *_: None)
        pid = self.people()["Ana Lopez"]["pid"]
        legacy = {k: v for k, v in network.card(self.ws, pid).items() if not k.startswith("email_") and k != "version"}
        util.atomic_write_json(self.ws.network / "cards" / ("%s.json" % pid), legacy)
        c = network.write_email(self.ws, pid, feedback="shorter subject please", log=lambda *_: None)
        self.assertEqual((c["version"], c["email_subject"]), (2, "Lakeside State alum with a question for you"))
        self.assertEqual(c["note"], legacy["note"])                          # the LinkedIn texts stay
        self.assertEqual(c["email_feedback"], "shorter subject please")
        self.assertEqual(util.read_json(self.ws.network / "cards" / ("%s.v1.json" % pid)), legacy)
        code, text = run_cli(self.ws, "net", "email", pid)
        self.assertEqual(code, 0)
        self.assertIn("Subject: " + EMAIL["email_subject"], text)
        self.assertIn("Best regards,\nJordan Rivera", text)
        self.assertEqual(network.card(self.ws, pid)["version"], 3)
        cfg = support.CONFIG + '\n[referrals]\nemail_signature = "Jordan Rivera\\nML engineer"\n'
        (self.ws.root / "jobpilot.toml").write_text(cfg)
        self.ws.reload()
        self.assertEqual(network.email_signature(self.ws), ["Jordan Rivera", "ML engineer"])
        with self.assertRaises(util.JobPilotError):
            network.write_email(self.ws, self.people()["Ira Moe"]["pid"])    # no card yet


class Marks(Base):
    def setUp(self):
        super().setUp()
        self.find(ROWS)
        self.ana = self.people()["Ana Lopez"]["pid"]

    def test_mark_and_undo(self):
        network.mark(self.ws, self.ana, "connect_sent", note="sent Monday")
        network.mark(self.ws, self.ana, "accepted")
        self.assertEqual(network.person(self.ws, self.ana)["stage"], "accepted")
        p = network.mark(self.ws, self.ana, "undo")
        self.assertEqual(p["stage"], "connect_sent")
        network.mark(self.ws, self.ana, "undo")
        self.assertEqual(network.person(self.ws, self.ana)["stage"], "new")
        with self.assertRaises(util.JobPilotError):
            network.mark(self.ws, self.ana, "undo")
        with self.assertRaises(util.JobPilotError):
            network.mark(self.ws, self.ana, "hugged")
        evs = util.jsonl_read(self.ws.network / "events.jsonl")
        self.assertEqual([e["event"] for e in evs], ["connect_sent", "accepted", "undo", "undo"])
        self.assertEqual(util.jsonl_verify(self.ws.network / "events.jsonl"), [])

    def test_referral_reaches_the_only_open_application(self):
        app = ledger.create(self.ws, "Acme Robotics", "Machine Learning Engineer")
        ledger.record(self.ws, app, "applied")
        network.mark(self.ws, self.ana, "referred")
        st = ledger.state(self.ws, app)
        self.assertEqual(st["referrer"], "A.L.")
        network.mark(self.ws, self.ana, "undo")
        self.assertIn("undone", ledger.state(self.ws, app)["notes"][-1]["note"])

    def test_due(self):
        network._update_people(self.ws, {self.ana: {"stage": "card_ready", "card_at": "2026-10-19T10:00:00+00:00"}})
        self.assertEqual(network.due(self.ws)[0]["stage"], "card_ready")
        network.mark(self.ws, self.ana, "connect_sent")
        self.assertEqual(network.due(self.ws), [])
        self.env(JOBPILOT_NOW="2026-11-12T15:00:00Z")
        self.assertIn("no answer", network.due(self.ws)[0]["action"])
        network.mark(self.ws, self.ana, "accepted")
        network.mark(self.ws, self.ana, "message_sent")
        self.assertEqual(network.due(self.ws), [])
        self.env(JOBPILOT_NOW="2026-11-20T15:00:00Z")
        self.assertIn("follow-up", network.due(self.ws)[0]["action"])

    def test_email_sent_and_its_follow_up(self):
        network._update_people(self.ws, {self.ana: {"stage": "card_ready", "card_at": "2026-10-19T10:00:00+00:00"}})
        p = network.mark(self.ws, self.ana, "email_sent", note="to a public address")
        self.assertEqual(p["stage"], "emailed")
        self.assertEqual(network.due(self.ws), [])
        self.env(JOBPILOT_NOW="2026-10-27T15:00:00Z")
        self.assertIn("follow-up email from the card in the same thread", network.due(self.ws)[0]["action"])
        network.mark(self.ws, self.ana, "replied")
        self.assertEqual(network.due(self.ws)[0]["stage"], "replied")

    def test_remove_and_restore_hide_people(self):
        network.remove_company(self.ws, "Acme Robotics", "not interested")
        self.assertEqual(network.people(self.ws), [])
        self.assertTrue(network.companies(self.ws)[0]["removed"])
        self.assertEqual(len(network.people(self.ws, "acmerobotics")), 3)
        network.restore_company(self.ws, "Acme Robotics")
        self.assertEqual(len(network.people(self.ws)), 3)

    def test_cli_list_show_mark(self):
        code, text = run_cli(self.ws, "net", "list", "Acme Robotics", "--json")
        self.assertEqual(code, 0)
        self.assertEqual([r["name"] for r in json.loads(text)], ["Ana Lopez", "Ira Moe"])
        code, text = run_cli(self.ws, "net", "list", "--all", "--json")
        self.assertEqual(len(json.loads(text)), 3)
        shown = json.loads(run_cli(self.ws, "net", "show", self.ana, "--json")[1])
        self.assertIsNone(shown["card"])
        self.assertTrue(any(line.startswith("education: Lakeside") for line in shown["profile_lines"]))
        code, text = run_cli(self.ws, "net", "mark", self.ana, "connect_sent", "--note", "hello")
        self.assertIn("connect_sent", text)
        code, text = run_cli(self.ws, "net", "companies")
        self.assertIn("Acme Robotics", text)
        code, text = run_cli(self.ws, "net", "find", "Acme Robotics", "--url", URL, "--dry-run", "--json")
        self.assertTrue(json.loads(text)["dry_run"])


if __name__ == "__main__":
    unittest.main()
