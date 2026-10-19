"""Core pipeline: config checks, collection under budget caps, coarse filter, evidence-checked review, queue, ledger,
plan check, report, schedule files and the CLI."""
import contextlib
import io
import json
import os
import unittest

import support
from jobpilot import (apify, brief, budget, cli, config, ledger, plancheck, pool, querymatch, review, schedule,
                      scoring, search, triage, util, workspace)

REVIEWER = '''
import re
GOOD = {
  "mle": {"category": "model_training", "gaps": [], "components": {
     "duties_4": {"score": 3.5, "reason": "Trains and ships ranking models.", "jd_evidence": ["build model training pipelines in PyTorch"], "profile_evidence": []},
     "background_3": {"score": 2.5, "reason": "Production ranking models.", "jd_evidence": ["2+ years of experience building machine learning models"], "profile_evidence": ["trained gradient-boosted and transformer ranking models in PyTorch"]},
     "skills_2": {"score": 2, "reason": "Python, PyTorch, SageMaker.", "jd_evidence": ["AWS SageMaker is a plus"], "profile_evidence": ["Skills: Python, PyTorch, scikit-learn, SQL, Docker, AWS SageMaker, Git."]},
     "seniority_1": {"score": 1, "reason": "About two years.", "jd_evidence": ["2+ years of experience"], "profile_evidence": ["About 2 years of industry experience."]}}},
  "ds": {"category": "experimentation", "gaps": ["No growth analytics"], "components": {
     "duties_4": {"score": 2.0, "reason": "Mostly A/B tests.", "jd_evidence": ["Design and analyze A/B tests"], "profile_evidence": []},
     "background_3": {"score": 1.0, "reason": "Internship only.", "jd_evidence": ["experience with hypothesis testing and regression"], "profile_evidence": ["A/B test analysis and demand forecasting in Python"]},
     "skills_2": {"score": 1.5, "reason": "Python and SQL.", "jd_evidence": ["strong SQL and Python"], "profile_evidence": ["Python, PyTorch, scikit-learn, SQL"]},
     "seniority_1": {"score": 1, "reason": "Junior role fits.", "jd_evidence": ["1-3 years of experience"], "profile_evidence": ["About 2 years of industry experience."]}}},
}
def respond(task, system, schema):
    out = []
    for i in re.findall(r'listing_id="([^"]+)"', task):
        track = "ds" if i.endswith("4") else "mle"
        a = GOOD[track]
        if i.endswith("9"):  # a fabricated quote: must be rejected by the code
            a = {**a, "components": {**a["components"], "skills_2": {"score": 2, "reason": "Claims Kubernetes.",
                 "jd_evidence": ["AWS SageMaker is a plus"], "profile_evidence": ["Kubernetes expert with 5 years"]}}}
        out.append({"listing_id": i, "include": True, "track": track, "reason": "Fits the track by its duties.",
                    "evidence": ["Requirements:"] if False else (["build model training pipelines in PyTorch"] if track == "mle" else ["Design and analyze A/B tests"]),
                    "intermediary": False, "end_employer": "", "careers_url": "", "eligibility_flags": [],
                    "injection_suspected": False, "assessment": a})
    return {"reviews": out}
'''


def rows():
    return {
        "mle-core": [support.item("4000000001", "Machine Learning Engineer", "Acme Robotics", support.JD_MLE,
                                  salary_max=180000),
                     support.item("4000000002", "Machine Learning Engineer Intern", "Acme Robotics", support.JD_INTERN,
                                  employment="INTERN"),
                     support.item("4000000003", "ML Engineer, Vision", "Defense Co", support.JD_CITIZEN),
                     support.item("4000000005", "Machine Learning Engineer", "Bluefin Labs", support.JD_MLE),
                     support.item("4000000009", "Machine Learning Engineer II", "Cobalt Systems",
                                  support.JD_MLE + "\nTeam: platform.")],
        "ds-core": [support.item("4000000004", "Data Scientist", "Northwind Retail", support.JD_DS, salary_max=150000),
                    support.item("4000000006", "Senior Sales Data Scientist", "Northwind Retail", support.JD_DS,
                                 experience="10+")],
    }


