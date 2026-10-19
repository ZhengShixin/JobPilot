"""Tailoring end to end: prepare a posting folder, build from a plan, lint, the headless run with a fake Claude, the
claims audit, the ledger event and the CLI."""
import contextlib
import io
import json
import os
import unittest
from pathlib import Path

import support
from jobpilot import cli, docx, ledger, search, tailor, util
from test_docx import RESUME_MD

GOOD_PLAN = {
    "jd_priorities": ["train and deploy ranking models", "PyTorch training pipelines"],
    "edits": [
        {"op": "replace", "find": "in PyTorch for marketplace search",
         "with": "in PyTorch for marketplace search, from training to offline evaluation", "why": "JD priority 1"},
        {"op": "move", "paragraph": "Inference Latency:", "after": "Ranking Models:"},
    ],
    "bullets_changed": [{"paragraph": "Ranking Models:", "kind": "reworded"},
                        {"paragraph": "Inference Latency:", "kind": "reordered"}],
    "facts_to_confirm": ["Were the ranking models served online or in batch?"],
    "not_claimed": ["Kubernetes"],
}

TAILOR_RESPONDER = """
import json
GOOD = %s
def respond(task, system, schema):
    assert system.startswith("# Role: tailor"), system[:40]
    assert "<master_paragraphs>" in task and '<untrusted source="jd"' in task
    if "could not be applied" in task or "FIRST_OK" in task:
        return GOOD
    bad = json.loads(json.dumps(GOOD))
    bad["edits"][0]["find"] = "this text is not in the resume"
    return bad
"""


def responder(first_ok):
    code = TAILOR_RESPONDER % json.dumps(GOOD_PLAN)
    if first_ok:
        code = code.replace('"FIRST_OK" in task', "True")
    return code


AUDIT_PASS = {"pass": True, "findings": [{"severity": "warn", "rule": "style", "claim": "from training to offline "
                                          "evaluation", "why": "A little long.", "fix": "Shorten it."}]}
AUDIT_BLOCK = {"pass": True, "findings": [{"severity": "blocker", "rule": "new_fact", "claim": "offline evaluation",
                                           "why": "The profile does not mention offline evaluation.",
                                           "fix": "Restore the master wording."}]}


def run_cli(*argv):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = cli.main(list(argv))
    return code, buf.getvalue()


class TailorCase(support.EnvCase):
    def setUp(self):
        super().setUp()
        self.ws = support.make_ws()
        docx.from_markdown(RESUME_MD, self.ws.root / "profile" / "resume.docx")
        self.ws.db.put({"id": "4000000001", "title": "Machine Learning Engineer", "company": "Acme Robotics",
                        "url": "https://www.linkedin.com/jobs/view/4000000001/", "location": "New York, NY",
                        "track": "mle", "stage": "reviewed", "status": "ready"})
        util.atomic_write_text(search.jd_path(self.ws, "4000000001"), support.JD_MLE)

    def write_plan(self, folder, plan):
        util.atomic_write_json(Path(folder) / "plan.json", plan)


class PrepareTest(TailorCase):
    def test_prepare_from_pool(self):
        prep = tailor.prepare(self.ws, posting_id="4000000001")
        folder = Path(prep["path"])
        self.assertTrue(folder.name.endswith("_Acme-Robotics_Machine-Learning-Engineer"))
        jd = (folder / "jd.md").read_text()
        self.assertIn("## Posting text (verbatim)", jd)
        self.assertIn(support.JD_MLE.strip(), jd)
        self.assertEqual(tailor.jd_of(folder), support.JD_MLE.strip())
        self.assertEqual(json.loads((folder / "meta.json").read_text())["posting_id"], "4000000001")
        self.assertEqual(prep["master"], "profile/resume.docx")
        self.assertTrue(any(p["label"] == "Ranking Models:" for p in prep["paragraphs"]))
        again = tailor.prepare(self.ws, posting_id="4000000001")
        self.assertEqual(again["path"], prep["path"])
        listing = tailor.numbered(prep["paragraphs"])
        self.assertIn("[0] (Title) Jordan Rivera", listing)
        self.assertIn("\\tJun 2023", listing)

    def test_prepare_pasted_and_errors(self):
        prep = tailor.prepare(self.ws, jd_text=support.JD_DS * 2, company="Northwind Retail", role="Data Scientist")
        self.assertIsNone(prep["posting_id"])
        self.assertIn("Northwind-Retail_Data-Scientist", prep["folder"])
        with self.assertRaises(util.JobPilotError):
            tailor.prepare(self.ws, jd_text="too short", company="X", role="Y")
        with self.assertRaises(util.JobPilotError):
            tailor.prepare(self.ws, posting_id="999")
        with self.assertRaises(util.JobPilotError):
            tailor.prepare(self.ws, jd_text=support.JD_DS * 2, company="", role="Y")


