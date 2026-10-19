"""The demo workspace: complete, deterministic, fictional, and built the way the real pipeline builds its data
(every stored review passes scoring.validate; every ledger keeps its hash chain)."""
import os
import tempfile
import unittest
from pathlib import Path

import support  # noqa: F401 - sets sys.path and JOBPILOT_OFFLINE

from jobpilot import config, demo, ledger, pool, scoring, search, util


class DemoTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="jp-demo-"))
        cls.ws = demo.create(cls.tmp / "demo")

    def test_counts(self):
        s = demo.summary(self.ws)
        self.assertGreaterEqual(s["postings"], 70)
        self.assertGreaterEqual(s["ready"], 15)
        self.assertGreaterEqual(s["apps"], 12)
        self.assertEqual(s["reports"], 7)
        statuses = self.ws.db.counts("status")
        for st in ("ready", "applied", "hold", "skipped", "below_threshold", "excluded", "dropped", "pending"):
            self.assertGreater(statuses.get(st, 0), 0, st)

    def test_config_is_valid(self):
        errors = [m for level, m in config.check(self.ws.cfg, self.ws) if level == "error"]
        self.assertEqual(errors, [])
        self.assertTrue((self.ws.data / "DEMO").exists())

    def test_every_review_passes_the_real_validator(self):
        text, sha = scoring.rubric(self.ws)
        rows = self.ws.db.find("stage='reviewed'")
        self.assertGreater(len(rows), 50)
        for row in rows:
            stored = self.ws.db.review(row["id"])
            result = scoring.validate(stored["raw"], search.read_jd(self.ws, row["id"]), text, self.ws.cfg)
            self.assertEqual(result.get("score"), row.get("score"), row["id"])
            self.assertEqual(row["review"]["rubric_sha"], sha)

    def test_queue_covers_every_track(self):
        q = pool.queue(self.ws)
        self.assertTrue(q)
        self.assertEqual({r["track"] for r in q}, {"mle", "ds", "as"})
        scores = [r["score"] for r in q]
        self.assertLess(scores.count(10.0), len(scores) / 3, "scores should spread, not cluster at 10")

    def test_pipeline_is_coherent(self):
        f = ledger.funnel(self.ws)
        self.assertGreaterEqual(f["applied"], f["oa"])
        self.assertGreaterEqual(f["oa"], 1)
        self.assertGreaterEqual(f["interview"], f["offer"])
        self.assertEqual(f["offer"], 1)
        due = ledger.due(self.ws)
        self.assertTrue(due["deadlines"] and due["interviews"] and due["followups"])
        for st in ledger.states(self.ws).values():
            if st.get("posting_id") and st["applied"]:
                self.assertEqual(self.ws.db.get(st["posting_id"])["status"], "applied")

    def test_ledgers_keep_their_hash_chains(self):
        for path in (self.ws.apps_path, self.ws.budget_path, self.ws.events_path, self.ws.network / "events.jsonl"):
            self.assertEqual(util.jsonl_verify(path), [], path)

    def test_no_timestamp_in_the_future(self):
        now = util.now()
        for e in util.jsonl_read(self.ws.events_path):
            self.assertLessEqual(util.parse_time(e["ts"]), now, e)
        for row in self.ws.db.find("1=1"):
            self.assertLessEqual(util.parse_time(row["first_seen"]), now, row["id"])

    def test_referrals_and_tailored_folders(self):
        people = util.read_json(self.ws.network / "people.json")
        self.assertGreaterEqual(len(people), 10)
        self.assertTrue(all(not p["linkedin_url"] for p in people.values()), "demo people never link to real profiles")
        cards = list((self.ws.network / "cards").glob("*.json"))
        self.assertGreaterEqual(len(cards), 5)
        for c in cards:
            card = util.read_json(c)
            self.assertLessEqual(len(card["note"]), 200)
            self.assertLessEqual(len(card["message"]), 700)
        reports = list(self.ws.tailored.glob("*/report.json"))
        self.assertEqual(len(reports), 2)
        try:
            from jobpilot import network
        except ImportError:
            return
        self.assertTrue(network.companies(self.ws))
        self.assertIsNotNone(network.person(self.ws, "P0001")["card"])

    def test_links_are_fictional(self):
        for row in self.ws.db.find("1=1"):
            self.assertTrue(row["url"].startswith("https://jobs.example.com/"), row["url"])

    def test_deterministic(self):
        again = demo.create(self.tmp / "again")
        a = sorted((r["id"], r.get("score"), r["status"]) for r in self.ws.db.find("1=1"))
        b = sorted((r["id"], r.get("score"), r["status"]) for r in again.db.find("1=1"))
        self.assertEqual(a, b)

    def test_refuses_a_non_empty_folder(self):
        other = self.tmp / "mine"
        other.mkdir()
        (other / "notes.txt").write_text("keep me")
        with self.assertRaises(util.JobPilotError):
            demo.create(other)
        with self.assertRaises(util.JobPilotError):
            demo.create(other, force=True)   # force only replaces an earlier demo
        self.assertTrue((other / "notes.txt").exists())
        rebuilt = demo.create(self.tmp / "again", force=True)
        self.assertTrue((rebuilt.data / "DEMO").exists())

    def test_chinese_demo(self):
        ws = demo.create(self.tmp / "zh", lang="zh")
        self.assertEqual(config.language(ws.cfg), "zh")
        text = (ws.reports / ("%s.md" % ws.today())).read_text()
        self.assertIn("每日报告", text)


if __name__ == "__main__":
    os.environ.setdefault("JOBPILOT_OFFLINE", "1")
    unittest.main()
