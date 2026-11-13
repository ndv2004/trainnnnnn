from __future__ import annotations

import hashlib
import os
import time
from pathlib import Path

import jwt

from .domain import uid, utc_add, utcnow
from .images import Problem


class EvidenceVault:
    def __init__(self, root: Path, store, security):
        self.root, self.store, self.security = root, store, security
        root.mkdir(parents=True, exist_ok=True)

    def write(self, event_id, raw, captured_at, days):
        evidence_id = uid()
        row = {
            "id": evidence_id,
            "event_id": event_id,
            "object_key": None,
            "sha256": None,
            "captured_at": captured_at,
            "expires_at": utc_add(captured_at, days * 86400),
            "status": "MISSING",
            "reason": "EVIDENCE_WRITE_FAILED",
        }
        if raw is None:
            row["reason"] = "NO_CURRENT_FRAME"
            return row
        key = uid() + ".enc"
        path = self.root / key
        tmp = path.with_suffix(".tmp")
        try:
            encrypted = self.security.cipher.encrypt(raw)
            with open(tmp, "xb") as f:
                f.write(encrypted)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
            row.update(object_key=key, sha256=hashlib.sha256(raw).hexdigest(), status="READY", reason=None)
        except OSError:
            tmp.unlink(missing_ok=True)
        return row

    def persist(self, row, db):
        db.execute(
            "INSERT INTO evidence VALUES(?,?,?,?,?,?,?,?)",
            tuple(
                row[k]
                for k in (
                    "id",
                    "event_id",
                    "object_key",
                    "sha256",
                    "captured_at",
                    "expires_at",
                    "status",
                    "reason",
                )
            ),
        )

    def signed_access(self, eid, actor_id):
        expiry = int(time.time()) + 60
        token = jwt.encode(
            {"eid": eid, "sub": actor_id, "purpose": "evidence", "exp": expiry},
            self.security.key,
            algorithm="HS256",
        )
        return {"url": f"/api/v1/proctoring/evidence/{eid}/content?signature={token}", "expires_at": expiry}

    def read(self, row):
        if row["status"] != "READY" or row["expires_at"] <= utcnow():
            raise Problem(410, "EVIDENCE_UNAVAILABLE", "Minh chứng đã hết hạn hoặc không có")
        try:
            plain = self.security.cipher.decrypt((self.root / row["object_key"]).read_bytes())
            if hashlib.sha256(plain).hexdigest() != row["sha256"]:
                raise ValueError("Hash mismatch")
            return plain
        except Exception:
            raise Problem(503, "EVIDENCE_READ_FAILED", "Không đọc được minh chứng") from None

    def purge(self, now=None):
        now = now or utcnow()
        rows = self.store.rows("SELECT * FROM evidence WHERE expires_at<=? AND status!='DELETED'", (now,))
        deleted = 0
        for row in rows:
            try:
                if row["object_key"]:
                    (self.root / row["object_key"]).unlink(missing_ok=True)
                with self.store.transaction() as db:
                    db.execute(
                        "UPDATE evidence SET status='DELETED',object_key=NULL,reason='EXPIRED' WHERE id=?",
                        (row["id"],),
                    )
                    event = self.store.event(row["event_id"])
                    event["evidence_status"] = "DELETED"
                    event["version"] += 1
                    self.store.save_event(event, db)
                    self.store.outbox(event["session_id"], "proctor.event.updated", event, db)
                deleted += 1
            except OSError:
                continue  # retry next maintenance tick; never mark undeleted bytes as deleted
        self.store.execute("DELETE FROM profiles WHERE expires_at<=?", (now,))
        return deleted