class BuildTest(TailorCase):
    def test_build_writes_resume_and_report(self):
        app = ledger.create(self.ws, "Acme Robotics", "Machine Learning Engineer", posting_id="4000000001")
        prep = tailor.prepare(self.ws, posting_id="4000000001")
        plan = json.loads(json.dumps(GOOD_PLAN))
        plan["edits"].append({"op": "replace", "find": "Cut inference latency by 38%",
                              "with": "Cut inference latency by 45%"})
        plan["cover_letter"] = "Dear Hiring Team,\n\nI build ranking models in PyTorch.\n\nSincerely,\nJordan Rivera"
        self.write_plan(prep["path"], plan)
        rep = tailor.build(self.ws, prep["folder"])
        self.assertTrue(rep["ok"], rep)
        self.assertEqual(rep["files"]["docx"], prep["folder"] + "/Jordan_Rivera_Resume.docx")
        self.assertTrue((self.ws.root / rep["files"]["cover_letter"]).exists())
        self.assertEqual([c["op"] for c in rep["changed"]], ["replace", "move", "replace"])
        self.assertTrue(any("new number(s) 45" in w for w in rep["lint"]["warnings"]))
        self.assertEqual(rep["facts_to_confirm"], GOOD_PLAN["facts_to_confirm"])
        text = docx.text_of(self.ws.root / rep["files"]["docx"])
        self.assertIn("from training to offline evaluation", text)
        self.assertLess(text.index("Ranking Models"), text.index("Inference Latency"))
        self.assertLess(text.index("Inference Latency"), text.index("Feature Pipeline"))
        stored = json.loads((Path(prep["path"]) / "report.json").read_text())
        self.assertEqual(stored["posting_id"], "4000000001")
        self.assertEqual(rep["app_id"], app)
        timeline = ledger.state(self.ws, app)["timeline"]
        self.assertEqual([t["event"] for t in timeline], ["created", "tailored"])
        tailor.build(self.ws, prep["folder"])  # a rebuild records nothing new
        self.assertEqual(len(ledger.state(self.ws, app)["timeline"]), 2)
        letter = docx.paragraphs(self.ws.root / rep["files"]["cover_letter"])
        self.assertEqual(letter[0]["text"], "Jordan Rivera")
        self.assertIn("jordan@example.com", letter[1]["text"])
        if docx.soffice():
            self.assertTrue((self.ws.root / rep["files"]["pdf"]).exists())
            self.assertEqual(rep["pages"], 1)
            self.assertEqual(rep["master_pages"], 1)
        kinds = [e.get("kind") for e in util.jsonl_read(self.ws.events_path)]
        self.assertIn("tailor", kinds)

    def test_never_claim_is_an_error(self):
        prep = tailor.prepare(self.ws, posting_id="4000000001")
        plan = json.loads(json.dumps(GOOD_PLAN))
        plan["edits"] = [{"op": "replace", "find": "AWS SageMaker, Docker", "with": "AWS SageMaker, Docker, Kubernetes"}]
        self.write_plan(prep["path"], plan)
        rep = tailor.build(self.ws, prep["folder"])
        self.assertFalse(rep["ok"])
        self.assertTrue(any("Kubernetes" in e for e in rep["lint"]["errors"]))
        code, out = run_cli("--workspace", str(self.ws.root), "tailor", "build", prep["folder"])
        self.assertEqual(code, 1)
        self.assertIn("Kubernetes", out)

    def test_bad_edit_and_invalid_plan(self):
        prep = tailor.prepare(self.ws, posting_id="4000000001")
        plan = json.loads(json.dumps(GOOD_PLAN))
        plan["edits"][0]["find"] = "not in the resume at all"
        self.write_plan(prep["path"], plan)
        rep = tailor.build(self.ws, prep["folder"])
        self.assertFalse(rep["ok"])
        self.assertTrue(rep["error"].startswith("edit 1 (replace)"))
        self.write_plan(prep["path"], {"edits": []})
        rep = tailor.build(self.ws, "4000000001")  # a posting id resolves to its folder
        self.assertTrue(rep["error"].startswith("invalid plan"))
        self.write_plan(prep["path"], dict(GOOD_PLAN, edits=[{"op": "move", "paragraph": "Ranking"}]))
        self.assertIn("exactly one of", tailor.build(self.ws, prep["folder"])["error"])
        with self.assertRaises(util.JobPilotError):
            tailor.build(self.ws, "no-such-folder")

    def test_markdown_and_pdf_masters(self):
        md = self.ws.root / "profile" / "resume.md"
        md.write_text(RESUME_MD)
        cfg = (self.ws.root / "jobpilot.toml").read_text().replace('resume = "profile/resume.docx"',
                                                                   'resume = "profile/resume.md"')
        (self.ws.root / "jobpilot.toml").write_text(cfg)
        self.ws.reload()
        master, source = tailor.master_docx(self.ws)
        self.assertEqual(source, md.resolve())
        self.assertEqual(master.suffix, ".docx")
        self.assertEqual(tailor.master_docx(self.ws)[0], master)  # cached
        (self.ws.root / "profile" / "resume.pdf").write_bytes(b"%PDF-1.4\n")
        (self.ws.root / "jobpilot.toml").write_text(cfg.replace("resume.md", "resume.pdf"))
        self.ws.reload()
        with self.assertRaises(util.JobPilotError) as cm:
            tailor.master_docx(self.ws)
        self.assertIn("build-docx", str(cm.exception))
        prep = tailor.prepare(self.ws, posting_id="4000000001")
        self.assertIn("PDF", prep["master_error"])


