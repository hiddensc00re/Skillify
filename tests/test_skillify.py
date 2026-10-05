"""Regression tests using isolated temporary databases and a real HTTP server."""
import concurrent.futures
import http.cookiejar
import json
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import make_handler
from store import AppError, Store


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.now = 10000.0
        self.store = Store(Path(self.temp.name) / "test.db", clock=lambda: self.now)
        self.a = self.candidate("Giulia Rossi", "giulia@example.test")
        self.b = self.candidate("Marco Bianchi", "marco@example.test")
        self.job = self.store.jobs(self.a)[0]["id"]

    def candidate(self, name, email):
        session, _ = self.store.session()
        cid = session["candidate_id"]
        self.store.save_profile(cid, {"name": name, "email": email})
        return cid

    def test_seed_is_idempotent_and_data_persists(self):
        self.assertEqual(8, len(self.store.jobs(self.a)))
        another = Store(self.store.path, clock=lambda: self.now)
        self.assertEqual(8, len(another.jobs(self.a)))
        self.assertEqual("Giulia Rossi", another.profile(self.a)["name"])

    def test_apply_hides_for_every_candidate_and_returns_after_timeout(self):
        self.store.apply(self.a, self.job)
        self.assertNotIn(self.job, [j["id"] for j in self.store.jobs(self.b)])
        self.assertEqual("paused", self.store.dashboard()[-1]["status"])
        self.now += 900
        self.assertIn(self.job, [j["id"] for j in self.store.jobs(self.b)])
        self.assertNotIn(self.job, [j["id"] for j in self.store.jobs(self.a)])
        self.store.apply(self.b, self.job)
        self.assertEqual(2, len(self.store.dashboard()[-1]["candidates"]))

    def test_simultaneous_candidates_only_one_wins(self):
        barrier = threading.Barrier(2)
        def apply(cid):
            barrier.wait()
            try:
                self.store.apply(cid, self.job)
                return "ok"
            except AppError as error:
                return error.status
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            results = list(pool.map(apply, [self.a, self.b]))
        self.assertCountEqual(["ok", 409], results)
        self.assertEqual(1, len(self.store.dashboard()[-1]["candidates"]))

    def test_duplicate_application_does_not_duplicate_rows(self):
        self.store.apply(self.a, self.job)
        self.now += 901
        with self.assertRaises(AppError) as error:
            self.store.apply(self.a, self.job)
        self.assertEqual(409, error.exception.status)
        self.assertEqual(1, len(self.store.applications(self.a)))

    def test_pass_only_affects_current_candidate_and_can_be_reset(self):
        self.store.pass_job(self.a, self.job)
        self.assertNotIn(self.job, [j["id"] for j in self.store.jobs(self.a)])
        self.assertIn(self.job, [j["id"] for j in self.store.jobs(self.b)])
        self.store.reset_passes(self.a)
        self.assertIn(self.job, [j["id"] for j in self.store.jobs(self.a)])

    def test_profile_required_for_application(self):
        session, _ = self.store.session()
        with self.assertRaises(AppError):
            self.store.apply(session["candidate_id"], self.job)
        self.assertEqual("active", self.store.dashboard()[-1]["status"])

    def test_close_reopen_and_missing_job(self):
        self.store.set_state(self.job, "close")
        self.now += 10000
        self.assertNotIn(self.job, [j["id"] for j in self.store.jobs(self.b)])
        with self.assertRaises(AppError):
            self.store.apply(self.b, self.job)
        self.store.set_state(self.job, "reopen")
        self.assertIn(self.job, [j["id"] for j in self.store.jobs(self.b)])
        with self.assertRaises(AppError) as error:
            self.store.set_state(9999, "close")
        self.assertEqual(404, error.exception.status)

    def test_job_creation_and_validation(self):
        data = dict(title="Backend", company="Test", location="Roma", mode="Remoto", category="Engineering", salary="€ 35.000 / anno", description="Sviluppo API", contract="Tempo indeterminato", tags="Python, SQL")
        jid = self.store.create_job(data)
        job = next(j for j in self.store.jobs(self.a) if j["id"] == jid)
        self.assertEqual(["Python", "SQL"], job["tags"])
        data["mode"] = "invalid"
        with self.assertRaises(AppError):
            self.store.create_job(data)
        with self.assertRaises(AppError):
            self.store.save_profile(self.a, {"name": "Giulia", "email": "non-valida"})

    def test_session_cookie_restores_identity_and_expires(self):
        session, created = self.store.session()
        self.assertTrue(created)
        restored, created = self.store.session(session["token"])
        self.assertFalse(created)
        self.assertEqual(session, restored)
        self.now += 30 * 86400 + 1
        expired, created = self.store.session(session["token"])
        self.assertTrue(created)
        self.assertNotEqual(session["candidate_id"], expired["candidate_id"])


class HTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.store = Store(Path(cls.temp.name) / "http.db")
        class QuietHandler(make_handler(cls.store)):
            def log_message(self, *_):
                pass
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), QuietHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()
        cls.temp.cleanup()

    def setUp(self):
        self.jar = http.cookiejar.CookieJar()
        self.client = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))
        _, bootstrap, _ = self.request("/api/bootstrap")
        self.csrf = bootstrap["csrf"]

    def request(self, path, data=None, headers=None, raw=None):
        request_headers = {}
        body = None
        if data is not None or raw is not None:
            body = raw if raw is not None else json.dumps(data).encode()
            request_headers.update({"Content-Type": "application/json", "X-CSRF-Token": getattr(self, "csrf", ""), "Origin": self.url})
        request_headers.update(headers or {})
        req = urllib.request.Request(self.url + path, body, request_headers)
        try:
            response = self.client.open(req)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            content = response.read()
            result = json.loads(content) if "application/json" in response.headers.get("Content-Type", "") else content
            return response.status, result, response.headers

    def test_full_http_candidate_flow_and_cookie_persistence(self):
        status, result, _ = self.request("/api/profile", {"name": "HTTP User", "email": "http@example.test", "skills": "Python"})
        self.assertEqual(200, status)
        _, boot, _ = self.request("/api/bootstrap")
        self.assertEqual(result["profile"]["id"], boot["profile"]["id"])
        _, jobs, _ = self.request("/api/jobs")
        jid = jobs["jobs"][0]["id"]
        self.assertEqual(200, self.request(f"/api/jobs/{jid}/apply", {})[0])
        self.assertEqual(409, self.request(f"/api/jobs/{jid}/apply", {})[0])
        _, apps, _ = self.request("/api/applications")
        self.assertEqual(jid, apps["applications"][0]["id"])
        self.request(f"/api/jobs/{jid}/state", {"action": "reopen"})

    def test_missing_csrf_or_foreign_origin_is_blocked(self):
        data = {"name": "Blocked", "email": "blocked@example.test"}
        self.assertEqual(403, self.request("/api/profile", data, {"X-CSRF-Token": ""})[0])
        self.assertEqual(403, self.request("/api/profile", data, {"Origin": "https://example.test"})[0])

    def test_malformed_json_and_validation(self):
        self.assertEqual(400, self.request("/api/profile", raw=b"{bad")[0])
        self.assertEqual(400, self.request("/api/profile", data=[])[0])
        self.assertEqual(400, self.request("/api/profile", data={"name": "x", "email": "broken"})[0])
        self.assertEqual(415, self.request("/api/profile", data={}, headers={"Content-Type": "text/plain"})[0])

    def test_static_files_and_database_not_exposed(self):
        status, body, headers = self.request("/")
        self.assertEqual(200, status)
        self.assertIn(b"Skillify", body)
        self.assertIn("script-src 'self'", headers["Content-Security-Policy"])
        self.assertEqual(200, self.request("/static/app.js")[0])
        self.assertEqual(200, self.request("/static/style.css")[0])
        for path in ("/data/skillify.db", "/../store.py", "/api/missing"):
            self.assertEqual(404, self.request(path)[0])

    def test_foreign_host_is_blocked(self):
        self.assertEqual(403, self.request("/api/dashboard", headers={"Host": "example.test"})[0])


class CLITests(unittest.TestCase):
    def test_real_server_start_expiration_and_restart_persistence(self):
        with tempfile.TemporaryDirectory() as directory:
            database = str(Path(directory) / "cli.db")
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                port = sock.getsockname()[1]
            url = f"http://127.0.0.1:{port}"
            command = [sys.executable, str(Path(__file__).resolve().parents[1] / "app.py"),
                       "--port", str(port), "--db", database, "--pause-seconds", "1"]
            jar = http.cookiejar.CookieJar()
            client = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))

            def request(path, data=None, csrf=None):
                headers = {"Content-Type": "application/json", "X-CSRF-Token": csrf or "", "Origin": url}
                req = urllib.request.Request(url + path, json.dumps(data).encode() if data is not None else None, headers)
                with client.open(req, timeout=2) as response:
                    return json.loads(response.read())

            def start():
                process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                deadline = time.monotonic() + 5
                while True:
                    try:
                        request("/api/health")
                        return process
                    except urllib.error.URLError:
                        if process.poll() is not None or time.monotonic() >= deadline:
                            process.terminate()
                            process.wait(timeout=3)
                            self.fail("Il server CLI non si è avviato.")
                        time.sleep(0.05)

            process = start()
            try:
                boot = request("/api/bootstrap")
                request("/api/profile", {"name": "CLI User", "email": "cli@example.test"}, boot["csrf"])
                job_id = request("/api/jobs")["jobs"][0]["id"]
                result = request(f"/api/jobs/{job_id}/apply", {}, boot["csrf"])
                self.assertGreater(result["paused_until"], time.time())
                self.assertEqual("paused", next(j for j in request("/api/dashboard")["jobs"] if j["id"] == job_id)["status"])
                deadline = time.monotonic() + 3
                while next(j for j in request("/api/dashboard")["jobs"] if j["id"] == job_id)["status"] != "active":
                    self.assertLess(time.monotonic(), deadline)
                    time.sleep(0.05)
            finally:
                process.terminate()
                process.wait(timeout=3)
            process = start()
            try:
                self.assertEqual("CLI User", request("/api/bootstrap")["profile"]["name"])
                self.assertEqual(job_id, request("/api/applications")["applications"][0]["id"])
                self.assertEqual(8, len(request("/api/dashboard")["jobs"]))
            finally:
                process.terminate()
                process.wait(timeout=3)


if __name__ == "__main__":
    unittest.main()