class CoreTest(support.EnvCase):
    def setUp(self):
        super().setUp()
        self.env(JOBPILOT_NOW="2026-10-19T12:00:00Z", JOBPILOT_SCHEDULE_DRY="1")
        self.ws = support.make_ws()

    # ------------------------------------------------------------ config
    def test_config_checks(self):
        self.assertEqual([m for lvl, m in config.check(self.ws.cfg, self.ws) if lvl == "error"], [])
        bad = support.CONFIG.replace('track = "ds"', 'track = "nope"').replace('budget_usd = 0.30', 'budget_usd = 0')
        (self.ws.root / "jobpilot.toml").write_text(bad)
        errs = [m for lvl, m in config.check(self.ws.reload(), self.ws) if lvl == "error"]
        self.assertTrue(any("nope" in m for m in errs))
        self.assertTrue(any("budget_usd" in m for m in errs))
        self.assertEqual(config.track_ids(support_cfg()), ["mle", "ds"])

    # ------------------------------------------------------------ collection
    def test_collect_ingests_and_respects_caps(self):
        client = apify.FakeApify(datasets=rows())
        res = search.collect(self.ws, client=client, poll_seconds=0, log=lambda *_: None)
        self.assertEqual(res["rows"], 7)
        self.assertEqual(res["new"], 7)
        self.assertTrue(res["complete"])
        caps = {s["input"]["titleSearch"][0]: s["cap"] for s in client.started}
        self.assertEqual(caps["Machine Learning Engineer"], 0.40)
        inp = client.started[0]["input"]
        self.assertEqual(inp["aiEmploymentTypeFilter"], ["FULL_TIME"])
        self.assertEqual(inp["locationSearch"], ["United States"])
        self.assertIn("datePostedAfter", inp)
        self.assertTrue(search.read_jd(self.ws, "4000000001").startswith("About the role"))
        self.assertLessEqual(budget.spent(self.ws, day=self.ws.today()), 1.0)
        # a second collection the same day reuses the manifest and pays nothing
        again = search.collect(self.ws, client=client, poll_seconds=0, log=lambda *_: None)
        self.assertEqual(len(client.started), 2)
        self.assertEqual(again["rows"], 7)

    def test_budget_skips_groups(self):
        util.jsonl_append(self.ws.budget_path, {"op": "reserve", "slot": "x", "usd": 0.99, "day": self.ws.today(),
                                                "kind": "search"})
        client = apify.FakeApify(datasets=rows())
        res = search.collect(self.ws, client=client, poll_seconds=0, log=lambda *_: None)
        self.assertEqual(client.started, [])
        self.assertEqual(sorted(res["skipped"]), ["ds-core", "mle-core"])
        self.assertFalse(res["complete"])

    def test_refused_and_uncertain_launches(self):
        client = apify.FakeApify(datasets=rows(), fail={"mle-core": 402})
        res = search.collect(self.ws, client=client, poll_seconds=0, log=lambda *_: None)
        self.assertIn("mle-core", res["skipped"])
        self.assertEqual(budget.spent(self.ws, day=self.ws.today()), round(client.started and
                                                                             client._runs["fake0001"]["usageTotalUsd"] or 0, 4))

        class Timeout(apify.FakeApify):
            def start(self, *a, **kw):
                raise apify.ApifyError("network error")
        ws2 = support.make_ws()
        res2 = search.collect(ws2, client=Timeout(datasets=rows()), poll_seconds=0, log=lambda *_: None)
        m = util.read_json(ws2.runs / ws2.today() / "manifest.json")
        self.assertEqual(m["groups"]["mle-core"]["status"], "LAUNCHING")
        self.assertNotIn("ds-core", m["groups"])  # nothing else is paid for while a launch is uncertain
        self.assertFalse(res2["complete"])

    def test_pause_refuses_paid_runs(self):
        util.atomic_write_text(self.ws.data / "PAUSE", "test\n")
        with self.assertRaises(util.JobPilotError):
            search.collect(self.ws, client=apify.FakeApify(datasets=rows()), poll_seconds=0, log=lambda *_: None)

    # ------------------------------------------------------------ filter + review
    def _collect_and_review(self):
        search.collect(self.ws, client=apify.FakeApify(datasets=rows()), poll_seconds=0, log=lambda *_: None)
        self.env(**support.fake_claude_env({"jd-review": REVIEWER}))
        return review.run(self.ws, log=lambda *_: None)

    def test_triage_rules(self):
        cfg = self.ws.cfg
        row = {"title": "Machine Learning Engineer Intern", "employment_type": "INTERN", "company": "X"}
        self.assertEqual(triage.drop(row, "", cfg)[0], "employment")
        row = {"title": "ML Engineer", "employment_type": "FULL_TIME", "company": "X"}
        self.assertEqual(triage.drop(row, support.JD_CITIZEN, cfg)[0], "citizenship")
        self.assertIsNone(triage.drop(row, "Must be a U.S. citizen or permanent resident authorized to work.", cfg))
        self.assertEqual(triage.drop({**row, "salary_max_usd": 70000}, "", cfg)[0], "salary")
        self.assertEqual(triage.drop({**row, "experience_level": "10+"}, "", cfg)[0], "experience")
        self.assertEqual(triage.drop({**row, "company": "Mercor"}, "", cfg)[0], "company")
        self.assertEqual(triage.drop({**row, "title": "Sales Engineer"}, "", cfg)[0], "title")
        self.assertIsNone(triage.drop({**row, "title": "Machine Learning Sales Engineer"}, "", cfg))
        sponsor_cfg = config._merge(cfg, {"candidate": {"work_authorization": "needs_sponsorship"}})
        self.assertEqual(triage.drop(row, "We are unable to sponsor visas for this role.", sponsor_cfg)[0],
                         "sponsorship")
        self.assertIsNone(triage.drop(row, "We are unable to sponsor visas for this role.", cfg))

    def test_review_scores_with_evidence(self):
        res = self._collect_and_review()
        self.assertEqual(res["coarse"]["dropped"], 3)   # intern, citizenship, 10+ years
        db = self.ws.db
        good = db.get("4000000001")
        self.assertEqual(good["status"], "ready")
        self.assertEqual(good["score"], 9.0)
        self.assertEqual(good["track"], "mle")
        ds = db.get("4000000004")
        self.assertEqual(ds["status"], "ready")      # 5.5 = the ds threshold
        self.assertEqual(ds["score"], 5.5)
        fabricated = db.get("4000000009")
        self.assertEqual(fabricated["stage"], "new")  # rejected: profile quote not in the rubric
        self.assertIn("profile quote not found", fabricated["review_error"])
        self.assertEqual(fabricated["review_attempts"], 1)
        repost = db.get("4000000005")                  # same JD as 4000000001 at another company: its own review
        self.assertEqual(repost["status"], "ready")
        full = db.review("4000000001")
        self.assertIn("raw", full)

    def test_rubric_change_marks_scores_stale(self):
        self._collect_and_review()
        self.ws.rubric_md.write_text(support.RUBRIC + "\n- Extra fact for the test.\n")
        stale = [r["id"] for r in review.backlog(self.ws) if r.get("stage") == "reviewed"]
        self.assertIn("4000000001", stale)

    def test_scoring_validation_rules(self):
        cfg = self.ws.cfg
        text, _ = scoring.rubric(self.ws)
        base = {"listing_id": "1", "include": True, "track": "mle", "reason": "fits well", "evidence": [],
                "eligibility_flags": [], "assessment": {"category": "model_training", "gaps": [], "components": {
                    "duties_4": {"score": 4, "reason": "core duty", "jd_evidence": ["train, evaluate and deploy"]},
                    "background_3": {"score": 3, "reason": "same", "jd_evidence": ["ranking models"],
                                     "profile_evidence": ["transformer ranking models in PyTorch"]},
                    "skills_2": {"score": 2, "reason": "stack", "jd_evidence": ["Python and PyTorch"],
                                 "profile_evidence": ["Python, PyTorch, scikit-learn"]},
                    "seniority_1": {"score": 1, "reason": "years", "jd_evidence": ["2+ years of experience"],
                                    "profile_evidence": ["About 2 years of industry experience."]}},
                    "fit_cap": {"score": 6.5, "reason": "needs years", "jd_evidence": ["2+ years of experience"]}}}
        out = scoring.validate(base, support.JD_MLE, text, cfg)
        self.assertEqual(out["score"], 6.5)
        bad = json.loads(json.dumps(base))
        bad["assessment"]["category"] = "experimentation"
        with self.assertRaises(scoring.Invalid):
            scoring.validate(bad, support.JD_MLE, text, cfg)
        bad = json.loads(json.dumps(base))
        bad["assessment"]["components"]["duties_4"]["score"] = 4.5
        with self.assertRaises(scoring.Invalid):
            scoring.validate(bad, support.JD_MLE, text, cfg)
        flagged = dict(base, eligibility_flags=["citizenship_required"])
        status, _ = scoring.status_for(scoring.validate(flagged, support.JD_MLE, text, cfg), cfg)
        self.assertEqual(status, "excluded")
        inj = dict(base, injection_suspected=True)
        self.assertEqual(scoring.status_for(scoring.validate(inj, support.JD_MLE, text, cfg), cfg)[0], "hold")

    def test_evidence_ignores_invisible_characters(self):
        jd = "Required: PyTorch,\u200b Hugging Face,\u200b Weights &\u200b Biases. Data\u00a0pipelines"
        hay = util.evidence_norm(jd)
        self.assertTrue(util.quote_in("PyTorch, Hugging Face, Weights & Biases", hay))
        self.assertTrue(util.quote_in("Data pipelines", hay))
        self.assertFalse(util.quote_in("PyTorch, TensorFlow", hay))

    def test_system_prompt_and_schema(self):
        prompt = review.system_prompt(self.ws)
        self.assertIn("# Role: jd-review", prompt)
        self.assertIn("`mle` (priority 1)", prompt)
        self.assertIn("permanent resident", prompt)
        self.assertIn("Never credit", prompt)
        s = review.output_schema(self.ws.cfg, 3)
        self.assertEqual(s["properties"]["reviews"]["maxItems"], 3)
        self.assertIn("none", s["properties"]["reviews"]["items"]["properties"]["track"]["enum"])

    # ------------------------------------------------------------ pool, ledger, report
    def test_queue_order_and_marks(self):
        self._collect_and_review()
        q = pool.queue(self.ws)
        self.assertEqual([r["track"] for r in q][:2], ["mle", "mle"])
        self.assertEqual(q[-1]["track"], "ds")
        companies = [r["company"] for r in q if r["track"] == "mle"]
        self.assertEqual(len(companies), len(set(companies)))
        pool.mark(self.ws, "4000000001", "skipped", note="not interested")
        self.assertNotIn("4000000001", [r["id"] for r in pool.queue(self.ws)])
        self.assertEqual(util.jsonl_read(self.ws.pool_events_path)[-1]["to"], "skipped")

    def test_ledger_stages(self):
        a = ledger.create(self.ws, "Acme Robotics", "ML Engineer", posting_id="4000000001", track="mle")
        self.assertEqual(ledger.create(self.ws, "Acme Robotics", "ML Engineer", posting_id="4000000001"), a)
        ledger.record(self.ws, a, "applied", date="2026-10-01")
        st = ledger.state(self.ws, a)
        self.assertEqual(st["stage"], "applied")
        self.assertEqual([f["app_id"] for f in ledger.due(self.ws)["followups"]], [a])
        ledger.record(self.ws, a, "oa_invited", deadline="2026-10-21")
        ledger.record(self.ws, a, "interview_scheduled", time="2026-10-24T15:00:00-04:00")
        ledger.record(self.ws, a, "rejected")
        st = ledger.state(self.ws, a)
        self.assertEqual(st["stage"], "rejected")
        self.assertTrue(st["closed"])
        f = ledger.funnel(self.ws)
        self.assertEqual((f["applied"], f["oa"], f["interview"], f["rejected"]), (1, 1, 1, 1))
        with self.assertRaises(util.JobPilotError):
            ledger.record(self.ws, a, "void")
        self.assertEqual(util.jsonl_verify(self.ws.apps_path), [])

    def test_brief(self):
        self._collect_and_review()
        text, data = brief.build(self.ws)
        self.assertIn("Machine Learning Engineer", text)
        self.assertTrue((self.ws.reports / ("%s.md" % self.ws.today())).exists())
        self.assertEqual(brief.latest(self.ws)["date"], self.ws.today())

    # ------------------------------------------------------------ plan check
    def test_querymatch_semantics(self):
        f = {"titleSearch": ["Research Scientist", "Quant:*"]}
        self.assertTrue(querymatch.group_matches(f, "Senior Research Scientist"))
        self.assertFalse(querymatch.group_matches(f, "Researcher, Scientist"))
        self.assertTrue(querymatch.group_matches(f, "Quantitative Analyst"))
        self.assertFalse(querymatch.group_matches({"titleSearch": ["Research"]}, "Researcher"))
        self.assertTrue(querymatch.group_matches({"titleSearch": ["ML"], "descriptionSearch": ["PyTorch"]},
                                                 "ML Engineer", description="We use PyTorch daily."))

    def test_plan_check(self):
        util.atomic_write_json(self.ws.examples_path, {
            "positive": [{"title": "Machine Learning Engineer", "track": "mle"},
                         {"title": "Senior Applied Scientist", "track": "mle"},
                         {"title": "Machine Learning Engineers", "track": "mle"},
                         {"title": "Data Scientist", "track": "ds", "description": "machine learning models"}],
            "negative": [{"title": "Machine Learning Engineer Intern"}, {"title": "Sales Director"}]})
        r = plancheck.check(self.ws)
        self.assertTrue(r["ok"], r["errors"])
        self.assertEqual(r["tracks"]["mle"]["found"], 2)   # no stemming: "Engineer" never matches "Engineers"
        self.assertEqual(r["tracks"]["mle"]["missed"], ["Machine Learning Engineers"])
        self.assertEqual(r["negatives"]["matched"], [])
        self.assertTrue(any(t["term"] == "Machine Learning Engineer" and t["title"] == "Machine Learning Engineers"
                            for t in r["traps"]))
        self.assertIn("recall", plancheck.render(r))
        bad = support.CONFIG.replace('titleSearch = ["Data Scientist"]', 'titleSearch = ["Data Scientist"]\n'
                                                                        'seniorityFilter = ["Junior"]')
        (self.ws.root / "jobpilot.toml").write_text(bad)
        self.ws.reload()
        self.assertFalse(plancheck.check(self.ws)["ok"])

    # ------------------------------------------------------------ schedule + CLI
    def test_schedule_files(self):
        text = schedule.plist(self.ws, "06:45")
        self.assertIn("<integer>6</integer>", text)
        self.assertIn("daily --quiet", text)
        self.assertIn(str(self.ws.root), text)
        self.assertIn("# jobpilot:", schedule.cron_line(self.ws, "06:45"))
        with self.assertRaises(util.JobPilotError):
            schedule.plist(self.ws, "25:00")

    def test_cli_smoke(self):
        root = self.ws.root
        os.environ["JOBPILOT_WORKSPACE"] = str(root)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.assertEqual(cli.main(["config", "check", "--json"]), 0)
            self.assertEqual(cli.main(["collect", "--dry-run", "--json"]), 0)
            cli.main(["queue", "--json"])
            self.assertEqual(cli.main(["app", "add", "--company", "Acme", "--role", "MLE", "--applied", "--json"]), 0)
            self.assertEqual(cli.main(["app", "event", "A001", "interview_invited", "--json"]), 0)
            self.assertEqual(cli.main(["app", "list", "--json"]), 0)
        self.assertIn("A001", buf.getvalue())
        self.assertTrue((root / ".jobpilot" / "runtime" / "jobpilot" / "cli.py").exists())

    def test_init_is_idempotent(self):
        ws, created = workspace.init(self.ws.root)
        self.assertEqual(created, [])
        self.assertIn("Jordan Rivera", (ws.root / "jobpilot.toml").read_text())


def support_cfg():
    return config._merge(config.DEFAULTS, __import__("tomllib").loads(support.CONFIG))


if __name__ == "__main__":
    unittest.main()
