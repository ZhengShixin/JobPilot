"""The local dashboard: login and session cookie, the auth/Host/Origin/CSRF checks, every read view, the writes,
file downloads, background tasks and the self-contained static export. Runs against a demo workspace on a free port."""
import http.client
import json
import os
import re
import stat
import tempfile
import threading
import time
import unittest
from pathlib import Path

import support  # noqa: F401 - sets sys.path and JOBPILOT_OFFLINE

from jobpilot import demo, ledger, util
from jobpilot.dashboard import server, tasks


class DashboardTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._env = dict(os.environ)
        os.environ["JOBPILOT_SCHEDULE_DRY"] = "1"
        cls.tmp = Path(tempfile.mkdtemp(prefix="jp-dash-"))
        cls.ws = demo.create(cls.tmp / "demo")
        cls.srv, cls.app = server.make_server(cls.ws, port=0)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.port = cls.app.port

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        os.environ.clear()
        os.environ.update(cls._env)

    # ------------------------------------------------------------ helpers
    def req(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=20)
        h = {"Host": "127.0.0.1:%d" % self.port}
        h.update(headers or {})
        data = body if isinstance(body, (bytes, type(None))) else json.dumps(body).encode()
        conn.request(method, path, body=data, headers=h)
        res = conn.getresponse()
        out = res.status, {k.lower(): v for k, v in res.getheaders()}, res.read()
        conn.close()
        return out

    def cookie(self):
        status, headers, _ = self.req("GET", "/?key=" + self.app.key)
        self.assertEqual(status, 302)
        return headers["set-cookie"].split(";")[0]

    def get(self, path):
        status, _, body = self.req("GET", path, headers={"Cookie": self.cookie()})
        self.assertEqual(status, 200, body[:300])
        return json.loads(body)

    def post(self, name, body, csrf=True, origin=True, ctype="application/json"):
        h = {"Cookie": self.cookie(), "Content-Type": ctype}
        if origin:
            h["Origin"] = "http://127.0.0.1:%d" % self.port
        if csrf:
            h["X-JobPilot-CSRF"] = self.get("/api/session")["csrf"]
        status, _, data = self.req("POST", "/api/" + name, body, headers=h)
        return status, json.loads(data or b"{}")

    def ready_ids(self, n=3):
        return [r["id"] for r in self.ws.db.find("status='ready'", order="score DESC", limit=n)]

    # ------------------------------------------------------------ auth
    def test_token_file_is_private(self):
        mode = stat.S_IMODE((self.ws.data / "dashboard.token").stat().st_mode)
        self.assertEqual(mode, 0o600)

    def test_login_sets_strict_httponly_cookie(self):
        status, headers, _ = self.req("GET", "/?key=" + self.app.key)
        self.assertEqual(status, 302)
        self.assertEqual(headers["location"], "/")
        cookie = headers["set-cookie"]
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Strict", cookie)
        self.assertNotIn(self.app.key, cookie)

    def test_wrong_key_gets_no_cookie(self):
        status, headers, _ = self.req("GET", "/?key=not-the-key")
        self.assertEqual(status, 200)
        self.assertNotIn("set-cookie", headers)

    def test_api_needs_the_cookie(self):
        self.assertEqual(self.req("GET", "/api/summary")[0], 401)
        self.assertEqual(self.req("GET", "/api/summary", headers={"Cookie": "jp_x=forged"})[0], 401)

    def test_foreign_host_is_refused(self):
        status, _, _ = self.req("GET", "/api/session", headers={"Cookie": self.cookie(), "Host": "evil.example"})
        self.assertEqual(status, 421)

    def test_login_endpoint(self):
        h = {"Content-Type": "application/json", "Origin": "http://127.0.0.1:%d" % self.port}
        status, headers, _ = self.req("POST", "/api/login", {"key": self.app.key}, headers=h)
        self.assertEqual(status, 200)
        self.assertIn("HttpOnly", headers["set-cookie"])
        self.assertEqual(self.req("POST", "/api/login", {"key": "wrong"}, headers=h)[0], 403)
        h["Origin"] = "http://evil.example"
        self.assertEqual(self.req("POST", "/api/login", {"key": self.app.key}, headers=h)[0], 403)

    def test_page_has_a_nonce_csp_and_no_external_resources(self):
        status, headers, body = self.req("GET", "/")
        self.assertEqual(status, 200)
        csp = headers["content-security-policy"]
        self.assertIn("default-src 'none'", csp)
        nonce = re.search(r"'nonce-([^']+)'", csp).group(1)
        html = body.decode()
        self.assertIn('nonce="%s"' % nonce, html)
        self.assertNotIn("{{NONCE}}", html)
        self.assertIsNone(re.search(r'(src|href)="https?://', html))
        self.assertEqual(headers["x-frame-options"], "DENY")

    # ------------------------------------------------------------ reads
    def test_every_read_view(self):
        params = {"posting": "?id=" + self.ready_ids(1)[0], "app": "?id=A001", "person": "?id=P0001",
                  "report": "?date=" + self.ws.today()}
        cookie = self.cookie()
        for name in server.READS:
            status, headers, body = self.req("GET", "/api/" + name + params.get(name, ""), headers={"Cookie": cookie})
            self.assertEqual(status, 200, "%s: %s" % (name, body[:200]))
            self.assertEqual(headers["cache-control"], "no-store")
            json.loads(body)

    def test_summary_numbers(self):
        s = self.get("/api/summary")
        self.assertGreater(s["kpis"]["ready"], 10)
        self.assertGreater(s["kpis"]["new_today"], 0)
        self.assertEqual(len(s["days"]), 7)
        self.assertEqual(set(s["series"]), {"mle", "ds", "as"})
        self.assertTrue(s["due"]["deadlines"] and s["due"]["interviews"])
        self.assertIsNotNone(s["budget"])

    def test_pool_filters(self):
        ready = self.get("/api/pool?status=ready&limit=500")
        self.assertTrue(all(r["status"] == "ready" for r in ready["rows"]))
        mle = self.get("/api/pool?status=ready&track=mle&limit=500")
        self.assertTrue(mle["rows"] and all(r["track"] == "mle" for r in mle["rows"]))
        high = self.get("/api/pool?status=ready&min=9&limit=500")
        self.assertTrue(all(r["score"] >= 9 for r in high["rows"]))
        text = self.get("/api/pool?status=all&q=northwind&limit=500")
        self.assertTrue(text["rows"] and all("northwind" in r["company"].lower() for r in text["rows"]))
        page = self.get("/api/pool?status=all&limit=5&offset=5")
        self.assertEqual(len(page["rows"]), 5)

    def test_posting_detail_has_evidence_and_jd(self):
        p = self.get("/api/posting?id=" + self.ready_ids(1)[0])
        self.assertIn("jd", p)
        self.assertTrue(p["jd_quotes"]["duties_4"])
        self.assertEqual(self.req("GET", "/api/posting?id=nope", headers={"Cookie": self.cookie()})[0], 404)

    def test_bad_report_date(self):
        self.assertEqual(self.req("GET", "/api/report?date=../x", headers={"Cookie": self.cookie()})[0], 400)

    # ------------------------------------------------------------ writes
    def test_post_checks(self):
        pid = self.ready_ids(1)[0]
        self.assertEqual(self.post("posting/mark", {"id": pid, "status": "hold"}, csrf=False)[0], 403)
        self.assertEqual(self.post("posting/mark", {"id": pid, "status": "hold"}, origin=False)[0], 403)
        self.assertEqual(self.post("posting/mark", b'{"id": "x"}', ctype="text/plain")[0], 415)
        self.assertEqual(self.post("posting/mark", {"id": pid, "status": "applied"})[0], 400)
        self.assertEqual(self.post("no/such", {})[0], 404)

    def test_mark_and_application_flow(self):
        a, b = self.ready_ids(5)[3:5]
        status, res = self.post("posting/mark", {"id": a, "status": "skipped", "note": "not now"})
        self.assertEqual(status, 200)
        self.assertEqual(self.ws.db.get(a)["status"], "skipped")
        self.assertEqual(self.post("posting/mark", {"id": a, "status": "ready"})[0], 200)
        status, res = self.post("app/create", {"posting_id": b, "applied": True, "date": self.ws.today()})
        self.assertEqual(status, 200, res)
        app_id = res["app"]["id"]
        self.assertEqual(self.ws.db.get(b)["status"], "applied")
        self.assertEqual(ledger.state(self.ws, app_id)["stage"], "applied")
        status, res = self.post("app/event", {"app_id": app_id, "event": "oa_invited", "deadline": "2030-01-05"})
        self.assertEqual(status, 200, res)
        self.assertEqual(ledger.state(self.ws, app_id)["stage"], "oa")
        self.assertEqual(self.post("app/event", {"app_id": app_id, "event": "bogus"})[0], 400)
        self.assertEqual(self.post("app/event", {"app_id": app_id, "event": "note", "date": "yesterday"})[0], 400)
        actions = util.jsonl_read(self.ws.data / "dashboard-actions.jsonl")
        self.assertTrue(any(x["action"] == "app/create" for x in actions))

    def test_manual_application(self):
        status, res = self.post("app/create", {"company": "Example Co", "role": "Data Scientist", "applied": False,
                                               "url": "javascript:alert(1)"})
        self.assertEqual(status, 200, res)
        self.assertEqual(res["app"]["stage"], "planned")
        self.assertFalse((res["app"]["url"] or "").startswith("javascript"))

    def test_demo_turns_paid_tasks_off(self):
        for name, body in (("run/daily", {}), ("tailor/start", {"posting_id": self.ready_ids(1)[0]}),
                           ("net/find", {"company": "Atlas Mapping"}), ("net/cards", {"company": "Atlas Mapping"})):
            status, res = self.post(name, body)
            self.assertEqual(status, 409, name)
            self.assertIn("demo", res["error"])

    # ------------------------------------------------------------ files and export
    def test_files_only_from_tailored(self):
        cookie = {"Cookie": self.cookie()}
        for bad in ("../jobpilot.toml", "data/dashboard.token", "/etc/passwd", "tailored/../data/dashboard.token"):
            self.assertIn(self.req("GET", "/files?path=" + bad, headers=cookie)[0], (403, 404), bad)
        rep = next(self.ws.tailored.glob("*/report.json"))
        status, headers, body = self.req("GET", "/files?path=" + self.ws.rel(rep), headers=cookie)
        self.assertEqual(status, 200)
        self.assertIn("attachment", headers["content-disposition"])
        self.assertEqual(self.req("GET", "/files?path=" + self.ws.rel(rep))[0], 401)

    def test_static_export(self):
        out = self.tmp / "export.html"
        server.export_static(self.ws, out)
        html = out.read_text()
        self.assertNotIn("{{NONCE}}", html)
        self.assertNotIn("/*BOOT*/", html)
        self.assertIsNone(re.search(r'(src|href)="https?://', html))
        blob = re.search(r'<script id="jp-static" type="application/json">(.*?)</script>', html, re.S).group(1)
        data = json.loads(blob)
        self.assertTrue(data["pool_all"] and data["posting"] and data["app"] and data["person"])
        self.assertEqual(data["session"]["csrf"], "")
        self.assertNotIn(self.app.key, html)


