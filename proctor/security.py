from __future__ import annotations

import json
import os
import secrets
import time
from pathlib import Path

import jwt
from cryptography.fernet import Fernet

from .domain import uid
from .images import Problem


class Security:
    def __init__(self, data_dir: Path, store):
        self.store = store
        path = data_dir / "secrets.json"
        if not path.exists():
            # Exclusive creation prevents replacing keys on a concurrent invocation.
            try:
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "w") as f:
                    json.dump({"jwt": secrets.token_urlsafe(48), "fernet": Fernet.generate_key().decode()}, f)
            except FileExistsError:
                pass
        keys = json.loads(path.read_text())
        self.key, self.cipher = keys["jwt"], Fernet(keys["fernet"].encode())

    def issue(self, actor, role, sid=None, purpose="user", ttl=900, generation=None):
        payload = {
            "sub": actor,
            "role": role,
            "session_id": sid,
            "purpose": purpose,
            "iat": int(time.time()),
            "exp": int(time.time()) + ttl,
            "jti": uid(),
            "iss": "exam-watch-local",
            "aud": "proctoring",
            "generation": generation,
        }
        return jwt.encode(payload, self.key, algorithm="HS256")

    def decode(self, token, allow_expired=False):
        try:
            p = jwt.decode(
                token,
                self.key,
                algorithms=["HS256"],
                issuer="exam-watch-local",
                audience="proctoring",
                options={
                    "verify_exp": not allow_expired,
                    "require": ["exp", "iat", "jti", "sub", "iss", "aud", "role", "purpose"],
                },
            )
        except jwt.ExpiredSignatureError:
            raise Problem(401, "TOKEN_EXPIRED", "Token hết hạn; xin token mới") from None
        except jwt.InvalidTokenError:
            raise Problem(401, "INVALID_TOKEN", "Token không hợp lệ") from None
        if self.store.row("SELECT jti FROM revoked_tokens WHERE jti=?", (p["jti"],)):
            raise Problem(401, "TOKEN_REVOKED", "Token đã thu hồi")
        return p

    def revoke(self, p, db):
        db.execute("INSERT OR IGNORE INTO revoked_tokens VALUES(?,?)", (p["jti"], p["exp"]))


class StandaloneIdentityAdapter:
    """Server-managed attempts/assignments. This is NOT an exam-system integration."""

    DEMO_ATTEMPTS = {
        "candidate_a": "10000000-0000-4000-8000-000000000001",
        "candidate_b": "10000000-0000-4000-8000-000000000002",
    }
    ACCOUNTS = {
        "candidate_a": ("demo-candidate-a", "candidate"),
        "candidate_b": ("demo-candidate-b", "candidate"),
        "proctor_a": ("demo-proctor-a", "proctor"),
        "proctor_b": ("demo-proctor-b", "proctor"),
        "outsider": ("demo-outsider", "proctor"),
        "admin": ("demo-admin", "admin"),
        "backend": ("demo-backend", "service"),
    }

    def __init__(self, store, demo):
        self.store = store
        if demo:
            with store.transaction() as db:
                for key, attempt in self.DEMO_ATTEMPTS.items():
                    db.execute(
                        "INSERT OR IGNORE INTO attempts VALUES(?,?,?,?)",
                        (
                            attempt,
                            self.ACCOUNTS[key][0],
                            "demo-exam",
                            json.dumps(["demo-proctor-a", "demo-proctor-b"]),
                        ),
                    )

    def attempt(self, attempt_id):
        row = self.store.row("SELECT * FROM attempts WHERE id=?", (attempt_id,))
        if not row:
            raise Problem(404, "NOT_FOUND", "Lượt thi chưa được backend xác nhận")
        return row

    def authorize(self, actor, session, action="read"):
        role = actor["role"]
        if role in ("admin", "service") and actor["purpose"] == "user":
            return
        if actor["purpose"] == "frames":
            if (
                action != "frames"
                or actor["session_id"] != session["session_id"]
                or actor["sub"] != session["candidate_id"]
            ):
                raise Problem(403, "ACCESS_DENIED", "Token thu ảnh không có quyền thao tác này")
            if actor.get("generation") != session["generation"]:
                raise Problem(401, "TOKEN_REVOKED", "Luồng đã được thay thế; xin token mới")
            return
        if (
            role == "candidate"
            and actor["sub"] == session["candidate_id"]
            and action in ("read", "candidate", "frames")
        ):
            return
        attempt = self.attempt(session["attempt_id"])
        if (
            role == "proctor"
            and actor["sub"] in json.loads(attempt["proctors"])
            and action in ("read", "review", "evidence", "manual")
        ):
            return
        raise Problem(404, "NOT_FOUND", "Không tìm thấy đối tượng trong phạm vi được cấp quyền")
