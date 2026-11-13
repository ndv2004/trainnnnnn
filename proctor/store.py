from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path

from .config import Config, canonical_hash
from .domain import uid, utcnow
from .images import Problem


def dumps(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


class SQLiteStore:
    """Standalone adapter. One process, WAL, explicit transactions and optimistic review locks."""

    def __init__(self, path: Path):
        self.lock = threading.RLock()
        self.db = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            PRAGMA journal_mode=WAL;
            PRAGMA synchronous=FULL;
            PRAGMA foreign_keys=ON;
            CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS model_versions(module TEXT, hash TEXT, payload TEXT NOT NULL, PRIMARY KEY(module,hash));
            CREATE TABLE IF NOT EXISTS configs(id TEXT PRIMARY KEY, hash TEXT UNIQUE, payload TEXT NOT NULL, created_at TEXT, created_by TEXT, reason TEXT);
            CREATE TABLE IF NOT EXISTS attempts(id TEXT PRIMARY KEY, candidate_id TEXT, exam_id TEXT, proctors TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY, attempt_id TEXT UNIQUE REFERENCES attempts(id), candidate_id TEXT, state TEXT, payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS profiles(candidate_id TEXT PRIMARY KEY, model_version TEXT, embedding BLOB NOT NULL, source_ref TEXT, expires_at TEXT);
            CREATE TABLE IF NOT EXISTS verifications(id TEXT PRIMARY KEY, session_id TEXT REFERENCES sessions(id), payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS prechecks(id TEXT PRIMARY KEY, session_id TEXT REFERENCES sessions(id), payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS calibrations(id TEXT PRIMARY KEY, session_id TEXT REFERENCES sessions(id), payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS manual_decisions(id TEXT PRIMARY KEY, session_id TEXT REFERENCES sessions(id), actor_id TEXT, payload TEXT NOT NULL, created_at TEXT);
            CREATE TABLE IF NOT EXISTS receipts(session_id TEXT REFERENCES sessions(id), stream_id TEXT, seq INTEGER, hash TEXT, payload TEXT, PRIMARY KEY(session_id,stream_id,seq));
            CREATE TABLE IF NOT EXISTS idempotency(actor TEXT, scope TEXT, key TEXT, hash TEXT, response TEXT, PRIMARY KEY(actor,scope,key));
            CREATE TABLE IF NOT EXISTS events(id TEXT PRIMARY KEY, session_id TEXT REFERENCES sessions(id), type TEXT, start_at TEXT, confirmed_at TEXT, end_at TEXT, review_status TEXT, version INTEGER, payload TEXT NOT NULL);
            CREATE UNIQUE INDEX IF NOT EXISTS one_open_event ON events(session_id,type) WHERE end_at IS NULL;
            CREATE INDEX IF NOT EXISTS events_timeline ON events(session_id,start_at,id);
            CREATE INDEX IF NOT EXISTS events_review ON events(review_status,confirmed_at);
            CREATE TABLE IF NOT EXISTS evidence(id TEXT PRIMARY KEY, event_id TEXT REFERENCES events(id), object_key TEXT, sha256 TEXT, captured_at TEXT, expires_at TEXT, status TEXT, reason TEXT);
            CREATE INDEX IF NOT EXISTS evidence_expiry ON evidence(expires_at,status);
            CREATE TABLE IF NOT EXISTS reviews(id TEXT PRIMARY KEY, event_id TEXT REFERENCES events(id), actor_id TEXT, before_state TEXT, after_state TEXT, reason TEXT, created_at TEXT);
            CREATE TABLE IF NOT EXISTS audit(id TEXT PRIMARY KEY, actor_id TEXT, action TEXT, target_id TEXT, at TEXT, request_id TEXT);
            CREATE TABLE IF NOT EXISTS outbox(id INTEGER PRIMARY KEY AUTOINCREMENT, message_id TEXT UNIQUE, session_id TEXT, event_type TEXT, payload TEXT, sent_at TEXT, attempts INTEGER DEFAULT 0, next_attempt REAL DEFAULT 0, dead INTEGER DEFAULT 0);
            CREATE INDEX IF NOT EXISTS outbox_delivery ON outbox(sent_at,id);
            CREATE TABLE IF NOT EXISTS revoked_tokens(jti TEXT PRIMARY KEY, expires_at INTEGER);
        """)

    @contextmanager
    def transaction(self):
        with self.lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                yield self.db
                self.db.execute("COMMIT")
            except BaseException:
                self.db.execute("ROLLBACK")
                raise

    def row(self, sql, args=()):
        with self.lock:
            r = self.db.execute(sql, args).fetchone()
            return dict(r) if r else None

    def rows(self, sql, args=()):
        with self.lock:
            return [dict(r) for r in self.db.execute(sql, args).fetchall()]

    def execute(self, sql, args=()):
        with self.lock:
            return self.db.execute(sql, args)

    def add_config(self, config: Config, actor="bootstrap", reason="Initial CPU experiment", activate=False):
        with self.transaction() as db:
            existing = db.execute("SELECT hash FROM configs WHERE id=?", (config.version,)).fetchone()
            if existing and existing["hash"] != config.digest:
                raise Problem(409, "CONFIG_IMMUTABLE", "Mã cấu hình đã có nội dung khác")
            db.execute(
                "INSERT OR IGNORE INTO configs VALUES(?,?,?,?,?,?)",
                (config.version, config.digest, config.model_dump_json(), utcnow(), actor, reason),
            )
            if activate:
                db.execute(
                    "INSERT INTO metadata VALUES('active_config',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (config.version,),
                )
                self.audit(actor, "CONFIG_ACTIVATED", config.version, db=db)

    def session(self, sid):
        row = self.row("SELECT payload FROM sessions WHERE id=?", (sid,))
        if not row:
            raise Problem(404, "NOT_FOUND", "Không tìm thấy phiên")
        return json.loads(row["payload"])

    def save_session(self, s, db=None):
        (db or self.db).execute(
            "UPDATE sessions SET state=?,payload=? WHERE id=?", (s["state"], dumps(s), s["session_id"])
        )

    def event(self, eid):
        row = self.row("SELECT payload FROM events WHERE id=?", (eid,))
        if not row:
            raise Problem(404, "NOT_FOUND", "Không tìm thấy sự kiện")
        return json.loads(row["payload"])

    def save_event(self, e, db):
        db.execute(
            "INSERT INTO events VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET end_at=excluded.end_at,review_status=excluded.review_status,version=excluded.version,payload=excluded.payload",
            (
                e["event_id"],
                e["session_id"],
                e["type"],
                e["start_at"],
                e["confirmed_at"],
                e["end_at"],
                e["review_status"],
                e["version"],
                dumps(e),
            ),
        )

    def audit(self, actor, action, target, request_id=None, db=None):
        (db or self.db).execute(
            "INSERT INTO audit VALUES(?,?,?,?,?,?)", (uid(), actor, action, target, utcnow(), request_id)
        )

    def outbox(self, sid, kind, payload, db):
        mid = uid()
        envelope = {
            "message_id": mid,
            "session_id": sid,
            "event_id": payload.get("event_id"),
            "version": payload.get("version", 1),
            "server_time": utcnow(),
            "type": kind,
            "payload": payload,
        }
        db.execute(
            "INSERT INTO outbox(message_id,session_id,event_type,payload) VALUES(?,?,?,?)",
            (mid, sid, kind, dumps(envelope)),
        )

    def idem_get(self, actor, scope, key, payload, db):
        if not key or len(key) > 200:
            raise Problem(400, "IDEMPOTENCY_KEY_REQUIRED", "Cần Idempotency-Key dài tối đa 200 ký tự")
        row = db.execute(
            "SELECT hash,response FROM idempotency WHERE actor=? AND scope=? AND key=?", (actor, scope, key)
        ).fetchone()
        if row:
            if row["hash"] != canonical_hash(payload):
                raise Problem(409, "IDEMPOTENCY_CONFLICT", "Cùng khóa nhưng nội dung khác")
            return json.loads(row["response"])
        return None

    def idem_put(self, actor, scope, key, payload, response, db):
        db.execute(
            "INSERT INTO idempotency VALUES(?,?,?,?,?)",
            (actor, scope, key, canonical_hash(payload), dumps(response)),
        )

    def close(self):
        self.db.close()
