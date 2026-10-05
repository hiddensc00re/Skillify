#!/usr/bin/env python3
"""Skillify local HTTP server. Python 3.10+, no third-party dependencies."""
import argparse
import json
import mimetypes
import re
import secrets
from http.cookies import SimpleCookie, CookieError
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from store import AppError, Store

ROOT = Path(__file__).resolve().parent


def make_handler(store):
    class Handler(BaseHTTPRequestHandler):
        server_version = "Skillify/1.0"

        def reply(self, status, payload, content_type="application/json; charset=utf-8", new_session=None):
            body = json.dumps(payload, ensure_ascii=False).encode() if content_type.startswith("application/json") else payload
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "same-origin")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
            if new_session:
                self.send_header("Set-Cookie", f"skillify_session={new_session['token']}; Path=/; HttpOnly; SameSite=Strict; Max-Age=2592000")
            self.end_headers()
            self.wfile.write(body)

        def get_session(self):
            cookies = SimpleCookie()
            try:
                cookies.load(self.headers.get("Cookie", ""))
            except CookieError:
                pass
            item = cookies.get("skillify_session")
            return store.session(item.value if item else None)

        def valid_host(self):
            # Keep the prototype local and prevent DNS rebinding to the company panel.
            return self.headers.get("Host", "") in (f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}")

        def do_GET(self):
            if not self.valid_host():
                return self.reply(403, {"error": "Usa localhost o 127.0.0.1 per aprire Skillify."})
            path = urlsplit(self.path).path
            if path.startswith("/api/"):
                session, created = self.get_session()
                candidate_id = session["candidate_id"]
                if path == "/api/bootstrap":
                    result = {"profile": store.profile(candidate_id), "csrf": session["csrf"],
                              "pause_seconds": store.pause_seconds}
                elif path == "/api/jobs":
                    result = {"jobs": store.jobs(candidate_id)}
                elif path == "/api/applications":
                    result = {"applications": store.applications(candidate_id)}
                elif path == "/api/dashboard":
                    result = {"jobs": store.dashboard()}
                elif path == "/api/health":
                    result = {"status": "ok"}
                else:
                    return self.reply(404, {"error": "Risorsa non trovata."})
                return self.reply(200, result, new_session=session if created else None)
            files = {"/": "index.html", "/static/style.css": "style.css", "/static/app.js": "app.js", "/favicon.svg": "favicon.svg"}
            if path not in files:
                return self.reply(404, {"error": "Pagina non trovata."})
            file = ROOT / "static" / files[path]
            mime = mimetypes.guess_type(file.name)[0] or "application/octet-stream"
            self.reply(200, file.read_bytes(), mime + ("; charset=utf-8" if mime.startswith("text/") or "javascript" in mime else ""))

        def do_POST(self):
            if not self.valid_host():
                return self.reply(403, {"error": "Host non autorizzato."})
            session, created = self.get_session()
            origin = self.headers.get("Origin")
            expected_origin = "http://" + self.headers.get("Host", "")
            if (origin is not None and origin != expected_origin) or not secrets.compare_digest(self.headers.get("X-CSRF-Token", ""), session["csrf"]):
                return self.reply(403, {"error": "Sessione non valida. Ricarica la pagina."}, new_session=session if created else None)
            try:
                if self.headers.get_content_type() != "application/json":
                    raise AppError("Invia JSON.", 415)
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                except ValueError:
                    raise AppError("Lunghezza della richiesta non valida.")
                if not 0 < length <= 20000:
                    raise AppError("Richiesta vuota o troppo grande.", 413)
                try:
                    data = json.loads(self.rfile.read(length))
                except (ValueError, UnicodeError):
                    raise AppError("JSON non valido.")
                if not isinstance(data, dict):
                    raise AppError("La richiesta deve essere un oggetto JSON.")
                path, cid = urlsplit(self.path).path, session["candidate_id"]
                match = re.fullmatch(r"/api/jobs/(\d+)/(apply|pass|state)", path)
                if path == "/api/profile":
                    result = {"profile": store.save_profile(cid, data)}
                elif path == "/api/passes/reset":
                    store.reset_passes(cid)
                    result = {"message": "Offerte saltate ripristinate."}
                elif path == "/api/jobs":
                    result = {"id": store.create_job(data), "message": "Offerta pubblicata."}
                elif match:
                    job_id, action = int(match[1]), match[2]
                    if action == "apply":
                        result = store.apply(cid, job_id)
                    elif action == "pass":
                        store.pass_job(cid, job_id)
                        result = {"message": "Offerta saltata."}
                    else:
                        store.set_state(job_id, data.get("action"))
                        result = {"message": "Stato dell’offerta aggiornato."}
                else:
                    raise AppError("Risorsa non trovata.", 404)
                self.reply(200, result)
            except AppError as error:
                self.reply(error.status, {"error": str(error)})

    return Handler


def main():
    parser = argparse.ArgumentParser(description="Skillify — offerte di lavoro con swipe (demo locale)")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--db", type=Path, default=ROOT / "data" / "skillify.db")
    parser.add_argument("--pause-seconds", type=int, default=900, help="Durata della sospensione dopo una candidatura")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535 or args.pause_seconds < 1:
        parser.error("Porta: 1–65535; pausa: almeno 1 secondo.")
    store = Store(args.db, pause_seconds=args.pause_seconds)
    try:
        server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(store))
    except OSError as error:
        parser.exit(1, f"Impossibile avviare Skillify: {error}. Prova --port 8001.\n")
    print(f"Skillify pronto su http://127.0.0.1:{args.port}\nDatabase: {args.db}\nPremi Ctrl+C per fermare.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nSkillify fermato.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
