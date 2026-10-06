import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from store import Store
from web import create_app


class WebTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name) / "web.db")
        self.password = "only-for-tests-" + "a" * 32
        self.app = create_app({"TESTING": True, "ADMIN_PASSWORD": self.password}, store=self.store)
        self.client = self.app.test_client()
        self.csrf = self.client.get("/api/bootstrap").json["csrf"]

    def post(self, path, data, client=None, csrf=None, origin="http://localhost"):
        return (client or self.client).post(path, json=data, headers={"X-CSRF-Token": csrf or self.csrf, "Origin": origin})

    def login(self):
        return self.post("/api/admin/login", {"password": self.password})

    def register_candidate(self):
        response = self.post('/api/auth/register', dict(name='Web QA',email='web@example.test',password='qa-password-123456',role='candidate'))
        self.assertEqual(200,response.status_code)
        self.csrf=response.json['csrf']

    def test_candidate_flow_and_private_dashboard(self):
        self.assertEqual(401,self.client.get('/api/applications').status_code)
        self.assertEqual(401,self.post('/api/jobs/1/apply',{}).status_code)
        self.register_candidate()
        jid=self.client.get('/api/jobs').json['jobs'][0]['id']
        response=self.post(f'/api/jobs/{jid}/apply',{})
        self.assertEqual(200,response.status_code)
        rid=response.json['id']
        self.assertEqual(409,self.post(f'/api/jobs/{jid}/apply',{}).status_code)
        self.assertEqual('reserved',self.client.get('/api/applications').json['applications'][0]['status'])
        other=self.app.test_client()
        self.assertNotIn(jid,[j['id'] for j in other.get('/api/jobs').json['jobs']])
        self.assertEqual(200,self.post(f'/api/reservations/{rid}/confirm',dict(motivation='Voglio svolgere questa prova per mostrare le mie competenze.',availability='Oggi',commitment=True)).status_code)
        self.assertEqual(200,self.post(f'/api/reservations/{rid}/start',{}).status_code)
        self.assertEqual(200,self.post(f'/api/reservations/{rid}/submit',{'response':'Una soluzione ragionata sufficientemente lunga per la consegna.'}).status_code)
        self.assertEqual(401,self.client.get('/api/dashboard').status_code)

    def test_admin_login_logout_and_cookie_bound_to_session(self):
        self.assertEqual(401, self.post("/api/admin/login", {"password": "wrong"}).status_code)
        response = self.login()
        self.assertEqual(200, response.status_code)
        self.assertIn("HttpOnly", response.headers["Set-Cookie"])
        cookie = self.client.get_cookie("skillify_admin")
        other = self.app.test_client()
        other.get("/api/bootstrap")
        other.set_cookie("skillify_admin", cookie.value)
        self.assertEqual(401, other.get("/api/dashboard").status_code)
        self.assertEqual(200, self.post("/api/admin/logout", {}).status_code)
        self.assertEqual(401, self.client.get("/api/dashboard").status_code)

    def test_job_writes_require_admin(self):
        job = dict(title="Backend", company="Test", location="Roma", mode="Remoto", category="Engineering", salary="€ 35.000", description="API", contract="Tempo indeterminato", tags="Python")
        self.assertEqual(401, self.post("/api/jobs", job).status_code)
        self.assertEqual(401, self.post("/api/jobs/1/state", {"action": "close"}).status_code)
        self.login()
        result = self.post("/api/jobs", job)
        self.assertEqual(200, result.status_code)
        self.assertEqual("Backend", next(j for j in self.client.get("/api/jobs").json["jobs"] if j["id"] == result.json["id"])["title"])

    def test_csrf_origin_and_json_validation(self):
        self.assertEqual(403, self.client.post("/api/profile", json={}).status_code)
        self.assertEqual(403, self.post("/api/profile", {}, origin="https://foreign.test").status_code)
        self.register_candidate()
        self.assertEqual(400, self.post("/api/profile", []).status_code)
        self.assertEqual(413, self.post("/api/profile", {"name": "x" * 110000}).status_code)
        self.assertEqual(404, self.post("/api/jobs/99999999999999999999999/apply", {}).status_code)

    def test_static_assets_and_hidden_source(self):
        for path in ("/", "/static/app.js", "/static/style.css", "/favicon.svg"):
            response = self.client.get(path)
            self.assertEqual(200, response.status_code)
            response.close()
        for path in ("/web.py", "/data/skillify.db", "/.env", "/static/../store.py"):
            self.assertEqual(404, self.client.get(path).status_code)

    def test_cloud_fails_closed_when_configuration_missing(self):
        app = create_app({"TESTING": True, "CLOUD": True, "DATABASE_URL": "", "ADMIN_PASSWORD": self.password})
        client = app.test_client()
        self.assertEqual(503, client.get("/api/bootstrap").status_code)
        response = client.get("/")
        self.assertEqual(200, response.status_code)
        response.close()
        app = create_app({"TESTING": True, "CLOUD": True, "DATABASE_URL": "postgresql://placeholder", "ADMIN_PASSWORD": "short"})
        self.assertEqual(503, app.test_client().get("/api/health").status_code)

    def test_cloud_cookies_secure_and_https_origin(self):
        app = create_app({"TESTING": True, "CLOUD": True, "ADMIN_PASSWORD": self.password}, store=self.store)
        client = app.test_client()
        response = client.get("/api/bootstrap", base_url="https://skillify.example")
        self.assertIn("Secure", response.headers["Set-Cookie"])
        response = client.post("/api/admin/login", json={"password": self.password}, base_url="https://skillify.example", headers={"Origin": "https://skillify.example", "X-CSRF-Token": response.json["csrf"]})
        self.assertEqual(200, response.status_code)
        self.assertIn("Secure", response.headers["Set-Cookie"])