class LintTest(TailorCase):
    def test_rules(self):
        master = "Skills: Python, Kubernetes basics. Cut latency by 38%."
        r = tailor.lint("Skills: Python, Scala, Kubernetes basics. XXX. [Company] TODO {{name}}", self.ws,
                        master_text=master)
        self.assertTrue(any("Scala" in e for e in r["errors"]))
        self.assertTrue(any("Kubernetes" in w and "already says" in w for w in r["warnings"]))
        self.assertEqual(sum("placeholder" in e for e in r["errors"]), 4)
        changed = [{"op": "replace", "edit": i, "before": "Did a thing %d." % i,
                    "after": "Did a thing %d for 1,200 users and 38%% faster, on a STEM OPT visa." % i}
                   for i in range(8)]
        r = tailor.lint("text", self.ws, master_text=master, changed=changed, pages=2, master_pages=1)
        self.assertTrue(any("1200" in w for w in r["warnings"]))
        self.assertFalse(any("new number(s) 38" in w for w in r["warnings"]))
        self.assertTrue(any(w.startswith("scope: 8") for w in r["warnings"]))
        self.assertTrue(any("2 page(s)" in w for w in r["warnings"]))
        self.assertTrue(any("work authorization" in w for w in r["warnings"]))
        self.assertEqual(tailor.lint("Clean text with Python.", self.ws, master_text=master),
                         {"errors": [], "warnings": []})
        chained = [{"op": "replace", "before": "a", "after": "b"}, {"op": "replace", "before": "b", "after": "c"},
                   {"op": "move", "before": "x", "after": "x"}, {"op": "insert_after", "before": None, "after": "n"}]
        self.assertEqual(tailor._paragraphs_touched(chained), 2)


