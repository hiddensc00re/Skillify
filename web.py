"""Flask/WSGI entrypoint used by Vercel; no server or database writes at import."""
import os
import secrets
import sqlite3
import threading
import hashlib
import io
from pathlib import Path
from urllib.parse import urlsplit

import psycopg
from flask import Flask, g, jsonify, request, send_from_directory, send_file
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from werkzeug.exceptions import HTTPException
from werkzeug.middleware.proxy_fix import ProxyFix

from store import AppError, Store

ROOT = Path(__file__).resolve().parent


def create_app(config=None, store=None):
    app = Flask(__name__, static_folder=None)
    app.json.ensure_ascii = False
    app.config.update(
        CLOUD=os.environ.get("VERCEL") == "1",
        DATABASE_URL=os.environ.get("DATABASE_URL", ""),
        ADMIN_PASSWORD=os.environ.get("ADMIN_PASSWORD", ""),
        APP_URL=os.environ.get("APP_URL", ""),
        SQLITE_PATH=ROOT / "data" / "skillify.db",
        PAUSE_SECONDS=int(os.environ.get("PAUSE_SECONDS", "900")),
        SEED_DEMO_JOBS=os.environ.get("SEED_DEMO_JOBS", "1") == "1",
        MAX_CONTENT_LENGTH=2 * 1024 * 1024 + 65536,
    )
    app.config.update(config or {})
    if app.config["CLOUD"]:
        app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1)
        if app.config["APP_URL"]:
            hosts = [urlsplit(app.config["APP_URL"]).hostname]
            hosts += [os.environ.get(key) for key in ("VERCEL_URL", "VERCEL_PROJECT_PRODUCTION_URL")]
            app.config["TRUSTED_HOSTS"] = [host for host in hosts if host]
    else:
        app.config["TRUSTED_HOSTS"] = ["localhost", "127.0.0.1"]
    cached_store = store
    store_lock = threading.Lock()

    def get_store():
        nonlocal cached_store
        if cached_store is not None:
            return cached_store
        with store_lock:
            if cached_store is not None:
                return cached_store
            database_url = app.config["DATABASE_URL"]
            if app.config["CLOUD"]:
                if not database_url.startswith(("postgresql://", "postgres://")):
                    raise AppError("Configura DATABASE_URL con un database PostgreSQL persistente nelle variabili Vercel.", 503)
                if len(app.config["ADMIN_PASSWORD"]) < 32:
                    raise AppError("Configura ADMIN_PASSWORD con almeno 32 caratteri nelle variabili Vercel.", 503)
            if app.config["PAUSE_SECONDS"] < 1:
                raise AppError("PAUSE_SECONDS deve essere positivo.", 503)
            cached_store = Store(app.config["SQLITE_PATH"], database_url=database_url or None,
                                 pause_seconds=app.config["PAUSE_SECONDS"], seed=app.config["SEED_DEMO_JOBS"])
            return cached_store

    def is_admin():
        password = app.config["ADMIN_PASSWORD"]
        if not password:
            return False
        try:
            signed = URLSafeTimedSerializer(password, salt="skillify-employer-v1").loads(
                request.cookies.get("skillify_admin", ""), max_age=3600)
            return secrets.compare_digest(signed["session"], g.session["token"])
        except (BadSignature, SignatureExpired, KeyError, TypeError):
            return False

    def require_admin():
        if not is_admin() and not (g.account and g.account['role'] == 'employer'):
            raise AppError("Accedi all’area aziende per continuare.", 401)

    def candidate():
        if not g.account or g.account['role'] != 'candidate':
            raise AppError('Accedi come candidato per continuare.', 401)
        return g.account['candidate_id']

    def employer():
        require_admin()
        return {'owner_id':g.account['id'] if g.account and g.account['role']=='employer' else None,
                'legacy':is_admin() and not g.account}

    def body():
        if not request.is_json:
            raise AppError("Invia JSON.", 415)
        data = request.get_json()
        if not isinstance(data, dict):
            raise AppError("La richiesta deve essere un oggetto JSON.")
        return data

    @app.before_request
    def prepare_request():
        if not request.path.startswith("/api/") or request.url_rule is None:
            return
        g.store = get_store()
        if request.path == "/api/health":
            return
        g.session, g.new_session = g.store.session(request.cookies.get("skillify_session"))
        g.account = g.store.account(g.session.get('account_id'))
        if request.method == "POST":
            if not request.path.endswith('/files') and (request.content_length or 0) > 100000:
                raise AppError('Richiesta troppo grande.',413)
            origin = request.headers.get("Origin")
            if (origin is not None and origin != request.host_url.rstrip("/")) or not secrets.compare_digest(
                request.headers.get("X-CSRF-Token", ""), g.session["csrf"]):
                raise AppError("Sessione non valida. Ricarica la pagina.", 403)

    @app.after_request
    def response_headers(response):
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        if getattr(g, "new_session", False):
            response.set_cookie("skillify_session", g.session["token"], max_age=30 * 86400,
                                httponly=True, secure=app.config["CLOUD"], samesite="Strict")
        return response

    @app.errorhandler(AppError)
    def application_error(error):
        return jsonify(error=str(error)), error.status

    @app.errorhandler(HTTPException)
    def http_error(error):
        messages = {400: "Richiesta non valida.", 404: "Risorsa non trovata.", 405: "Metodo non consentito.", 413: "Richiesta troppo grande."}
        return jsonify(error=messages.get(error.code, "Richiesta non riuscita.")), error.code

    @app.errorhandler(psycopg.Error)
    @app.errorhandler(sqlite3.Error)
    def database_error(error):
        app.logger.error("Database unavailable (%s)", type(error).__name__)
        return jsonify(error="Database momentaneamente non disponibile. Riprova tra poco."), 503

    @app.get("/")
    def home():
        return send_from_directory(ROOT / "public", "index.html")

    @app.get("/static/<path:name>")
    def static_asset(name):
        if name not in ("style.css", "app.js"):
            raise AppError("Risorsa non trovata.", 404)
        return send_from_directory(ROOT / "public" / "static", name)

    @app.get("/favicon.svg")
    def favicon():
        return send_from_directory(ROOT / "public", "favicon.svg")

    @app.get("/api/health")
    def health():
        with g.store.connect() as db:
            db.execute("SELECT 1").fetchone()
        return jsonify(status="ok", storage="postgresql" if g.store.database_url else "sqlite")

    @app.get("/api/bootstrap")
    def bootstrap():
        return jsonify(profile=g.store.profile(g.session["candidate_id"]), csrf=g.session["csrf"],
                       pause_seconds=g.store.pause_seconds, is_cloud=app.config["CLOUD"],
                       admin_required=bool(app.config["ADMIN_PASSWORD"]), admin_authenticated=is_admin(), account=g.account,
                       version='0.3.0')

    @app.get("/api/jobs")
    def jobs():
        return jsonify(jobs=g.store.feed(g.session["candidate_id"]))

    @app.get("/api/applications")
    def applications():
        return jsonify(applications=g.store.candidate_reservations(candidate()))

    @app.get("/api/dashboard")
    def dashboard():
        return jsonify(jobs=g.store.employer_jobs(**employer()))

    @app.post("/api/profile")
    def profile():
        return jsonify(profile=g.store.save_profile(candidate(), body()))

    @app.post("/api/passes/reset")
    def reset_passes():
        body()
        g.store.reset_passes(g.session["candidate_id"])
        return jsonify(message="Offerte saltate ripristinate.")

    @app.post("/api/jobs")
    def create_job():
        auth=employer()
        return jsonify(id=g.store.create_job(body(),owner_id=auth['owner_id']), message="Offerta pubblicata.")

    @app.post("/api/jobs/<int:job_id>/<action>")
    def job_action(job_id, action):
        if job_id > 9223372036854775807:
            raise AppError("Offerta non trovata.", 404)
        data = body()
        if action == "apply":
            return jsonify(g.store.reserve(candidate(), job_id))
        if action == "pass":
            g.store.pass_job(g.session["candidate_id"], job_id)
            return jsonify(message="Offerta saltata.")
        if action == "state":
            return jsonify(g.store.employer_state(job_id,data.get('action'),note=field_note(data),**employer()))
        raise AppError("Risorsa non trovata.", 404)

    def field_note(data):
        from store import field
        return field(data,'note',2000,False)

    def auth_response(account=None):
        g.session=g.store.switch_account(g.session['token'],account)
        g.new_session=True
        response=jsonify(account=account,csrf=g.session['csrf'],message='Accesso aggiornato.')
        response.delete_cookie('skillify_admin',secure=app.config['CLOUD'],httponly=True,samesite='Strict')
        return response

    @app.post('/api/auth/register')
    def register():
        return auth_response(g.store.register(body()))

    @app.post('/api/auth/login')
    def login():
        data=body()
        key=hashlib.sha256((str(data.get('email','')).lower()+':'+str(data.get('role',''))).encode()).hexdigest()
        return auth_response(g.store.authenticate(data,key))

    @app.post('/api/auth/logout')
    def logout():
        body()
        return auth_response()

    @app.post('/api/reservations/<int:rid>/<action>')
    def reservation_action(rid,action):
        return jsonify(g.store.reservation_action(candidate(),rid,action,body()))

    @app.post('/api/jobs/<int:job_id>/files')
    def job_files(job_id):
        auth=employer()
        upload=request.files.get('file')
        if not upload:raise AppError('Seleziona un file.')
        return jsonify(g.store.add_attachment(job_id,None,upload.filename,upload.read(2*1024*1024+1),**auth))

    @app.post('/api/reservations/<int:rid>/files')
    def candidate_files(rid):
        cid=candidate()
        rows=g.store.candidate_reservations(cid)
        r=next((x for x in rows if x['id']==rid),None)
        if not r:raise AppError('Candidatura non trovata.',404)
        upload=request.files.get('file')
        if not upload:raise AppError('Seleziona un file.')
        return jsonify(g.store.add_attachment(r['job_id'],rid,upload.filename,upload.read(2*1024*1024+1),candidate_id=cid))

    @app.get('/api/files/<int:fid>')
    def download_file(fid):
        auth={'owner_id':g.account['id'] if g.account and g.account['role']=='employer' else None,'legacy':is_admin() and not g.account}
        f=g.store.attachment(fid,candidate_id=g.account['candidate_id'] if g.account and g.account['role']=='candidate' else None,**auth)
        return send_file(io.BytesIO(bytes(f['content'])),mimetype='application/octet-stream',as_attachment=True,download_name=f['name'])

    @app.post("/api/admin/login")
    def admin_login():
        data = body()
        if g.account:
            raise AppError('Esci dal tuo account prima di accedere alla gestione demo.',409)
        password = app.config["ADMIN_PASSWORD"]
        given = data.get("password", "")
        if not password or not isinstance(given, str) or not secrets.compare_digest(given.encode(), password.encode()):
            raise AppError("Password non corretta.", 401)
        token = URLSafeTimedSerializer(password, salt="skillify-employer-v1").dumps({"session": g.session["token"]})
        response = jsonify(message="Accesso effettuato.")
        response.set_cookie("skillify_admin", token, max_age=3600, httponly=True,
                            secure=app.config["CLOUD"], samesite="Strict")
        return response

    @app.post("/api/admin/logout")
    def admin_logout():
        body()
        response = jsonify(message="Disconnessione effettuata.")
        response.delete_cookie("skillify_admin", secure=app.config["CLOUD"], httponly=True, samesite="Strict")
        return response

    return app


app = create_app()
