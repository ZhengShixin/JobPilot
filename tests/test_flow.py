"""The application flow: paths through the stages (rounds, ends, reopened and voided applications), the invariants that
make the sankey add up, and the SVG (well-formed, both themes and languages, stage names and counts only)."""
import unittest
import xml.etree.ElementTree as ET

import support
from jobpilot import ledger
from jobpilot.dashboard import flow


class FlowTest(support.EnvCase):
    def setUp(self):
        super().setUp()
        self.env(JOBPILOT_NOW="2026-10-19T15:00:00Z")
        self.ws = support.make_ws()

    def app(self, company, *events):
        a = ledger.create(self.ws, company, "Machine Learning Engineer")
        for ev in events:
            name, fields = (ev, {}) if isinstance(ev, str) else ev
            ledger.record(self.ws, a, name, **fields)
        return a

    def test_paths_rounds_and_ends(self):
        self.app("Waiting Co", "applied")                                          # no answer yet
        self.app("Asked Co", "applied", "receipt", "recruiter_reply")              # a personal reply, no stage
        self.app("Planned Co")                                                     # never sent: not in the flow
        self.app("Voided Co", "applied", "void")                                   # recorded by mistake
        self.app("Reminder Co", "applied", "oa_invited", "oa_invited")             # a reminder is not a 2nd round
        self.app("Rounds Co", "applied", "oa_invited", "oa_done", "oa_invited", "oa_done", "interview_invited",
                 ("interview_scheduled", {"time": "2026-10-20T10:00"}),
                 ("interview_scheduled", {"time": "2026-10-21T10:00", "rescheduled": True}),   # same round
                 "interview_done", ("interview_scheduled", {"time": "2026-10-27T10:00"}),      # the next round
                 "interview_done", "offer")
        self.app("Turned Down Co", "applied", "interview_invited", "interview_done", "rejected")
        self.app("Silent Co", "applied", "closed")
        self.app("Back Co", "applied", "closed", "reopened", "oa_invited")         # reopened, in progress
        f = flow.compute(self.ws)
        self.assertEqual(flow.check(f), [])
        counts = {v["id"]: v["count"] for v in f["nodes"]}
        self.assertEqual(counts, {"applied": 7, "none": 1, "replied": 1, "oa1": 3, "oa2": 1, "iv1": 2, "iv2": 1,
                                  "offer": 1, "reject": 1, "withdrawn": 1})
        opens = {v["id"]: v["open"] for v in f["nodes"] if v["open"] is not None}
        self.assertEqual(opens, {"oa1": 2, "oa2": 0, "iv1": 0, "iv2": 0})          # Reminder Co and Back Co
        t = f["totals"]
        self.assertEqual((t["applied"], t["responded"], t["oa"], t["interview"], t["offer"], t["in_progress"]),
                         (7, 5, 3, 2, 1, 2))
        self.assertTrue(t["summary"].startswith("Applied 7; "))
        self.assertEqual(f["rounds"], {"oa": 2, "iv": 2})
        labels = {v["id"]: v["label"] for v in f["nodes"]}
        self.assertEqual((labels["oa2"], labels["iv1"], labels["withdrawn"]),
                         ("Assessment 2", "Interview 1", "Closed · withdrawn"))
        node = next(v for v in f["nodes"] if v["id"] == "offer")
        self.assertEqual([a["company"] for a in node["apps"]], ["Rounds Co"])

    def test_single_rounds_are_not_numbered_and_chinese_labels(self):
        self.app("One Co", "applied", "oa_invited", "interview_invited")
        labels = {v["id"]: v["label"] for v in flow.compute(self.ws, "zh")["nodes"]}
        self.assertEqual(labels, {"applied": "已投递", "oa1": "测评", "iv1": "面试"})

    def test_svg(self):
        self.app("Secret Robotics", "applied", "oa_invited", "rejected")
        self.app("Hidden Labs", "applied")
        f = flow.compute(self.ws)
        for theme in ("light", "dark"):
            for width in (360, 960, 1600):
                svg = flow.render_svg(f, theme=theme, width=width)
                root = ET.fromstring(svg)
                self.assertEqual(root.tag, "{http://www.w3.org/2000/svg}svg")
                self.assertLessEqual(float(root.get("data-width")), max(width, 600) + 1)
                self.assertNotIn("Secret Robotics", svg)                     # stage names and counts only
                self.assertIn(flow.THEMES[theme]["oa"], svg)
                nodes = [g for g in root.iter("{http://www.w3.org/2000/svg}g") if g.get("data-node")]
                self.assertEqual({g.get("data-node") for g in nodes}, {"applied", "oa1", "reject", "none"})
                self.assertTrue(all(g.get("tabindex") == "0" and g.get("aria-label") and g.get("data-tip")
                                    for g in nodes))
        self.assertIn("被拒", flow.render_svg(flow.compute(self.ws, "zh"), lang="zh"))
        empty = support.make_ws()
        svg = flow.render_svg(flow.compute(empty))
        self.assertIn("No applications sent yet", svg)
        ET.fromstring(svg)


if __name__ == "__main__":
    unittest.main()
