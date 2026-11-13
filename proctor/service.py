from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime

import numpy as np

from .config import Config, Settings, canonical_hash
from .domain import Change, uid, utc_add, utcnow
from .event_engine import EventEngine
from .evidence import EvidenceVault
from .images import Problem, decode_jpeg
from .models import ModelSuite
from .process_guard import ProcessGuard
from .security import Security, StandaloneIdentityAdapter
from .store import SQLiteStore, dumps


@dataclass
class Packet:
    raw: bytes
    frame: object
    seq: int
    at: float
    utc: str
    generation: int


@dataclass
class Runtime:
    cfg: Config
    engine: EventEngine
    origin: float = field(default_factory=time.monotonic)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    last_received: float | None = None
    last_processed: float | None = None
    last_object: float = -1e9
    last_verify: float = -1e9
    pending: Packet | None = None
    rates: deque = field(default_factory=deque)
    calibration: list = field(default_factory=list)
    calibration_origin: float | None = None
    calibration_seq: int = -1
    interrupted_id: str | None = None
    technical_ids: dict = field(default_factory=dict)
    last_health: dict = field(default_factory=dict)


class Service:
    def __init__(self, settings: Settings, models=None):
        self.settings = settings
        self.cfg = settings.load_config()
        self.guard = ProcessGuard(settings.data_dir / "worker.lock")
        self.store = SQLiteStore(settings.data_dir / "proctor.sqlite3")
        self.store.add_config(self.cfg)
        active = self.store.row("SELECT value FROM metadata WHERE key='active_config'")
        if active:
            self.cfg = Config.model_validate_json(
                self.store.row("SELECT payload FROM configs WHERE id=?", (active["value"],))["payload"]
            )
        else:
            self.store.add_config(self.cfg, activate=True)
        self.security = Security(settings.data_dir, self.store)
        self.identity = StandaloneIdentityAdapter(self.store, settings.demo)
        self.vault = EvidenceVault(settings.data_dir / "private-evidence", self.store, self.security)
        self.models = models if models is not None else ModelSuite(settings.models_dir)
        with self.store.transaction() as db:
            for module, version in self.models.versions.items():
                db.execute(
                    "INSERT OR IGNORE INTO model_versions VALUES(?,?,?)",
                    (module, version["sha256"], dumps(version)),
                )
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="proctor-cpu")
        self.runtimes: dict[str, Runtime] = {}
        self.ready_queue = deque()
        self.queued = set()
        self.wake = asyncio.Event()
        self.subscribers: dict[str, set] = {}
        self.tasks = []
        self.metrics = {"processed_frames": 0, "dropped_frames": 0, "unknown_signals": 0, "total_signals": 0}
        self._recover()

    def runtime(self, s):
        sid = s["session_id"]
        if sid not in self.runtimes:
            cfg = Config.model_validate(s["config"])
            self.runtimes[sid] = Runtime(cfg, EventEngine(cfg))
        return self.runtimes[sid]

    def _recover(self):
        rows = self.store.rows("SELECT payload FROM sessions WHERE state IN ('MONITORING','DEGRADED')")
        for row in rows:
            s = json.loads(row["payload"])
            with self.store.transaction() as db:
                for e_row in db.execute(
                    "SELECT payload FROM events WHERE session_id=? AND end_at IS NULL", (s["session_id"],)
                ).fetchall():
                    e = json.loads(e_row["payload"])
                    e.update(
                        end_at=e["last_observed_at"],
                        close_reason="WORKER_INTERRUPTED",
                        version=e["version"] + 1,
                    )
                    self.store.save_event(e, db)
                    self.store.outbox(s["session_id"], "proctor.event.updated", e, db)
                s.update(state="DEGRADED", resume_required=True)
                self.store.save_session(s, db)
                self.store.audit("worker", "WORKER_INTERRUPTED", s["session_id"], db=db)
            rt = self.runtime(s)
            rt.interrupted_id = self.technical_open(
                s, "WORKER_INTERRUPTED", s.get("last_observed_at") or utcnow()
            )

    async def start(self):
        self.tasks = [
            asyncio.create_task(self._worker()),
            asyncio.create_task(self._maintenance()),
            asyncio.create_task(self._outbox_loop()),
        ]

    async def stop(self):
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        await asyncio.to_thread(self.executor.shutdown, wait=True, cancel_futures=True)
        self.models.close()
        self.store.close()
        self.guard.close()

    async def infer(self, function, *args):
        return await asyncio.get_running_loop().run_in_executor(self.executor, function, *args)

    def create(self, actor, attempt_id, key):
        if actor["purpose"] != "user" or actor["role"] not in ("candidate", "service", "admin"):
            raise Problem(403, "ACCESS_DENIED", "Không có quyền tạo phiên")
        attempt = self.identity.attempt(attempt_id)
        if actor["role"] == "candidate" and actor["sub"] != attempt["candidate_id"]:
            raise Problem(404, "NOT_FOUND", "Không tìm thấy lượt thi")
        body = {"attempt_id": attempt_id}
        with self.store.transaction() as db:
            old = self.store.idem_get(actor["sub"], "create", key, body, db)
            if old:
                return old, False
            row = db.execute("SELECT payload FROM sessions WHERE attempt_id=?", (attempt_id,)).fetchone()
            if row:
                s = json.loads(row["payload"])
                new = False
            else:
                s = {
                    "session_id": uid(),
                    "attempt_id": attempt_id,
                    "candidate_id": attempt["candidate_id"],
                    "exam_id": attempt["exam_id"],
                    "state": "CREATED",
                    "created_at": utcnow(),
                    "config_version": self.cfg.version,
                    "config_hash": self.cfg.digest,
                    "config": self.cfg.model_dump(),
                    "version": 1,
                    "verified": False,
                    "manual_override": None,
                    "verify_attempts": 0,
                    "consent": False,
                    "baseline": None,
                    "calibration_id": None,
                    "stream_id": None,
                    "generation": 0,
                    "last_seq": -1,
                    "last_offset_ms": -1,
                    "started_at": None,
                    "ended_at": None,
                    "last_observed_at": None,
                    "resume_required": False,
                    "dropped_frames": 0,
                    "health": {},
                    "latest_observation": None,
                }
                db.execute(
                    "INSERT INTO sessions VALUES(?,?,?,?,?)",
                    (s["session_id"], attempt_id, attempt["candidate_id"], s["state"], dumps(s)),
                )
                self.store.audit(actor["sub"], "SESSION_CREATED", s["session_id"], db=db)
                new = True
            result = {
                "session_id": s["session_id"],
                "state": s["state"],
                "config_version": s["config_version"],
            }
            self.store.idem_put(actor["sub"], "create", key, body, result, db)
            return result, new

    def public_session(self, s):
        return {k: v for k, v in s.items() if k not in ("config", "baseline", "candidate_id")}

    def require_state(self, s, states):
        if s["state"] == "ENDED":
            raise Problem(409, "SESSION_ENDED", "Phiên đã kết thúc")
        if s["state"] not in states:
            raise Problem(
                409, "INVALID_STATE", "Thao tác không phù hợp trạng thái hiện tại", {"state": s["state"]}
            )

    def require_consent(self, s):
        if not s["consent"]:
            raise Problem(409, "CONSENT_REQUIRED", "Cần đồng ý thu ảnh thử nghiệm trước khi gửi ảnh")

    async def precheck(self, s, raw):
        rt = self.runtime(s)
        async with rt.lock:
            s = self.store.session(s["session_id"])
            self.require_state(s, ("CREATED", "PRECHECK", "VERIFYING"))
            self.require_consent(s)
            frame = decode_jpeg(raw, rt.cfg)
            result = await self.infer(self.models.inspect, frame, rt.cfg, False)
            s["state"] = "VERIFYING" if result["quality"]["face_ok"] else "PRECHECK"
            s["precheck_ok"] = result["quality"]["face_ok"]
            s["health"] = self.models.health.copy()
            with self.store.transaction() as db:
                self.store.save_session(s, db)
                db.execute(
                    "INSERT INTO prechecks VALUES(?,?,?)",
                    (
                        uid(),
                        s["session_id"],
                        dumps(
                            {
                                "quality": result["quality"],
                                "created_at": utcnow(),
                                "config_version": rt.cfg.version,
                            }
                        ),
                    ),
                )
            return {"state": s["state"], "quality": result["quality"], "modules": self.models.health}

    async def verify(self, s, raw):
        rt = self.runtime(s)
        async with rt.lock:
            s = self.store.session(s["session_id"])
            self.require_state(s, ("VERIFYING",))
            self.require_consent(s)
            if s["verify_attempts"] >= rt.cfg.verify_attempt_limit:
                raise Problem(409, "MANUAL_REVIEW_REQUIRED", "Đã hết lượt xác thực tự động")
            frame = decode_jpeg(raw, rt.cfg)
            inspected = await self.infer(self.models.inspect, frame, rt.cfg, False)
            result = await self.infer(self.compare_profile, s, frame, inspected, rt.cfg)
            s["verify_attempts"] += 1
            if result["result"] == "MATCH":
                s.update(verified=True, state="CALIBRATING", calibration_id=uid())
            if s["verify_attempts"] >= rt.cfg.verify_attempt_limit and not s["verified"]:
                result["manual_review_required"] = True
            with self.store.transaction() as db:
                self.store.save_session(s, db)
                db.execute("INSERT INTO verifications VALUES(?,?,?)", (uid(), s["session_id"], dumps(result)))
                self.store.audit(s["candidate_id"], "FACE_VERIFIED", s["session_id"], db=db)
            return {
                **result,
                "state": s["state"],
                "attempts_used": s["verify_attempts"],
                "calibration_id": s["calibration_id"],
            }

    def compare_profile(self, s, frame, inspected, cfg):
        result = {
            "result": "INCONCLUSIVE",
            "score": None,
            "threshold": cfg.verification_cosine_threshold,
            "model_version": self.models.versions.get("face_verification"),
            "quality": inspected["quality"],
            "created_at": utcnow(),
            "reason": None,
        }
        profile = self.store.row(
            "SELECT * FROM profiles WHERE candidate_id=? AND expires_at>?", (s["candidate_id"], utcnow())
        )
        version = self.models.versions.get("face_verification", {}).get("sha256")
        if not profile:
            result["reason"] = "REFERENCE_PROFILE_MISSING"
        elif profile["model_version"] != version:
            result["reason"] = "PROFILE_MODEL_MISMATCH"
        elif not inspected["quality"]["face_ok"]:
            result["reason"] = "QUALITY_INSUFFICIENT"
        else:
            vector = self.models.embedding(frame, inspected["faces"])
            if vector is None:
                result["reason"] = "MODEL_UNAVAILABLE"
            else:
                try:
                    reference = np.frombuffer(self.security.cipher.decrypt(profile["embedding"]), np.float32)
                    score = float(np.dot(reference, vector))
                    result.update(
                        result="MATCH" if score >= cfg.verification_cosine_threshold else "NO_MATCH",
                        score=score,
                    )
                except Exception:
                    result["reason"] = "PROFILE_UNREADABLE"
        return result

    async def enroll_profile(self, candidate_id, raw, source_ref, expires_at, actor):
        if actor["role"] not in ("admin", "service") or actor["purpose"] != "user":
            raise Problem(403, "ACCESS_DENIED", "Chỉ quản trị/backend được đăng ký hồ sơ chuẩn")
        if not self.store.row("SELECT id FROM attempts WHERE candidate_id=?", (candidate_id,)):
            raise Problem(404, "NOT_FOUND", "Thí sinh chưa có trong adapter backend")
        frame = decode_jpeg(raw, self.cfg)
        inspected = await self.infer(self.models.inspect, frame, self.cfg, False)
        if not inspected["quality"]["face_ok"]:
            raise Problem(
                409, "REFERENCE_QUALITY_FAILED", "Ảnh chuẩn cần đúng một mặt rõ", inspected["quality"]
            )
        vector = await self.infer(self.models.embedding, frame, inspected["faces"])
        if vector is None:
            raise Problem(503, "MODEL_UNAVAILABLE", "Không tạo được vector bằng SFace")
        model_hash = self.models.versions["face_verification"]["sha256"]
        encrypted = self.security.cipher.encrypt(vector.astype(np.float32).tobytes())
        with self.store.transaction() as db:
            db.execute(
                "INSERT INTO profiles VALUES(?,?,?,?,?) ON CONFLICT(candidate_id) DO UPDATE SET model_version=excluded.model_version,embedding=excluded.embedding,source_ref=excluded.source_ref,expires_at=excluded.expires_at",
                (candidate_id, model_hash, encrypted, source_ref, expires_at),
            )
            self.store.audit(actor["sub"], "PROFILE_ENROLLED_FROM_VERIFIED_SOURCE", candidate_id, db=db)
        return {
            "candidate_id": candidate_id,
            "model_version": model_hash,
            "expires_at": expires_at,
            "status": "REGISTERED",
        }

    async def calibration_sample(self, s, raw, seq, offset_ms, camera_id):
        rt = self.runtime(s)
        async with rt.lock:
            s = self.store.session(s["session_id"])
            self.require_state(s, ("CALIBRATING",))
            self.require_consent(s)
            now = time.monotonic()
            if seq <= rt.calibration_seq or seq < 0 or offset_ms < 0:
                raise Problem(409, "STALE_FRAME", "Mẫu hiệu chỉnh sai thứ tự")
            if rt.calibration_origin is None:
                rt.calibration_origin = now - offset_ms / 1000
                if offset_ms > rt.cfg.max_offset_skew_ms:
                    raise Problem(409, "INVALID_CAPTURE_TIME", "Mốc đầu hiệu chỉnh phải bắt đầu gần 0")
            if abs((now - rt.calibration_origin) * 1000 - offset_ms) > rt.cfg.max_offset_skew_ms:
                raise Problem(409, "INVALID_CAPTURE_TIME", "Mốc hiệu chỉnh lệch thời gian máy chủ")
            frame = decode_jpeg(raw, rt.cfg)
            inspected = await self.infer(self.models.inspect, frame, rt.cfg, True)
            rt.calibration_seq = seq
            pose = inspected["pose"]
            if inspected["quality"]["face_ok"] and pose and pose["eyes_ok"]:
                signature = canonical_hash(
                    {"width": frame.shape[1], "height": frame.shape[0], "camera_id": camera_id}
                )
                rt.calibration.append(
                    {
                        **pose,
                        "at": now,
                        "seq": seq,
                        "signature": signature,
                        "width": frame.shape[1],
                        "height": frame.shape[0],
                        "camera_id": camera_id,
                    }
                )
                rt.calibration = rt.calibration[-100:]
            return {
                "calibration_id": s["calibration_id"],
                "accepted": bool(pose and pose["eyes_ok"]),
                "valid_samples": len(rt.calibration),
                "quality": inspected["quality"],
            }

    async def calibration_complete(self, s, calibration_id):
        rt = self.runtime(s)
        async with rt.lock:
            s = self.store.session(s["session_id"])
            self.require_state(s, ("CALIBRATING",))
            if calibration_id != s["calibration_id"]:
                raise Problem(409, "CALIBRATION_MISMATCH", "Sai lượt hiệu chỉnh")
            samples = rt.calibration
            reason = None
            if len(samples) < rt.cfg.calibration_samples:
                reason = "NOT_ENOUGH_CLEAR_SAMPLES"
            elif (samples[-1]["at"] - samples[0]["at"]) * 1000 < rt.cfg.calibration_duration_ms:
                reason = "CALIBRATION_TOO_SHORT"
            elif len({x["signature"] for x in samples}) != 1:
                reason = "CAMERA_CHANGED"
            elif any(
                np.std([x[k] for x in samples]) > rt.cfg.calibration_max_std_deg for k in ("yaw", "pitch")
            ):
                reason = "HEAD_UNSTABLE"
            if reason:
                rt.calibration.clear()
                rt.calibration_seq, rt.calibration_origin = -1, None
                return {"status": "RETRY_REQUIRED", "reason": reason, "valid_samples": len(samples)}
            baseline = {k: float(np.median([x[k] for x in samples])) for k in ("yaw", "pitch", "iris_x")}
            baseline.update(
                eyes_ok=True,
                camera_signature=samples[-1]["signature"],
                width=samples[-1]["width"],
                height=samples[-1]["height"],
                camera_id=samples[-1]["camera_id"],
            )
            s.update(baseline=baseline, state="READY")
            with self.store.transaction() as db:
                self.store.save_session(s, db)
                db.execute(
                    "INSERT INTO calibrations VALUES(?,?,?)",
                    (
                        calibration_id,
                        s["session_id"],
                        dumps(
                            {
                                "baseline": baseline,
                                "samples": len(samples),
                                "created_at": utcnow(),
                                "config_version": rt.cfg.version,
                            }
                        ),
                    ),
                )
            return {
                "status": "PASSED",
                "state": "READY",
                "calibration_id": calibration_id,
                "gaze_status": "EXPERIMENTAL" if rt.cfg.gaze_enabled else "DISABLED",
            }

    def _stream(self, s, rt, stream_id=None):
        new_stream_id = stream_id or uid()
        if new_stream_id == s["stream_id"] or new_stream_id in s.get("used_stream_ids", []):
            raise Problem(409, "STREAM_REPLAY", "Mã luồng đã dùng; cần UUID mới")
        rt.origin = time.monotonic()
        rt.last_received = rt.last_processed = None
        rt.last_object = rt.last_verify = -1e9
        rt.pending = None
        rt.rates.clear()
        rt.engine = EventEngine(rt.cfg)
        s.update(
            stream_id=new_stream_id,
            generation=s["generation"] + 1,
            last_seq=-1,
            last_offset_ms=-1,
            resume_required=False,
        )
        s["used_stream_ids"] = [*s.get("used_stream_ids", []), new_stream_id]
        return self.security.issue(
            s["candidate_id"], "candidate", s["session_id"], "frames", generation=s["generation"]
        )

    async def begin(self, s, actor):
        rt = self.runtime(s)
        async with rt.lock:
            s = self.store.session(s["session_id"])
            self.require_state(s, ("READY",))
            if not s["verified"] or not s["baseline"] or not s["consent"]:
                raise Problem(409, "PRECONDITIONS_FAILED", "Chưa xác thực, đồng ý hoặc hiệu chỉnh")
            missing = [
                m
                for m in rt.cfg.required_modules
                if self.models.health.get(m, {}).get("status") != "AVAILABLE"
            ]
            if missing:
                raise Problem(503, "MODEL_UNAVAILABLE", "Thiếu mô-đun bắt buộc", {"modules": missing})
            if (
                sum(
                    r["state"] in ("MONITORING", "DEGRADED")
                    for r in self.store.rows("SELECT state FROM sessions")
                )
                >= rt.cfg.max_active_sessions
            ):
                raise Problem(503, "SERVICE_OVERLOADED", "Đã đủ phiên CPU thử nghiệm")
            token = self._stream(s, rt)
            s.update(
                state="MONITORING", started_at=s["started_at"] or utcnow(), health=self.models.health.copy()
            )
            with self.store.transaction() as db:
                self.store.save_session(s, db)
                self.store.audit(actor["sub"], "SESSION_STARTED", s["session_id"], db=db)
            return {
                "state": s["state"],
                "stream_id": s["stream_id"],
                "frames_token": token,
                "expires_in": self.settings.token_ttl,
            }

    async def accept(self, s, actor, raw, seq, offset_ms, camera_id):
        if actor["purpose"] != "frames":
            raise Problem(403, "FRAME_TOKEN_REQUIRED", "Dùng token chỉ dành cho thu ảnh")
        rt = self.runtime(s)
        async with rt.lock:
            s = self.store.session(s["session_id"])
            self.identity.authorize(actor, s, "frames")
            self.require_state(s, ("MONITORING", "DEGRADED"))
            if s["resume_required"]:
                raise Problem(409, "RESUME_REQUIRED", "Cần khôi phục luồng trước khi gửi ảnh")
            if seq < 0 or offset_ms < 0:
                raise Problem(400, "INVALID_REQUEST", "seq và offset không được âm")
            digest = canonical_hash(
                {"image": hashlib.sha256(raw).hexdigest(), "offset": offset_ms, "camera_id": camera_id}
            )
            old = self.store.row(
                "SELECT hash,payload FROM receipts WHERE session_id=? AND stream_id=? AND seq=?",
                (s["session_id"], s["stream_id"], seq),
            )
            if old:
                if old["hash"] != digest:
                    raise Problem(409, "FRAME_CONFLICT", "Số thứ tự đã có nội dung khác")
                return {"accepted_seq": seq, "processing_status": "DUPLICATE"}, 200
            if seq <= s["last_seq"] or offset_ms <= s["last_offset_ms"]:
                raise Problem(409, "STALE_FRAME", "Ảnh cũ hoặc sai thứ tự")
            now = time.monotonic()
            if abs((now - rt.origin) * 1000 - offset_ms) > rt.cfg.max_offset_skew_ms:
                raise Problem(409, "INVALID_CAPTURE_TIME", "Ảnh cũ hoặc mốc thời gian không hợp lệ")
            while rt.rates and now - rt.rates[0] >= 1:
                rt.rates.popleft()
            if len(rt.rates) >= rt.cfg.max_requests_per_second:
                raise Problem(429, "RATE_LIMITED", "Tối đa 5 ảnh/giây; bỏ ảnh cũ và thử lại")
            frame = decode_jpeg(raw, rt.cfg)
            if s["baseline"] and (
                frame.shape[1] != s["baseline"]["width"]
                or frame.shape[0] != s["baseline"]["height"]
                or camera_id != s["baseline"]["camera_id"]
            ):
                self.persist_changes(s, rt.engine.gap(now, "CAMERA_CHANGED"), None)
                s.update(state="CALIBRATING", baseline=None, calibration_id=uid(), resume_required=True)
                rt.pending = None
                rt.calibration.clear()
                rt.calibration_seq, rt.calibration_origin = -1, None
                with self.store.transaction() as db:
                    self.store.save_session(s, db)
                    self.store.audit(actor["sub"], "CAMERA_CHANGED", s["session_id"], db=db)
                raise Problem(
                    409, "RECALIBRATION_REQUIRED", "Camera hoặc kích thước ảnh đổi; cần hiệu chỉnh lại"
                )
            replaced = rt.pending.seq if rt.pending else None
            if replaced is not None:
                s["dropped_frames"] += 1
                self.metrics["dropped_frames"] += 1
            stamp = utcnow()
            s.update(last_seq=seq, last_offset_ms=offset_ms)
            with self.store.transaction() as db:
                db.execute(
                    "INSERT INTO receipts VALUES(?,?,?,?,?)",
                    (s["session_id"], s["stream_id"], seq, digest, dumps({"received_at": stamp})),
                )
                self.store.save_session(s, db)
            rt.pending = Packet(raw, frame, seq, now, stamp, s["generation"])
            rt.last_received = now
            rt.rates.append(now)
            if s["session_id"] not in self.queued:
                self.queued.add(s["session_id"])
                self.ready_queue.append(s["session_id"])
            self.wake.set()
            return {"accepted_seq": seq, "processing_status": "QUEUED", "replaced_pending_seq": replaced}, 202

    async def process_one(self, sid):
        s = self.store.session(sid)
        rt = self.runtime(s)
        async with rt.lock:
            packet, rt.pending = rt.pending, None
            if (
                not packet
                or s["state"] not in ("MONITORING", "DEGRADED")
                or packet.generation != s["generation"]
            ):
                return
        # Inference is outside the session lock: ingestion keeps exactly one newest pending frame.
        age = (time.monotonic() - packet.at) * 1000
        if age > rt.cfg.max_queue_age_ms:
            async with rt.lock:
                s = self.store.session(sid)
                s["dropped_frames"] += 1
                self.metrics["dropped_frames"] += 1
                self.persist_changes(s, rt.engine.gap(time.monotonic()), None)
                self.technical_set(s, rt, "PROCESSING_GAP", True, packet.utc)
                s["state"] = "DEGRADED"
                s["health"]["pipeline"] = {"status": "UNAVAILABLE", "error_code": "QUEUE_EXPIRED"}
                self.store.save_session(s)
            return
        run_objects = (packet.at - rt.last_object) * 1000 >= rt.cfg.object_interval_ms - 1e-6
        o, inspected = await self.infer(
            self.models.observe,
            packet.frame,
            rt.cfg,
            packet.at,
            packet.utc,
            packet.seq,
            s["baseline"],
            run_objects,
        )
        if run_objects:
            rt.last_object = packet.at
        if (packet.at - rt.last_verify) * 1000 >= rt.cfg.verification_interval_ms and inspected["quality"][
            "face_ok"
        ]:
            result = await self.infer(self.compare_profile, s, packet.frame, inspected, rt.cfg)
            o.identity = result["result"]
            o.data["identity_check"] = result
            # All attempts obey the schedule; an unavailable profile must not run SFace every frame.
            rt.last_verify = packet.at
            with self.store.transaction() as db:
                db.execute("INSERT INTO verifications VALUES(?,?,?)", (uid(), sid, dumps(result)))
        async with rt.lock:
            s = self.store.session(sid)  # never overwrite seq accepted while inference was running
            if s["state"] not in ("MONITORING", "DEGRADED") or packet.generation != s["generation"]:
                return
            latency = (time.monotonic() - packet.at) * 1000
            if latency > rt.cfg.max_gap_ms:
                self.persist_changes(s, rt.engine.gap(time.monotonic()), None)
                s["state"] = "DEGRADED"
                s["health"]["pipeline"] = {"status": "UNAVAILABLE", "error_code": "RESULT_EXPIRED"}
                self.technical_set(s, rt, "PROCESSING_GAP", True, packet.utc)
            else:
                self.technical_set(s, rt, "PROCESSING_GAP", False, packet.utc)
                if rt.interrupted_id:
                    self.technical_close(rt.interrupted_id, packet.utc, "RECOVERED")
                    rt.interrupted_id = None
                s["latest_observation"] = {
                    **o.data,
                    "seq": packet.seq,
                    "observed_at": packet.utc,
                    "valid_until": utc_add(packet.utc, rt.cfg.max_gap_ms / 1000),
                }
                self.persist_changes(s, rt.engine.observe(o), packet.raw)
                missing = any(
                    self.models.health.get(m, {}).get("status") != "AVAILABLE"
                    for m in rt.cfg.required_modules
                )
                self.technical_set(s, rt, "MODEL_UNAVAILABLE", missing, packet.utc)
                self.technical_set(
                    s, rt, "QUALITY_INSUFFICIENT", not o.data["quality"]["image_ok"], packet.utc
                )
                s["state"] = "DEGRADED" if missing or not o.data["quality"]["image_ok"] else "MONITORING"
                s["health"] = o.data["modules"]
                s["latest_observation"] = {
                    **o.data,
                    "seq": packet.seq,
                    "observed_at": packet.utc,
                    "valid_until": utc_add(packet.utc, rt.cfg.max_gap_ms / 1000),
                }
                s["last_observed_at"] = packet.utc
                self.metrics["processed_frames"] += 1
                self.metrics["unknown_signals"] += sum(x.value.value == "UNKNOWN" for x in o.signals.values())
                self.metrics["total_signals"] += len(o.signals)
                rt.last_processed = packet.at
            s["metrics"] = {"queue_age_ms": round(age, 2), "end_to_end_latency_ms": round(latency, 2)}
            with self.store.transaction() as db:
                self.store.save_session(s, db)
                compact_health = {m: (h.get("status"), h.get("error_code")) for m, h in s["health"].items()}
                if compact_health != rt.last_health:
                    self.store.outbox(
                        sid, "proctor.health.changed", {"health": s["health"], "state": s["state"]}, db
                    )
                    rt.last_health = compact_health

    def persist_changes(self, s, changes: list[Change], raw):
        cfg = Config.model_validate(s["config"])
        for change in changes:
            source = change.event
            old = self.store.row("SELECT payload FROM events WHERE id=?", (source.id,))
            if old:
                e = json.loads(old["payload"])
                e.update(
                    end_at=source.end_at,
                    close_reason=source.close_reason,
                    last_observed_at=source.last_observed_at,
                    version=e["version"] + 1,
                )
            else:
                e = {
                    "event_id": source.id,
                    "session_id": s["session_id"],
                    "type": source.type,
                    "start_at": source.start_at,
                    "confirmed_at": source.confirmed_at,
                    "end_at": source.end_at,
                    "last_observed_at": source.last_observed_at,
                    "close_reason": source.close_reason,
                    "review_status": "UNREVIEWED",
                    "version": 1,
                    "evidence_status": "MISSING",
                    "evidence_ids": [],
                    "config_version": cfg.version,
                    "config_hash": cfg.digest,
                    "model_versions": self.models.versions,
                    "category": "OBSERVATION",
                    "observation_data": s.get("latest_observation"),
                }
            evidence = None
            existing = self.store.rows(
                "SELECT * FROM evidence WHERE event_id=? ORDER BY captured_at DESC", (source.id,)
            )
            if change.action == "create" or (
                change.action == "update"
                and len(existing) < cfg.evidence_max_per_event
                and (
                    not existing
                    or (
                        datetime.fromisoformat(source.last_observed_at.replace("Z", "+00:00"))
                        - datetime.fromisoformat(existing[0]["captured_at"].replace("Z", "+00:00"))
                    ).total_seconds()
                    * 1000
                    >= cfg.evidence_interval_ms
                )
            ):
                evidence = self.vault.write(source.id, raw, source.last_observed_at, cfg.evidence_days)
                e["evidence_ids"].append(evidence["id"])
                if evidence["status"] == "READY" or e["evidence_status"] != "READY":
                    e["evidence_status"] = evidence["status"]
                if evidence["reason"]:
                    e["evidence_reason"] = evidence["reason"]
            with self.store.transaction() as db:
                # A synchronous review request may have committed while evidence I/O ran.
                # Merge against the current row inside the write transaction; never undo a review.
                current = db.execute("SELECT payload FROM events WHERE id=?", (source.id,)).fetchone()
                if current:
                    latest = json.loads(current["payload"])
                    e["review_status"] = latest["review_status"]
                    e["version"] = latest["version"] + 1
                    e["evidence_ids"] = list(dict.fromkeys([*latest["evidence_ids"], *e["evidence_ids"]]))
                self.store.save_event(e, db)
                if evidence:
                    self.vault.persist(evidence, db)
                self.store.outbox(
                    s["session_id"], "proctor.event.created" if not old else "proctor.event.updated", e, db
                )

    def technical_open(self, s, kind, start_at):
        eid = uid()
        e = {
            "event_id": eid,
            "session_id": s["session_id"],
            "type": kind,
            "category": "TECHNICAL",
            "start_at": start_at,
            "confirmed_at": utcnow(),
            "last_observed_at": start_at,
            "end_at": None,
            "close_reason": None,
            "review_status": "UNREVIEWED",
            "version": 1,
            "evidence_status": "MISSING",
            "evidence_reason": "NO_CURRENT_FRAME",
            "evidence_ids": [],
            "config_version": s["config_version"],
            "config_hash": s["config_hash"],
            "model_versions": self.models.versions,
        }
        with self.store.transaction() as db:
            self.store.save_event(e, db)
            self.store.outbox(s["session_id"], "proctor.event.created", e, db)
        return eid

    def technical_close(self, eid, end_at, reason):
        with self.store.transaction() as db:
            e = self.store.event(eid)
            e.update(end_at=end_at, last_observed_at=end_at, close_reason=reason, version=e["version"] + 1)
            self.store.save_event(e, db)
            self.store.outbox(e["session_id"], "proctor.event.updated", e, db)

    def technical_set(self, s, rt, kind, active, observed_at):
        if active and kind not in rt.technical_ids:
            rt.technical_ids[kind] = self.technical_open(s, kind, observed_at)
        elif not active and kind in rt.technical_ids:
            self.technical_close(rt.technical_ids.pop(kind), observed_at, "RECOVERED")
        elif active:
            with self.store.transaction() as db:
                event = self.store.event(rt.technical_ids[kind])
                event["last_observed_at"] = observed_at
                self.store.save_event(event, db)

    async def heartbeat(self, now=None):
        now = time.monotonic() if now is None else now
        for sid, rt in list(self.runtimes.items()):
            async with rt.lock:
                s = self.store.session(sid)
                if s["state"] not in ("MONITORING", "DEGRADED"):
                    continue
                self.persist_changes(s, rt.engine.tick(now), None)
                reference = rt.last_received if rt.last_received is not None else rt.origin
                if now - reference >= rt.cfg.camera_interrupted_ms / 1000 and not rt.interrupted_id:
                    self.persist_changes(s, rt.engine.gap(now), None)
                    base = s["last_observed_at"] or s["started_at"] or utcnow()
                    rt.interrupted_id = self.technical_open(
                        s, "CAMERA_INTERRUPTED", utc_add(base, rt.cfg.max_gap_ms / 1000)
                    )
                    s.update(state="DEGRADED", resume_required=True)
                    s["health"]["transport"] = {"status": "UNAVAILABLE", "error_code": "NO_FRESH_FRAMES"}
                    with self.store.transaction() as db:
                        self.store.save_session(s, db)

    async def resume(self, s, actor, old_token, stream_id, camera_changed=False):
        rt = self.runtime(s)
        async with rt.lock:
            s = self.store.session(s["session_id"])
            self.require_state(s, ("MONITORING", "DEGRADED", "READY"))
            if stream_id == s["stream_id"] or stream_id in s.get("used_stream_ids", []):
                raise Problem(409, "STREAM_REPLAY", "Mã luồng đã dùng; cần UUID mới")
            if actor["role"] not in ("service", "admin"):
                p = self.security.decode(old_token or "", allow_expired=True)
                self.identity.authorize(p, s, "frames")
                if p["purpose"] != "frames":
                    raise Problem(403, "FRAME_TOKEN_REQUIRED", "Cần token luồng cũ")
            self.persist_changes(s, rt.engine.gap(time.monotonic()), None)
            for eid in rt.technical_ids.values():
                self.technical_close(eid, utcnow(), "RESUMED")
            rt.technical_ids.clear()
            if rt.interrupted_id:
                self.technical_close(rt.interrupted_id, utcnow(), "RESUMED")
                rt.interrupted_id = None
            token = self._stream(s, rt, stream_id)
            if camera_changed or not s["baseline"]:
                s.update(state="CALIBRATING", baseline=None, calibration_id=uid())
                rt.calibration.clear()
                rt.calibration_seq, rt.calibration_origin = -1, None
            else:
                s["state"] = "MONITORING"
            with self.store.transaction() as db:
                self.store.save_session(s, db)
                self.store.audit(actor["sub"], "STREAM_RESUMED", s["session_id"], db=db)
            return {
                "state": s["state"],
                "stream_id": s["stream_id"],
                "frames_token": token,
                "calibration_required": s["state"] == "CALIBRATING",
                "calibration_id": s["calibration_id"],
            }

    async def end(self, s, actor, reason, key):
        rt = self.runtime(s)
        async with rt.lock:
            s = self.store.session(s["session_id"])
            payload = {"reason": reason}
            with self.store.transaction() as db:
                previous = self.store.idem_get(actor["sub"], "end:" + s["session_id"], key, payload, db)
                if previous:
                    return previous
                if s["state"] == "ENDED":
                    result = {"state": "ENDED", "session_id": s["session_id"], "ended_at": s["ended_at"]}
                    self.store.idem_put(actor["sub"], "end:" + s["session_id"], key, payload, result, db)
                    return result
            self.persist_changes(s, rt.engine.gap(time.monotonic(), "SESSION_ENDED"), None)
            rt.pending = None
            if rt.interrupted_id:
                self.technical_close(rt.interrupted_id, utcnow(), "SESSION_ENDED")
                rt.interrupted_id = None
            # Also close DB-open technical events and any events not present in this process's engine.
            with self.store.transaction() as db:
                for row in db.execute(
                    "SELECT payload FROM events WHERE session_id=? AND end_at IS NULL", (s["session_id"],)
                ).fetchall():
                    event = json.loads(row["payload"])
                    event.update(
                        end_at=event["last_observed_at"],
                        close_reason="SESSION_ENDED",
                        version=event["version"] + 1,
                    )
                    self.store.save_event(event, db)
                    self.store.outbox(s["session_id"], "proctor.event.updated", event, db)
            rt.technical_ids.clear()
            s.update(state="ENDED", ended_at=s["ended_at"] or utcnow(), end_reason=reason)
            result = {"state": "ENDED", "session_id": s["session_id"], "ended_at": s["ended_at"]}
            with self.store.transaction() as db:
                self.store.save_session(s, db)
                self.store.idem_put(actor["sub"], "end:" + s["session_id"], key, payload, result, db)
                self.store.outbox(s["session_id"], "proctor.session.ended", result, db)
                self.store.audit(actor["sub"], "SESSION_ENDED", s["session_id"], db=db)
            return result

    def review(self, event, actor, status, reason, version, key):
        body = {"status": status, "reason": reason, "expected_version": version}
        with self.store.transaction() as db:
            previous = self.store.idem_get(actor["sub"], "review:" + event["event_id"], key, body, db)
            if previous:
                return previous
            event = self.store.event(event["event_id"])
            if event["version"] != version:
                raise Problem(
                    409, "VERSION_CONFLICT", "Sự kiện đã được cập nhật", {"current_version": event["version"]}
                )
            before = event["review_status"]
            event.update(review_status=status, version=version + 1)
            self.store.save_event(event, db)
            db.execute(
                "INSERT INTO reviews VALUES(?,?,?,?,?,?,?)",
                (uid(), event["event_id"], actor["sub"], before, status, reason, utcnow()),
            )
            self.store.audit(actor["sub"], "EVENT_REVIEWED", event["event_id"], db=db)
            self.store.outbox(event["session_id"], "proctor.event.updated", event, db)
            self.store.idem_put(actor["sub"], "review:" + event["event_id"], key, body, event, db)
            return event

    async def dispatch_once(self):
        rows = self.store.rows(
            "SELECT * FROM outbox WHERE sent_at IS NULL AND dead=0 AND next_attempt<=? ORDER BY id LIMIT 50",
            (time.time(),),
        )
        for row in rows:
            targets = list(self.subscribers.get(row["session_id"], set()))
            if not targets:
                continue  # retain for the next subscriber or the authorized replay endpoint
            failed = False
            for ws in targets:
                try:
                    await asyncio.wait_for(ws.send_text(row["payload"]), timeout=1)
                except Exception:
                    failed = True
                    self.subscribers.get(row["session_id"], set()).discard(ws)
            if failed:
                attempts = row["attempts"] + 1
                self.store.execute(
                    "UPDATE outbox SET attempts=?,next_attempt=?,dead=? WHERE id=?",
                    (attempts, time.time() + min(60, 2 ** min(attempts, 6)), int(attempts >= 10), row["id"]),
                )
            else:
                self.store.execute("UPDATE outbox SET sent_at=? WHERE id=?", (utcnow(), row["id"]))

    async def _worker(self):
        while True:
            await self.wake.wait()
            while self.ready_queue:
                sid = self.ready_queue.popleft()
                self.queued.discard(sid)
                try:
                    await self.process_one(sid)
                except Exception:
                    # No traceback or image/vector/token in logs. Supervisor remains alive.
                    s = self.store.session(sid)
                    rt = self.runtime(s)
                    async with rt.lock:
                        self.persist_changes(s, rt.engine.gap(time.monotonic()), None)
                        s["state"] = "DEGRADED" if s["state"] != "ENDED" else "ENDED"
                        s["health"]["pipeline"] = {"status": "UNAVAILABLE", "error_code": "PIPELINE_FAILED"}
                        if s["state"] != "ENDED":
                            self.technical_set(s, rt, "PROCESSING_GAP", True, utcnow())
                        with self.store.transaction() as db:
                            self.store.save_session(s, db)
            self.wake.clear()

    async def _maintenance(self):
        n = 0
        while True:
            await asyncio.sleep(0.25)
            await self.heartbeat()
            n += 1
            if n % 240 == 0:
                self.vault.purge()

    async def _outbox_loop(self):
        while True:
            await self.dispatch_once()
            await asyncio.sleep(0.25)
