"""Shared persistence: SQLite locally and PostgreSQL on Vercel."""
import json
import re
import secrets
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path


class AppError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


SEED_JOBS = [
    ("Frontend Developer", "Studio Forma", "Milano", "Ibrido", "Engineering", "€ 32.000 – 42.000 / anno", ["JavaScript", "React", "CSS"], "Costruisci interfacce accessibili per prodotti digitali usati ogni giorno. Lavorerai con un piccolo team di designer e sviluppatori, dall’idea al rilascio.", "Tempo indeterminato", "SF", "green"),
    ("Product Designer", "Lumen", "Remoto · Italia", "Remoto", "Design", "€ 35.000 – 45.000 / anno", ["Figma", "UX research", "Design system"], "Trasforma problemi complessi in esperienze semplici. Cerchiamo una persona curiosa che sappia ascoltare gli utenti e prototipare nuove soluzioni.", "Tempo indeterminato", "LU", "violet"),
    ("Data Analyst", "Orbito", "Bologna", "Ibrido", "Data", "€ 30.000 – 40.000 / anno", ["SQL", "Python", "Dashboard"], "Aiuta il team a prendere decisioni con dati chiari. Analizzerai metriche di prodotto e creerai dashboard utili per tutta l’azienda.", "Tempo indeterminato", "OR", "blue"),
    ("Social Media Specialist", "Fresco", "Roma", "In sede", "Marketing", "€ 26.000 – 32.000 / anno", ["Copywriting", "Social", "Analytics"], "Dai voce a un brand indipendente. Ideerai contenuti, gestirai il calendario editoriale e misurerai i risultati delle campagne.", "Tempo determinato", "FR", "orange"),
    ("Python Developer", "Nodo", "Remoto · Italia", "Remoto", "Engineering", "€ 38.000 – 50.000 / anno", ["Python", "API", "SQL"], "Progetta servizi affidabili per una piattaforma in crescita. Il ruolo include sviluppo backend, revisione del codice e miglioramento delle prestazioni.", "Tempo indeterminato", "NO", "green"),
    ("Customer Success Specialist", "Marea", "Torino", "Ibrido", "Business", "€ 27.000 – 34.000 / anno", ["Comunicazione", "CRM", "Onboarding"], "Accompagna i clienti dalla prima prova ai risultati. Sarai il punto di riferimento per onboarding, formazione e feedback sul prodotto.", "Tempo indeterminato", "MA", "blue"),
    ("Junior UX Designer", "Tandem", "Firenze", "In sede", "Design", "€ 24.000 – 29.000 / anno", ["Prototyping", "Figma", "Accessibilità"], "Cresci in un team che dedica tempo alla ricerca e al confronto. Contribuirai a flussi, prototipi e test con utenti reali.", "Apprendistato", "TA", "violet"),
    ("Digital Marketing Manager", "Semina", "Remoto · Italia", "Remoto", "Marketing", "€ 36.000 – 46.000 / anno", ["SEO", "Advertising", "Strategia"], "Guida campagne digitali con obiettivi misurabili. Collaborerai con creativi e analisti per far crescere un servizio dedicato alla sostenibilità.", "Tempo indeterminato", "SE", "orange"),
]


def field(data, key, maximum, required=True):
    value = data.get(key, "")
    if not isinstance(value, str):
        raise AppError(f"Il campo {key} deve essere testo.")
    value = value.strip()
    if required and not value:
        raise AppError(f"Compila il campo {key}.")
    if len(value) > maximum:
        raise AppError(f"Il campo {key} è troppo lungo (massimo {maximum} caratteri).")
    return value