@unittest.skipUnless(os.environ.get("SKILLIFY_TEST_DATABASE_URL"), "Configure a disposable PostgreSQL database to run integration tests.")
class PostgreSQLTests(unittest.TestCase):
    def test_real_postgres_concurrent_candidates_and_persistence(self):
        import concurrent.futures
        import threading
        url = os.environ["SKILLIFY_TEST_DATABASE_URL"]
        store = Store(database_url=url, pause_seconds=900, seed=False)
        cid = []
        for name in ("One", "Two"):
            session, _ = store.session()
            store.save_profile(session["candidate_id"], {"name": name, "email": name.lower() + "@example.test"})
            cid.append(session["candidate_id"])
        jid = store.create_job(dict(title="Integration QA", company="QA", location="Roma", mode="Remoto", category="Engineering", salary="Test", description="Disposable integration job", contract="Test", tags="Python, SQL"))
        barrier = threading.Barrier(2)
        def apply(candidate):
            from store import AppError
            barrier.wait()
            try:
                store.apply(candidate, jid)
                return 200
            except AppError as error:
                return error.status
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            self.assertCountEqual([200, 409], list(pool.map(apply, cid)))
        restored = Store(database_url=url, seed=False)
        self.assertEqual(1, len(next(j for j in restored.dashboard() if j["id"] == jid)["candidates"]))
        restored.set_state(jid, "close")
        self.assertNotIn(jid, [j["id"] for j in restored.jobs(cid[0])])
        restored.set_state(jid, "reopen")
        for candidate in cid:
            restored.pass_job(candidate, jid)
            restored.reset_passes(candidate)
        with restored.connect() as db:
            db.execute("DELETE FROM applications WHERE job_id=?", (jid,))
            db.execute("DELETE FROM passes WHERE job_id=?", (jid,))
            db.execute("DELETE FROM jobs WHERE id=?", (jid,))
            for candidate in cid:
                db.execute("DELETE FROM sessions WHERE candidate_id=?", (candidate,))
                db.execute("DELETE FROM candidates WHERE id=?", (candidate,))


if __name__ == "__main__":
    unittest.main()