class AutoTest(TailorCase):
    def test_auto_plans_builds_and_audits(self):
        self.env(**support.fake_claude_env({"tailor": responder(first_ok=True), "claims-audit": AUDIT_PASS}))
        rep = tailor.auto(self.ws, "4000000001", log=lambda *_: None)
        self.assertTrue(rep["ok"], rep)
        self.assertTrue(rep["audit"]["pass"])
        self.assertEqual(rep["generated_by"]["attempts"], 1)
        self.assertEqual(rep["generated_by"]["model"], "opus")
        folder = Path(rep["path"])
        self.assertEqual(json.loads((folder / "plan.json").read_text())["edits"], GOOD_PLAN["edits"])
        roles = [json.loads(line)["role"] for line in
                 (Path(os.environ["FAKE_CLAUDE_DIR"]) / "calls.jsonl").read_text().splitlines()]
        self.assertEqual(roles, ["tailor", "claims-audit"])
        runs = [r["role"] for r in util.jsonl_read(self.ws.claude_runs_path)]
        self.assertEqual(runs, ["tailor", "claims-audit"])
        listed = tailor.folders(self.ws)
        self.assertEqual(listed[0]["status"], "built")

    def test_auto_retries_a_plan_that_does_not_apply(self):
        self.env(**support.fake_claude_env({"tailor": responder(first_ok=False), "claims-audit": AUDIT_PASS}))
        logs = []
        rep = tailor.auto(self.ws, "4000000001", log=logs.append)
        self.assertTrue(rep["ok"], rep)
        self.assertEqual(rep["generated_by"]["attempts"], 2)
        self.assertTrue(logs and "asking once more" in logs[0])

    def test_blocker_fails_the_audit_and_the_cli(self):
        self.env(**support.fake_claude_env({"tailor": responder(first_ok=True), "claims-audit": AUDIT_BLOCK}))
        code, out = run_cli("--workspace", str(self.ws.root), "tailor", "auto", "4000000001")
        self.assertEqual(code, 1)
        self.assertIn("claims audit: BLOCKED", out)
        rep = json.loads((tailor.folder_for(self.ws, "4000000001") / "report.json").read_text())
        self.assertIs(rep["audit"]["pass"], False)

    def test_failed_run_is_reported(self):
        self.env(**support.fake_claude_env({}))
        rep = tailor.auto(self.ws, "4000000001", log=lambda *_: None)
        self.assertFalse(rep["ok"])
        self.assertIn("the tailoring run failed", rep["error"])


class CliTest(TailorCase):
    def test_resume_and_tailor_commands(self):
        ws = str(self.ws.root)
        code, out = run_cli("--workspace", ws, "resume", "paragraphs", "--json")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)[0]["text"], "Jordan Rivera")
        md = self.ws.root / "letter.md"
        md.write_text("# Jordan Rivera\n\nDear team,\n\nHello.\n")
        code, out = run_cli("resume", "build-docx", str(md), str(self.ws.root / "letter.docx"), "--kind", "letter")
        self.assertEqual(code, 0)
        self.assertTrue((self.ws.root / "letter.docx").exists())
        code, out = run_cli("resume", "text", str(self.ws.root / "profile" / "resume.docx"))
        self.assertIn("Brightline Analytics", out)
        code, out = run_cli("--workspace", ws, "tailor", "prepare", "4000000001")
        self.assertEqual(code, 0)
        self.assertIn("Next: write", out)
        jd = self.ws.root / "jd.txt"
        jd.write_text(support.JD_DS * 2)
        code, out = run_cli("--workspace", ws, "tailor", "prepare", "--jd-file", str(jd), "--company", "Northwind",
                            "--role", "Data Scientist", "--json")
        self.assertEqual(json.loads(out)["company"], "Northwind")
        code, out = run_cli("--workspace", ws, "tailor", "list", "--json")
        self.assertEqual(len(json.loads(out)), 2)
        folder = tailor.folder_for(self.ws, "4000000001")
        util.atomic_write_json(folder / "plan.json", GOOD_PLAN)
        self.assertEqual(run_cli("--workspace", ws, "tailor", "build", "4000000001")[0], 0)
        code, out = run_cli("--workspace", ws, "tailor", "show", "4000000001")
        self.assertEqual(code, 0)
        self.assertIn("Acme Robotics — Machine Learning Engineer", out)
        self.assertIn("? Were the ranking models served online or in batch?", out)
        bad = self.ws.root / "bad.md"
        bad.write_text("Led Kubernetes migrations for [Company].")
        code, out = run_cli("--workspace", ws, "tailor", "lint", str(bad))
        self.assertEqual(code, 1)
        self.assertIn("Kubernetes", out)


if __name__ == "__main__":
    unittest.main()