class TasksTest(unittest.TestCase):
    def setUp(self):
        self.ws = support.make_ws()

    def test_runs_the_workspace_cli_and_records_the_result(self):
        task = tasks.start(self.ws, "daily", ["version"])
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            t = tasks.get(self.ws, task["id"])
            if t["status"] != "running":
                break
            time.sleep(0.2)
        self.assertEqual(t["status"], "done", t)
        self.assertEqual(t["exit"], 0)
        self.assertIn("jobpilot", t["log_tail"])

    def test_single_flight_and_dead_process(self):
        folder = self.ws.data / "tasks"
        folder.mkdir(parents=True, exist_ok=True)
        live = {"id": "T20990101-000000-aaaa", "kind": "tailor", "target": "123", "argv": [], "status": "running",
                "started": util.stamp(), "pid": os.getpid(), "log": str(self.ws.logs / "x.log")}
        util.atomic_write_json(folder / (live["id"] + ".json"), live)
        with self.assertRaises(util.JobPilotError):
            tasks.start(self.ws, "tailor", ["tailor", "auto", "123"], target="123")
        dead = dict(live, id="T20990101-000001-bbbb", target="456", pid=999999,
                    started="2020-01-01T00:00:00+00:00")
        util.atomic_write_json(folder / (dead["id"] + ".json"), dead)
        self.assertEqual(tasks.get(self.ws, dead["id"])["status"], "failed")
        self.assertIsNone(tasks.get(self.ws, "../../etc/passwd"))
        with self.assertRaises(util.JobPilotError):
            tasks.start(self.ws, "rm", ["rm", "-rf", "/"])


if __name__ == "__main__":
    unittest.main()