class Store:
    def __init__(self, path=None, pause_seconds=900, clock=time.time, seed=True, database_url=None):
        self.path = str(path)
        self.database_url = database_url
        self.pause_seconds = pause_seconds
        self.clock = clock
        if not database_url:
            if path is None:
                raise ValueError("Specifica un database SQLite locale o DATABASE_URL.")
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            if database_url:
                # Coordinate schema creation and initial seed across cold starts.
                db.execute("SELECT pg_advisory_xact_lock(78202026)")
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS candidates (
                    id INTEGER PRIMARY KEY, name TEXT NOT NULL DEFAULT '',
                    email TEXT NOT NULL DEFAULT '', skills TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    token TEXT PRIMARY KEY, candidate_id INTEGER NOT NULL REFERENCES candidates(id),
                    csrf TEXT NOT NULL, expires_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS jobs (
                    id INTEGER PRIMARY KEY, title TEXT NOT NULL, company TEXT NOT NULL,
                    location TEXT NOT NULL, mode TEXT NOT NULL, category TEXT NOT NULL,
                    salary TEXT NOT NULL, tags TEXT NOT NULL, description TEXT NOT NULL,
                    contract TEXT NOT NULL, initials TEXT NOT NULL, color TEXT NOT NULL,
                    paused_until REAL NOT NULL DEFAULT 0, closed INTEGER NOT NULL DEFAULT 0,
                    created_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS applications (
                    id INTEGER PRIMARY KEY, job_id INTEGER NOT NULL REFERENCES jobs(id),
                    candidate_id INTEGER NOT NULL REFERENCES candidates(id), created_at REAL NOT NULL,
                    UNIQUE(job_id, candidate_id)
                );
                CREATE TABLE IF NOT EXISTS passes (
                    job_id INTEGER NOT NULL REFERENCES jobs(id),
                    candidate_id INTEGER NOT NULL REFERENCES candidates(id),
                    PRIMARY KEY(job_id, candidate_id)
                );
                CREATE INDEX IF NOT EXISTS applications_job ON applications(job_id);
            """)
            if seed and not db.execute("SELECT 1 FROM jobs LIMIT 1").fetchone():
                db.executemany("""INSERT INTO jobs
                    (title,company,location,mode,category,salary,tags,description,contract,initials,color,created_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                    [(*j[:6], json.dumps(j[6], ensure_ascii=False), *j[7:], self.clock()) for j in SEED_JOBS])

    @contextmanager
    def connect(self):
        if self.database_url:
            import psycopg
            from psycopg.rows import dict_row
            from postgres import Connection
            with psycopg.connect(self.database_url, row_factory=dict_row, connect_timeout=8) as connection:
                connection.execute("SET LOCAL statement_timeout = '10s'")
                connection.execute("SET LOCAL lock_timeout = '10s'")
                yield Connection(connection)
            return
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def session(self, token=None):
        now = self.clock()
        with self.connect() as db:
            row = db.execute("SELECT * FROM sessions WHERE token=? AND expires_at>?", (token, now)).fetchone()
            if row:
                return dict(row), False
            db.execute("DELETE FROM sessions WHERE expires_at<=?", (now,))
            candidate_id = db.execute("INSERT INTO candidates DEFAULT VALUES").lastrowid
            session = dict(token=secrets.token_urlsafe(32), candidate_id=candidate_id,
                           csrf=secrets.token_urlsafe(32), expires_at=now + 30 * 86400)
            db.execute("INSERT INTO sessions VALUES (:token,:candidate_id,:csrf,:expires_at)", session)
            return session, True

    def profile(self, candidate_id):
        with self.connect() as db:
            return dict(db.execute("SELECT * FROM candidates WHERE id=?", (candidate_id,)).fetchone())

    def save_profile(self, candidate_id, data):
        name, email = field(data, "name", 100), field(data, "email", 254)
        skills = field(data, "skills", 300, False)
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
            raise AppError("Inserisci un indirizzo email valido.")
        with self.connect() as db:
            db.execute("UPDATE candidates SET name=?,email=?,skills=? WHERE id=?", (name, email, skills, candidate_id))
        return self.profile(candidate_id)

    def job_json(self, row):
        job = dict(row)
        job["tags"] = json.loads(job["tags"])
        job["status"] = "closed" if job["closed"] else "paused" if job["paused_until"] > self.clock() else "active"
        return job

    def jobs(self, candidate_id):
        with self.connect() as db:
            rows = db.execute("""SELECT * FROM jobs j WHERE closed=0 AND paused_until<=?
                AND NOT EXISTS (SELECT 1 FROM applications a WHERE a.job_id=j.id AND a.candidate_id=?)
                AND NOT EXISTS (SELECT 1 FROM passes p WHERE p.job_id=j.id AND p.candidate_id=?)
                ORDER BY id""", (self.clock(), candidate_id, candidate_id)).fetchall()
            return [self.job_json(row) for row in rows]

    def apply(self, candidate_id, job_id):
        now = self.clock()
        with self.connect() as db:
            # SQLite takes a write lock; PostgreSQL serializes on the selected job row.
            if not self.database_url:
                db.execute("BEGIN IMMEDIATE")
            profile = db.execute("SELECT * FROM candidates WHERE id=?", (candidate_id,)).fetchone()
            if not profile["name"] or not profile["email"]:
                raise AppError("Completa il profilo prima di candidarti.")
            job = db.execute("SELECT * FROM jobs WHERE id=?" + (" FOR UPDATE" if self.database_url else ""), (job_id,)).fetchone()
            now = self.clock()
            if not job:
                raise AppError("Offerta non trovata.", 404)
            if db.execute("SELECT 1 FROM applications WHERE candidate_id=? AND job_id=?", (candidate_id, job_id)).fetchone():
                raise AppError("Ti sei già candidato a questa offerta.", 409)
            if job["closed"] or job["paused_until"] > now:
                raise AppError("Un altro candidato ha scelto questa offerta: non è più disponibile. Aggiorno l’elenco.", 409)
            db.execute("INSERT INTO applications (job_id,candidate_id,created_at) VALUES (?,?,?)", (job_id, candidate_id, now))
            db.execute("UPDATE jobs SET paused_until=? WHERE id=?", (now + self.pause_seconds, job_id))
            db.execute("DELETE FROM passes WHERE job_id=? AND candidate_id=?", (job_id, candidate_id))
            return {"message": "Candidatura registrata!", "paused_until": now + self.pause_seconds}

    def pass_job(self, candidate_id, job_id):
        with self.connect() as db:
            if not db.execute("SELECT 1 FROM jobs WHERE id=?", (job_id,)).fetchone():
                raise AppError("Offerta non trovata.", 404)
            db.execute("INSERT OR IGNORE INTO passes VALUES (?,?)", (job_id, candidate_id))

    def reset_passes(self, candidate_id):
        with self.connect() as db:
            db.execute("DELETE FROM passes WHERE candidate_id=?", (candidate_id,))

    def applications(self, candidate_id):
        with self.connect() as db:
            rows = db.execute("""SELECT j.*, a.created_at AS applied_at FROM applications a
                JOIN jobs j ON j.id=a.job_id WHERE a.candidate_id=? ORDER BY a.created_at DESC,a.id DESC""", (candidate_id,)).fetchall()
            return [self.job_json(row) for row in rows]

    def dashboard(self):
        with self.connect() as db:
            jobs = [self.job_json(row) for row in db.execute("SELECT * FROM jobs ORDER BY id DESC")]
            for job in jobs:
                job["candidates"] = [dict(row) for row in db.execute("""SELECT c.name,c.email,c.skills,a.created_at
                    FROM applications a JOIN candidates c ON c.id=a.candidate_id
                    WHERE a.job_id=? ORDER BY a.created_at DESC""", (job["id"],))]
            return jobs

    def create_job(self, data):
        values = {k: field(data, k, limit) for k, limit in {
            "title": 100, "company": 100, "location": 100, "mode": 20, "category": 30,
            "salary": 100, "description": 3000, "contract": 100}.items()}
        if values["mode"] not in ("Remoto", "Ibrido", "In sede"):
            raise AppError("Modalità di lavoro non valida.")
        if values["category"] not in ("Engineering", "Design", "Data", "Marketing", "Business"):
            raise AppError("Categoria non valida.")
        tags = field(data, "tags", 300, False)
        values.update(tags=json.dumps([t.strip() for t in tags.split(",") if t.strip()][:8], ensure_ascii=False),
                      initials=values["company"][:2].upper(), color="green", created_at=self.clock())
        with self.connect() as db:
            return db.execute("""INSERT INTO jobs
                (title,company,location,mode,category,salary,description,contract,tags,initials,color,created_at)
                VALUES (:title,:company,:location,:mode,:category,:salary,:description,:contract,:tags,:initials,:color,:created_at)""", values).lastrowid

    def set_state(self, job_id, action):
        if action not in ("reopen", "close"):
            raise AppError("Azione non valida.")
        with self.connect() as db:
            row = db.execute("UPDATE jobs SET closed=?,paused_until=0 WHERE id=?", (int(action == "close"), job_id))
            if not row.rowcount:
                raise AppError("Offerta non trovata.", 404)
