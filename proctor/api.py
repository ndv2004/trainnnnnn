from __future__ import annotations

import asyncio
import base64
import json
import secrets
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import UUID

import jwt
import psutil
from fastapi import (
    Depends,
    FastAPI,
    File,
    Form,
    Header,
    Query,
    Request,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from starlette.exceptions import HTTPException

from .config import ROOT, Config, Settings
from .domain import uid, utcnow
from .images import Problem
from .middleware import BodyLimit
from .security import StandaloneIdentityAdapter
from .service import Service
from .store import dumps

PREFIX = "/api/v1/proctoring"


class StrictBody(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreateSession(StrictBody):
    attempt_id: UUID


class Consent(StrictBody):
    accepted: bool


class EndBody(StrictBody):
    reason: str = Field(min_length=3, max_length=200)


class CompleteBody(StrictBody):
    calibration_id: UUID


class ResumeBody(StrictBody):
    old_token: str | None = Field(default=None, max_length=4096)
    stream_id: UUID
    camera_changed: bool = False


class ReviewBody(StrictBody):
    status: Literal["UNREVIEWED", "DISMISSED", "NEEDS_FOLLOWUP", "CONFIRMED_OBSERVATION"]
    reason: str = Field(min_length=5, max_length=1000)
    expected_version: int = Field(ge=1)


class ManualBody(StrictBody):
    decision: Literal["ALLOW_RETRY", "ACCEPT_MANUALLY"]
    reason: str = Field(min_length=5, max_length=1000)
    expected_version: int = Field(ge=1)


class ConfigBody(StrictBody):
    config: Config
    reason: str = Field(min_length=5, max_length=1000)


class LoginBody(StrictBody):
    account: Literal["candidate_a", "candidate_b", "proctor_a", "proctor_b", "outsider", "admin", "backend"]


class AttemptBody(StrictBody):
    attempt_id: UUID
    candidate_id: str = Field(min_length=3, max_length=100)
    exam_id: str = Field(min_length=1, max_length=100)
    proctor_ids: list[str] = Field(max_length=20)


class RevokeBody(StrictBody):
    token: str = Field(max_length=4096)


class ClientHealthBody(StrictBody):
    code: Literal["CAMERA_PERMISSION_LOST", "CAMERA_STOPPED", "CLIENT_TRANSPORT_ERROR"]


def create_app(settings: Settings | None = None, model_suite=None, start_tasks=True):
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app):
        service = Service(settings, model_suite)
        app.state.service = service
        if start_tasks:
            await service.start()
        try:
            yield
        finally:
            await service.stop()

    app = FastAPI(
        title="Exam Watch — AI hỗ trợ giám sát",
        version="0.1.0",
        lifespan=lifespan,
        description="Bản CPU độc lập. Dấu hiệu dành cho giám thị xem xét; không tự kết luận gian lận.",
    )
    app.add_middleware(BodyLimit)

    def result(payload, status=200):
        return JSONResponse(payload, status_code=status, headers={"Cache-Control": "no-store"})

    @app.middleware("http")
    async def boundaries(request: Request, call_next):
        request.state.request_id = "req-" + secrets.token_hex(12)
        # Local demo cannot be exposed through host rebinding, a proxy or permissive CORS.
        if settings.demo:
            host = request.url.hostname
            peer = request.client.host if request.client else ""
            if host not in ("localhost", "127.0.0.1", "::1", "testserver") or peer not in (
                "127.0.0.1",
                "::1",
                "testclient",
            ):
                return result(
                    {
                        "error": {
                            "code": "LOCALHOST_ONLY",
                            "message": "Demo chỉ mở trên localhost",
                            "details": {},
                        },
                        "request_id": request.state.request_id,
                    },
                    403,
                )
        elif (
            request.client
            and request.client.host not in ("127.0.0.1", "::1", "testclient")
            and request.url.scheme != "https"
        ):
            return result(
                {
                    "error": {
                        "code": "HTTPS_REQUIRED",
                        "message": "Truy cập qua mạng cần HTTPS",
                        "details": {},
                    },
                    "request_id": request.state.request_id,
                },
                403,
            )
        origin = request.headers.get("origin")
        if origin and origin != str(request.base_url).rstrip("/"):
            return result(
                {
                    "error": {
                        "code": "ORIGIN_DENIED",
                        "message": "Không cho phép truy cập khác nguồn",
                        "details": {},
                    },
                    "request_id": request.state.request_id,
                },
                403,
            )
        try:
            length = int(request.headers.get("content-length", "0"))
        except ValueError:
            length = 10**12
        if length > 1048576 + 65536:
            return result(
                {
                    "error": {
                        "code": "IMAGE_TOO_LARGE",
                        "message": "Yêu cầu vượt giới hạn kích thước",
                        "details": {},
                    },
                    "request_id": request.state.request_id,
                },
                413,
            )
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' blob:; media-src 'self' blob:; style-src 'self'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'"
        )
        return response

    @app.exception_handler(Problem)
    async def problem_handler(request, exc):
        headers = {"Cache-Control": "no-store"}
        if exc.status in (429, 503):
            headers["Retry-After"] = "1"
        return JSONResponse(
            {
                "error": {"code": exc.code, "message": exc.message, "details": exc.details},
                "request_id": request.state.request_id,
            },
            exc.status,
            headers=headers,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_handler(request, exc):
        # Pydantic errors can contain raw inputs; return locations/types only.
        return result(
            {
                "error": {
                    "code": "INVALID_REQUEST",
                    "message": "Dữ liệu yêu cầu không hợp lệ",
                    "details": {
                        "fields": [{"location": list(e["loc"]), "type": e["type"]} for e in exc.errors()]
                    },
                },
                "request_id": request.state.request_id,
            },
            400,
        )

    @app.exception_handler(HTTPException)
    async def http_handler(request, exc):
        return result(
            {
                "error": {
                    "code": "NOT_FOUND" if exc.status_code == 404 else "INVALID_REQUEST",
                    "message": "Không tìm thấy" if exc.status_code == 404 else "Yêu cầu không hợp lệ",
                    "details": {},
                },
                "request_id": request.state.request_id,
            },
            exc.status_code,
        )

    @app.exception_handler(Exception)
    async def unexpected(request, exc):
        return result(
            {
                "error": {
                    "code": "INTERNAL_ERROR",
                    "message": "Dịch vụ gặp lỗi; dùng request_id để đối soát",
                    "details": {},
                },
                "request_id": request.state.request_id,
            },
            500,
        )

    def svc(request: Request) -> Service:
        return request.app.state.service

    def actor(request: Request, authorization: Annotated[str | None, Header()] = None):
        if not authorization or not authorization.startswith("Bearer "):
            raise Problem(401, "TOKEN_REQUIRED", "Cần Bearer token")
        p = svc(request).security.decode(authorization[7:])
        if p["purpose"] not in ("user", "frames"):
            raise Problem(403, "ACCESS_DENIED", "Sai mục đích token")
        return p

    def session(service, p, sid, action="read"):
        s = service.store.session(str(sid))
        service.identity.authorize(p, s, action)
        return s

    def backend(p):
        if p["role"] not in ("service", "admin") or p["purpose"] != "user":
            raise Problem(403, "ACCESS_DENIED", "Thao tác cần danh tính backend/quản trị")

    def with_request(request, payload):
        return {**payload, "request_id": request.state.request_id}

    async def image_bytes(image):
        raw = await image.read(1048577)
        await image.close()
        if len(raw) > 1048576:
            raise Problem(413, "IMAGE_TOO_LARGE", "Ảnh vượt 1 MiB")
        return raw

    @app.get("/", include_in_schema=False)
    def home():
        return FileResponse(ROOT / "proctor/web/index.html")

    app.mount("/assets", StaticFiles(directory=ROOT / "proctor/web"), name="assets")

    @app.get("/health/live")
    def live(request: Request):
        return with_request(request, {"status": "ALIVE", "service": "exam-watch-local"})

    @app.get("/health/ready")
    def ready(request: Request, service=Depends(svc)):
        missing = [
            m
            for m in service.cfg.required_modules
            if service.models.health.get(m, {}).get("status") != "AVAILABLE"
        ]
        db_ok = True
        try:
            service.store.row("SELECT 1")
        except Exception:
            db_ok = False
        return result(
            with_request(
                request,
                {
                    "status": "READY" if not missing and db_ok else "UNAVAILABLE",
                    "modules": service.models.health,
                    "storage": "READY" if db_ok else "UNAVAILABLE",
                    "config_version": service.cfg.version,
                    "demo": settings.demo,
                },
            ),
            200 if not missing and db_ok else 503,
        )

    @app.get("/demo/info")
    def demo_info(request: Request):
        if not settings.demo:
            raise Problem(404, "NOT_FOUND", "Demo đã tắt")
        return with_request(
            request,
            {
                "standalone_demo": True,
                "attempts": StandaloneIdentityAdapter.DEMO_ATTEMPTS,
                "accounts": list(StandaloneIdentityAdapter.ACCOUNTS),
                "retention_days": svc(request).cfg.evidence_days,
            },
        )

    @app.post("/demo/login")
    def demo_login(request: Request, body: LoginBody, service=Depends(svc)):
        if not settings.demo:
            raise Problem(404, "NOT_FOUND", "Demo đã tắt")
        sub, role = StandaloneIdentityAdapter.ACCOUNTS[body.account]
        return with_request(
            request,
            {
                "access_token": service.security.issue(sub, role),
                "role": role,
                "actor_id": sub,
                "expires_in": 900,
            },
        )

    @app.post(PREFIX + "/sessions")
    def create_session(
        request: Request,
        body: CreateSession,
        p=Depends(actor),
        service=Depends(svc),
        idempotency_key: Annotated[str | None, Header()] = None,
    ):
        payload, new = service.create(p, str(body.attempt_id), idempotency_key)
        return result(with_request(request, payload), 201 if new else 200)

    @app.get(PREFIX + "/sessions")
    def list_sessions(request: Request, p=Depends(actor), service=Depends(svc)):
        if p["purpose"] != "user":
            raise Problem(403, "ACCESS_DENIED", "Cần token người dùng")
        items = []
        for row in service.store.rows("SELECT payload FROM sessions ORDER BY rowid DESC LIMIT 500"):
            s = json.loads(row["payload"])
            try:
                service.identity.authorize(p, s)
                items.append(service.public_session(s))
            except Problem:
                continue
        return with_request(request, {"items": items})

    @app.get(PREFIX + "/sessions/{sid}")
    def get_session(request: Request, sid: UUID, p=Depends(actor), service=Depends(svc)):
        s = session(service, p, sid)
        data = service.public_session(s)
        latest = data.get("latest_observation")
        if latest and latest.get("valid_until", "") < utcnow():
            data["latest_observation"] = {
                "status": "UNKNOWN",
                "reason": "OBSERVATION_EXPIRED",
                "seq": latest["seq"],
                "observed_at": latest["observed_at"],
                "valid_until": latest["valid_until"],
            }
        return with_request(request, data)

    @app.post(PREFIX + "/sessions/{sid}/consent")
    def consent(request: Request, sid: UUID, body: Consent, p=Depends(actor), service=Depends(svc)):
        s = session(service, p, sid, "candidate")
        service.require_state(s, ("CREATED", "PRECHECK", "VERIFYING"))
        s["consent"] = body.accepted
        with service.store.transaction() as db:
            service.store.save_session(s, db)
            service.store.audit(
                p["sub"], "CONSENT_ACCEPTED" if body.accepted else "CONSENT_DECLINED", str(sid), db=db
            )
        return with_request(request, {"accepted": body.accepted})

    @app.post(PREFIX + "/sessions/{sid}/precheck")
    async def precheck(
        request: Request, sid: UUID, image: UploadFile = File(...), p=Depends(actor), service=Depends(svc)
    ):
        s = session(service, p, sid, "candidate")
        return with_request(request, await service.precheck(s, await image_bytes(image)))

    @app.get(PREFIX + "/sessions/{sid}/verification-history")
    def verification_history(request: Request, sid: UUID, p=Depends(actor), service=Depends(svc)):
        session(service, p, sid, "manual")
        attempts = [
            json.loads(r["payload"])
            for r in service.store.rows(
                "SELECT payload FROM verifications WHERE session_id=? ORDER BY rowid", (str(sid),)
            )
        ]
        prechecks = [
            json.loads(r["payload"])
            for r in service.store.rows(
                "SELECT payload FROM prechecks WHERE session_id=? ORDER BY rowid", (str(sid),)
            )
        ]
        manual = [
            {**r, "payload": json.loads(r["payload"])}
            for r in service.store.rows(
                "SELECT actor_id,payload,created_at FROM manual_decisions WHERE session_id=? ORDER BY created_at",
                (str(sid),),
            )
        ]
        return with_request(
            request, {"prechecks": prechecks, "attempts": attempts, "manual_decisions": manual}
        )

    @app.post(PREFIX + "/sessions/{sid}/verify")
    async def verify(
        request: Request, sid: UUID, image: UploadFile = File(...), p=Depends(actor), service=Depends(svc)
    ):
        s = session(service, p, sid, "candidate")
        return with_request(request, await service.verify(s, await image_bytes(image)))

    @app.post(PREFIX + "/sessions/{sid}/calibration/samples", status_code=202)
    async def calibration_sample(
        request: Request,
        sid: UUID,
        image: UploadFile = File(...),
        seq: int = Form(...),
        capture_offset_ms: int = Form(...),
        camera_id: str = Form("default", max_length=200),
        p=Depends(actor),
        service=Depends(svc),
    ):
        s = session(service, p, sid, "candidate")
        return with_request(
            request,
            await service.calibration_sample(s, await image_bytes(image), seq, capture_offset_ms, camera_id),
        )

    @app.post(PREFIX + "/sessions/{sid}/calibration/complete")
    async def calibration_complete(
        request: Request, sid: UUID, body: CompleteBody, p=Depends(actor), service=Depends(svc)
    ):
        s = session(service, p, sid, "candidate")
        return with_request(request, await service.calibration_complete(s, str(body.calibration_id)))

    @app.post(PREFIX + "/sessions/{sid}/start")
    async def start(request: Request, sid: UUID, p=Depends(actor), service=Depends(svc)):
        backend(p)
        s = session(service, p, sid)
        return with_request(request, await service.begin(s, p))

    @app.post(PREFIX + "/sessions/{sid}/frame-token")
    def renew_frame_token(request: Request, sid: UUID, p=Depends(actor), service=Depends(svc)):
        s = session(service, p, sid, "candidate")
        service.require_state(s, ("MONITORING", "DEGRADED"))
        if s["resume_required"]:
            raise Problem(409, "RESUME_REQUIRED", "Cần khôi phục luồng trước khi cấp token")
        return with_request(
            request,
            {
                "frames_token": service.security.issue(
                    s["candidate_id"], "candidate", str(sid), "frames", generation=s["generation"]
                ),
                "expires_in": 900,
            },
        )

    @app.get(PREFIX + "/capabilities")
    def capabilities(request: Request, p=Depends(actor), service=Depends(svc)):
        return with_request(
            request,
            {
                "modules": service.models.health,
                "gaze": {
                    "status": "EXPERIMENTAL" if service.cfg.gaze_enabled else "DISABLED",
                    "reason": "NO_LABELED_GAZE_VALIDATION",
                    "scope": "relative_horizontal_only",
                },
                "liveness": {"status": "UNAVAILABLE", "reason": "ANTI_SPOOF_NOT_IMPLEMENTED_OR_VALIDATED"},
                "identity_adapter": "standalone",
                "runtime": "CPU",
            },
        )

    @app.post(PREFIX + "/sessions/{sid}/liveness/challenge")
    def liveness(request: Request, sid: UUID, p=Depends(actor), service=Depends(svc)):
        session(service, p, sid, "candidate")
        raise Problem(
            503,
            "LIVENESS_UNAVAILABLE",
            "Chưa triển khai và kiểm chứng chống giả mạo; không cấp thử thách giả",
        )

    @app.post(PREFIX + "/sessions/{sid}/client-health")
    async def client_health(
        request: Request, sid: UUID, body: ClientHealthBody, p=Depends(actor), service=Depends(svc)
    ):
        s = session(service, p, sid, "candidate")
        rt = service.runtime(s)
        async with rt.lock:
            s = service.store.session(str(sid))
            service.require_state(s, ("MONITORING", "DEGRADED"))
            service.persist_changes(s, rt.engine.gap(asyncio.get_running_loop().time()), None)
            rt.pending = None
            if not rt.interrupted_id:
                rt.interrupted_id = service.technical_open(s, "CAMERA_INTERRUPTED", utcnow())
            s.update(state="DEGRADED", resume_required=True)
            s["health"]["camera"] = {"status": "UNAVAILABLE", "error_code": body.code}
            with service.store.transaction() as db:
                service.store.save_session(s, db)
                service.store.audit(p["sub"], body.code, str(sid), db=db)
        return with_request(request, {"state": "DEGRADED", "resume_required": True})

    @app.post("/demo/sessions/{sid}/start")
    async def demo_start(request: Request, sid: UUID, p=Depends(actor), service=Depends(svc)):
        if not settings.demo:
            raise Problem(404, "NOT_FOUND", "Demo đã tắt")
        s = session(service, p, sid, "candidate")
        return with_request(
            request, await service.begin(s, {**p, "sub": "demo-backend-on-behalf-of-" + p["sub"]})
        )

    @app.post(PREFIX + "/sessions/{sid}/frames", status_code=202)
    async def frames(
        request: Request,
        sid: UUID,
        image: UploadFile = File(...),
        seq: int = Form(...),
        capture_offset_ms: int = Form(...),
        camera_id: str = Form("default", max_length=200),
        p=Depends(actor),
        service=Depends(svc),
    ):
        s = session(service, p, sid, "frames")
        payload, status = await service.accept(
            s, p, await image_bytes(image), seq, capture_offset_ms, camera_id
        )
        return result(with_request(request, payload), status)

    @app.post(PREFIX + "/sessions/{sid}/resume")
    async def resume(request: Request, sid: UUID, body: ResumeBody, p=Depends(actor), service=Depends(svc)):
        s = session(service, p, sid, "candidate")
        return with_request(
            request, await service.resume(s, p, body.old_token, str(body.stream_id), body.camera_changed)
        )

    @app.post(PREFIX + "/sessions/{sid}/end")
    async def end(
        request: Request,
        sid: UUID,
        body: EndBody,
        p=Depends(actor),
        service=Depends(svc),
        idempotency_key: Annotated[str | None, Header()] = None,
    ):
        s = session(service, p, sid, "candidate")
        if p["role"] == "candidate" and body.reason != "PARTICIPANT_STOPPED":
            raise Problem(403, "ACCESS_DENIED", "Thí sinh chỉ được dừng tham gia thử nghiệm")
        return with_request(request, await service.end(s, p, body.reason, idempotency_key))

    @app.get(PREFIX + "/sessions/{sid}/events")
    def events(
        request: Request,
        sid: UUID,
        cursor: str | None = None,
        limit: int = Query(50, ge=1, le=100),
        type: str | None = None,
        review_status: str | None = None,
        p=Depends(actor),
        service=Depends(svc),
    ):
        session(service, p, sid)
        clauses, args = ["session_id=?"], [str(sid)]
        if cursor:
            try:
                t, eid = json.loads(base64.urlsafe_b64decode(cursor.encode()))
                clauses.append("(start_at>? OR (start_at=? AND id>?))")
                args.extend([t, t, eid])
            except Exception:
                raise Problem(400, "INVALID_CURSOR", "Cursor không hợp lệ") from None
        if type:
            clauses.append("type=?")
            args.append(type)
        if review_status:
            clauses.append("review_status=?")
            args.append(review_status)
        rows = service.store.rows(
            "SELECT payload FROM events WHERE " + " AND ".join(clauses) + " ORDER BY start_at,id LIMIT ?",
            (*args, limit + 1),
        )
        items = [json.loads(r["payload"]) for r in rows[:limit]]
        next_cursor = (
            base64.urlsafe_b64encode(dumps([items[-1]["start_at"], items[-1]["event_id"]]).encode()).decode()
            if len(rows) > limit
            else None
        )
        return with_request(request, {"items": items, "next_cursor": next_cursor})

    def event_for(service, p, eid, action):
        event = service.store.event(str(eid))
        session(service, p, event["session_id"], action)
        return event

    @app.patch(PREFIX + "/events/{eid}/review")
    def review(
        request: Request,
        eid: UUID,
        body: ReviewBody,
        p=Depends(actor),
        service=Depends(svc),
        idempotency_key: Annotated[str | None, Header()] = None,
    ):
        event = event_for(service, p, eid, "review")
        return with_request(
            request,
            service.review(event, p, body.status, body.reason, body.expected_version, idempotency_key),
        )

    @app.get(PREFIX + "/events/{eid}/reviews")
    def reviews(request: Request, eid: UUID, p=Depends(actor), service=Depends(svc)):
        event_for(service, p, eid, "review")
        return with_request(
            request,
            {
                "items": service.store.rows(
                    "SELECT * FROM reviews WHERE event_id=? ORDER BY created_at", (str(eid),)
                )
            },
        )

    @app.post(PREFIX + "/sessions/{sid}/manual-decision")
    async def manual(
        request: Request,
        sid: UUID,
        body: ManualBody,
        p=Depends(actor),
        service=Depends(svc),
        idempotency_key: Annotated[str | None, Header()] = None,
    ):
        s = session(service, p, sid, "manual")
        rt = service.runtime(s)
        async with rt.lock:
            s = service.store.session(str(sid))
            with service.store.transaction() as db:
                previous = service.store.idem_get(
                    p["sub"], "manual:" + str(sid), idempotency_key, body.model_dump(), db
                )
                if previous:
                    return with_request(request, previous)
                service.require_state(s, ("VERIFYING", "PRECHECK"))
                if body.expected_version != s["version"]:
                    raise Problem(
                        409, "VERSION_CONFLICT", "Phiên đã thay đổi", {"current_version": s["version"]}
                    )
                if body.decision == "ALLOW_RETRY":
                    s.update(verify_attempts=0, state="VERIFYING", verified=False)
                else:
                    if not s.get("precheck_ok") or not s["consent"]:
                        raise Problem(409, "PRECONDITIONS_FAILED", "Cần kiểm tra ảnh và đồng ý trước")
                    s.update(
                        verified=True,
                        state="CALIBRATING",
                        calibration_id=uid(),
                        manual_override={
                            "actor": p["sub"],
                            "reason": body.reason,
                            "at": utcnow(),
                            "decision": body.decision,
                        },
                    )
                s["version"] += 1
                service.store.save_session(s, db)
                service.store.audit(p["sub"], "MANUAL_" + body.decision, str(sid), db=db)
                db.execute(
                    "INSERT INTO manual_decisions VALUES(?,?,?,?,?)",
                    (uid(), str(sid), p["sub"], dumps(body.model_dump()), utcnow()),
                )
                response = {
                    "state": s["state"],
                    "version": s["version"],
                    "calibration_id": s["calibration_id"],
                    "manual_override": s["manual_override"],
                }
                service.store.idem_put(
                    p["sub"], "manual:" + str(sid), idempotency_key, body.model_dump(), response, db
                )
                return with_request(request, response)

    def evidence_for(service, p, eid):
        row = service.store.row("SELECT * FROM evidence WHERE id=?", (str(eid),))
        if not row:
            raise Problem(404, "NOT_FOUND", "Không tìm thấy minh chứng")
        event_for(service, p, row["event_id"], "evidence")
        return row

    @app.get(PREFIX + "/evidence/{eid}/access")
    def access(request: Request, eid: UUID, p=Depends(actor), service=Depends(svc)):
        row = evidence_for(service, p, eid)
        if row["status"] != "READY" or row["expires_at"] <= utcnow():
            raise Problem(410, "EVIDENCE_UNAVAILABLE", "Không có minh chứng còn hạn")
        return with_request(request, service.vault.signed_access(str(eid), p["sub"]))

    @app.get(PREFIX + "/evidence/{eid}/content")
    def content(request: Request, eid: UUID, signature: str, p=Depends(actor), service=Depends(svc)):
        row = evidence_for(service, p, eid)
        try:
            signed = jwt.decode(
                signature,
                service.security.key,
                algorithms=["HS256"],
                options={"require": ["exp", "eid", "sub", "purpose"]},
            )
            if signed["eid"] != str(eid) or signed["sub"] != p["sub"] or signed["purpose"] != "evidence":
                raise jwt.InvalidTokenError()
        except jwt.InvalidTokenError:
            raise Problem(
                403, "EVIDENCE_URL_EXPIRED_OR_INVALID", "URL minh chứng hết hạn hoặc không hợp lệ"
            ) from None
        return Response(
            service.vault.read(row), media_type="image/jpeg", headers={"Cache-Control": "no-store"}
        )

    @app.post(PREFIX + "/configs", status_code=201)
    def config(request: Request, body: ConfigBody, p=Depends(actor), service=Depends(svc)):
        if p["role"] != "admin" or p["purpose"] != "user":
            raise Problem(403, "ACCESS_DENIED", "Chỉ quản trị được thay cấu hình")
        service.store.add_config(body.config, p["sub"], body.reason, activate=True)
        service.cfg = body.config
        return with_request(
            request,
            {"config_version": service.cfg.version, "hash": service.cfg.digest, "applies_to": "NEW_SESSIONS"},
        )

    @app.post(PREFIX + "/profiles/{candidate_id}")
    async def enroll(
        request: Request,
        candidate_id: str,
        image: UploadFile = File(...),
        source_ref: str = Form(..., min_length=5, max_length=500),
        expires_at: str = Form(...),
        p=Depends(actor),
        service=Depends(svc),
    ):
        backend(p)
        try:
            expiry = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
            if expiry.tzinfo is None or not 0 < (expiry - datetime.now(UTC)).total_seconds() <= 365 * 86400:
                raise ValueError
            expiry_s = expiry.astimezone(UTC).isoformat().replace("+00:00", "Z")
        except ValueError:
            raise Problem(400, "INVALID_EXPIRY", "Hồ sơ cần thời hạn trong 365 ngày và múi giờ") from None
        return with_request(
            request,
            await service.enroll_profile(candidate_id, await image_bytes(image), source_ref, expiry_s, p),
        )

    @app.delete(PREFIX + "/profiles/{candidate_id}")
    def delete_profile(request: Request, candidate_id: str, p=Depends(actor), service=Depends(svc)):
        backend(p)
        with service.store.transaction() as db:
            db.execute("DELETE FROM profiles WHERE candidate_id=?", (candidate_id,))
            service.store.audit(p["sub"], "PROFILE_DELETED", candidate_id, db=db)
        return with_request(request, {"status": "DELETED"})

    @app.post(PREFIX + "/admin/attempts", status_code=201)
    def add_attempt(request: Request, body: AttemptBody, p=Depends(actor), service=Depends(svc)):
        backend(p)
        with service.store.transaction() as db:
            existing = db.execute("SELECT id FROM attempts WHERE id=?", (str(body.attempt_id),)).fetchone()
            if existing:
                raise Problem(409, "ATTEMPT_EXISTS", "Lượt thi đã tồn tại; không được đổi danh tính ngầm")
            db.execute(
                "INSERT INTO attempts VALUES(?,?,?,?)",
                (str(body.attempt_id), body.candidate_id, body.exam_id, dumps(body.proctor_ids)),
            )
            service.store.audit(p["sub"], "ATTEMPT_REGISTERED", str(body.attempt_id), db=db)
        return with_request(request, {"attempt_id": str(body.attempt_id), "status": "REGISTERED"})

    @app.post(PREFIX + "/admin/tokens/revoke")
    def revoke(request: Request, body: RevokeBody, p=Depends(actor), service=Depends(svc)):
        backend(p)
        target = service.security.decode(body.token, allow_expired=True)
        with service.store.transaction() as db:
            service.security.revoke(target, db)
            service.store.audit(p["sub"], "TOKEN_REVOKED", target["sub"], db=db)
        return with_request(request, {"status": "REVOKED"})

    @app.get(PREFIX + "/sessions/{sid}/notifications")
    def notifications(
        request: Request,
        sid: UUID,
        after_id: int = Query(0, ge=0),
        limit: int = Query(100, ge=1, le=200),
        p=Depends(actor),
        service=Depends(svc),
    ):
        session(service, p, sid, "review")
        rows = service.store.rows(
            "SELECT id,payload FROM outbox WHERE session_id=? AND id>? ORDER BY id LIMIT ?",
            (str(sid), after_id, limit),
        )
        return with_request(
            request,
            {
                "items": [{"cursor": r["id"], **json.loads(r["payload"])} for r in rows],
                "next_after_id": rows[-1]["id"] if rows else after_id,
            },
        )

    @app.get(PREFIX + "/admin/metrics")
    def metrics(request: Request, p=Depends(actor), service=Depends(svc)):
        backend(p)
        return with_request(
            request,
            {
                **service.metrics,
                "cpu_percent": psutil.cpu_percent(),
                "rss_bytes": psutil.Process().memory_info().rss,
                "pending_frames": sum(rt.pending is not None for rt in service.runtimes.values()),
                "outbox_pending": service.store.row("SELECT count(*) n FROM outbox WHERE sent_at IS NULL")[
                    "n"
                ],
            },
        )

    @app.post(PREFIX + "/admin/outbox/retry")
    def retry_outbox(request: Request, p=Depends(actor), service=Depends(svc)):
        backend(p)
        service.store.execute("UPDATE outbox SET dead=0,attempts=0,next_attempt=0 WHERE dead=1")
        return with_request(request, {"status": "RETRY_SCHEDULED"})

    @app.post(PREFIX + "/admin/purge")
    def purge(request: Request, p=Depends(actor), service=Depends(svc)):
        backend(p)
        return with_request(request, {"deleted_evidence": service.vault.purge()})

    @app.websocket(PREFIX + "/sessions/{sid}/ws")
    async def websocket(ws: WebSocket, sid: UUID):
        service = ws.app.state.service
        if settings.demo and (
            ws.url.hostname not in ("localhost", "127.0.0.1", "::1", "testserver")
            or ws.client.host not in ("127.0.0.1", "::1", "testclient")
        ):
            await ws.close(code=1008)
            return
        origin = ws.headers.get("origin")
        if origin and origin != str(ws.url).split(PREFIX)[0].replace("ws://", "http://").replace(
            "wss://", "https://"
        ):
            await ws.close(code=1008)
            return
        await ws.accept()
        registered = False
        try:
            auth = await asyncio.wait_for(ws.receive_json(), 5)
            token = auth.get("token", "")
            p = service.security.decode(token)
            session(service, p, sid, "review")
            if p["purpose"] != "user":
                raise Problem(403, "ACCESS_DENIED", "Cần token giám thị")
            service.subscribers.setdefault(str(sid), set()).add(ws)
            registered = True
            await ws.send_json(
                {
                    "type": "subscribed",
                    "session_id": str(sid),
                    "replay_url": f"{PREFIX}/sessions/{sid}/notifications",
                }
            )
            while True:
                try:
                    await asyncio.wait_for(ws.receive_text(), 15)
                except TimeoutError:
                    pass
                p = service.security.decode(token)
                session(service, p, sid, "review")
                await ws.send_json({"type": "heartbeat", "server_time": utcnow()})
        except (Problem, TimeoutError, ValueError, KeyError):
            await ws.close(code=1008)
        except WebSocketDisconnect:
            pass
        finally:
            if registered:
                service.subscribers.get(str(sid), set()).discard(ws)

    return app
