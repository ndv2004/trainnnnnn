import json
import time
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import cv2
import jwt
import numpy as np
import pytest
from conftest import headers, monitoring, new_session, token
from starlette.websockets import WebSocketDisconnect

from proctor.config import Config
from proctor.domain import Change, EngineEvent, uid, utc_add, utcnow
from proctor.images import Problem

P = "/api/v1/proctoring"


def frame(client, sid, fh, raw, seq=1, offset=0):
    return client.post(
        f"{P}/sessions/{sid}/frames",
        headers=fh,
        files={"image": ("f.jpg", raw, "image/jpeg")},
        data={"seq": seq, "capture_offset_ms": offset},
    )


def event(client, sid, raw=None, kind="PHONE_VISIBLE"):
    stamp = utcnow()
    engine_event = EngineEvent(uid(), kind, 0, 1, stamp, stamp, 1, stamp)
    client.service.persist_changes(client.service.store.session(sid), [Change("create", engine_event)], raw)
    return client.service.store.event(engine_event.id)


def test_create_unique_idempotency_and_body_collision(client):
    sid, h = new_session(client)
    attempt = client.get("/demo/info").json()["attempts"]["candidate_a"]
    r = client.post(P + "/sessions", json={"attempt_id": attempt}, headers=h)
    assert r.status_code == 200 and r.json()["session_id"] == sid
    attempt_b = client.get("/demo/info").json()["attempts"]["candidate_b"]
    backend = headers(client, "backend", "same-key")
    assert client.post(P + "/sessions", json={"attempt_id": attempt}, headers=backend).status_code == 200
    assert client.post(P + "/sessions", json={"attempt_id": attempt_b}, headers=backend).status_code == 409
    assert client.service.store.row("SELECT count(*) n FROM sessions")["n"] == 1


def test_missing_profile_never_auto_enrolls_and_cannot_start(client, raw_jpeg):
    sid, h = new_session(client)
    assert (
        client.post(
            f"{P}/sessions/{sid}/precheck", files={"image": ("a.jpg", raw_jpeg)}, headers=h
        ).status_code
        == 200
    )
    for _ in range(3):
        r = client.post(f"{P}/sessions/{sid}/verify", files={"image": ("a.jpg", raw_jpeg)}, headers=h)
        assert r.json()["result"] == "INCONCLUSIVE"
        assert r.json()["score"] is None
        assert r.json()["reason"] == "REFERENCE_PROFILE_MISSING"
    assert (
        client.post(f"{P}/sessions/{sid}/verify", files={"image": ("a.jpg", raw_jpeg)}, headers=h).status_code
        == 409
    )
    assert client.service.store.row("SELECT count(*) n FROM profiles")["n"] == 0
    assert client.post(f"{P}/sessions/{sid}/start", headers=headers(client, "backend")).status_code == 409
    assert client.post(f"{P}/sessions/{sid}/start", headers=h).status_code == 403


def test_verify_rejects_candidate_profile_replacement(client, raw_jpeg):
    sid, h = new_session(client)
    r = client.post(
        P + "/profiles/demo-candidate-a",
        headers=h,
        files={"image": ("f.jpg", raw_jpeg)},
        data={"source_ref": "verified-source", "expires_at": utc_add(utcnow(), 86400)},
    )
    assert r.status_code == 403


@pytest.mark.parametrize(
    "content,status,code",
    [
        (b"fake JPEG", 415, "UNSUPPORTED_IMAGE"),
        (b"\xff\xd8broken", 422, "IMAGE_UNDECODABLE"),
        (b"x" * 1048577, 413, "IMAGE_TOO_LARGE"),
    ],
)
def test_image_format_and_byte_limits(client, content, status, code):
    sid, h, fh, _ = monitoring(client)
    r = frame(client, sid, fh, content)
    assert r.status_code == status, r.text
    assert r.json()["error"]["code"] == code
    assert client.models.calls == 0


def test_pixel_limit_before_inference(client):
    sid, h, fh, _ = monitoring(client)
    raw = cv2.imencode(".jpg", np.zeros((1081, 1920, 3), np.uint8))[1].tobytes()
    r = frame(client, sid, fh, raw)
    assert r.status_code == 413 and client.models.calls == 0


def test_sequence_dedup_content_collision_and_stale(client, raw_jpeg):
    sid, h, fh, _ = monitoring(client)
    assert frame(client, sid, fh, raw_jpeg, 2, 10).status_code == 202
    duplicate = frame(client, sid, fh, raw_jpeg, 2, 10)
    assert duplicate.status_code == 200 and duplicate.json()["processing_status"] == "DUPLICATE"
    assert frame(client, sid, fh, raw_jpeg + b"x", 2, 10).status_code == 409
    assert frame(client, sid, fh, raw_jpeg, 1, 5).json()["error"]["code"] == "STALE_FRAME"
    assert frame(client, sid, fh, raw_jpeg, 3, 100000).json()["error"]["code"] == "INVALID_CAPTURE_TIME"
    assert frame(client, sid, fh, raw_jpeg, 3, -1).status_code == 400


def test_one_pending_frame_and_rate_control(client, raw_jpeg):
    sid, h, fh, _ = monitoring(client)
    for i in range(1, 6):
        r = frame(client, sid, fh, raw_jpeg, i, i)
        assert r.status_code == 202, r.text
        assert r.json()["replaced_pending_seq"] == (i - 1 if i > 1 else None)
    rt = client.service.runtimes[sid]
    assert rt.pending.seq == 5
    assert client.service.store.session(sid)["dropped_frames"] == 4
    r = frame(client, sid, fh, raw_jpeg, 6, 6)
    assert r.status_code == 429 and r.headers["retry-after"] == "1"
    assert len(client.service.ready_queue) == 1


def test_cross_session_tokens_expired_and_revoked(client, raw_jpeg):
    sid, h, fh, r = monitoring(client)
    other, hb = new_session(client, "candidate_b")
    assert client.get(f"{P}/sessions/{sid}", headers=hb).status_code == 404
    assert frame(client, other, fh, raw_jpeg).status_code == 403
    assert frame(client, sid, {"Authorization": "Bearer garbage"}, raw_jpeg).status_code == 401
    service = client.service
    expired = service.security.issue("demo-candidate-a", "candidate", sid, "frames", ttl=-1, generation=1)
    assert (
        frame(client, sid, {"Authorization": "Bearer " + expired}, raw_jpeg).json()["error"]["code"]
        == "TOKEN_EXPIRED"
    )
    admin = headers(client, "admin")
    assert (
        client.post(P + "/admin/tokens/revoke", headers=admin, json={"token": r["frames_token"]}).status_code
        == 200
    )
    assert frame(client, sid, fh, raw_jpeg).json()["error"]["code"] == "TOKEN_REVOKED"


def test_review_cas_idempotency_and_audit(client, raw_jpeg):
    sid, h = new_session(client)
    e = event(client, sid, raw_jpeg)
    a = headers(client, "proctor_a", "review-once")
    b = headers(client, "proctor_b", "second-review")
    body = {"status": "CONFIRMED_OBSERVATION", "reason": "Đã xem minh chứng", "expected_version": 1}
    first = client.patch(f"{P}/events/{e['event_id']}/review", headers=a, json=body)
    assert first.status_code == 200 and first.json()["version"] == 2
    assert client.patch(f"{P}/events/{e['event_id']}/review", headers=a, json=body).status_code == 200
    assert client.patch(f"{P}/events/{e['event_id']}/review", headers=b, json=body).status_code == 409
    assert client.service.store.row("SELECT count(*) n FROM reviews")["n"] == 1
    assert (
        client.get(f"{P}/events/{e['event_id']}/reviews", headers=a).json()["items"][0]["reason"]
        == body["reason"]
    )


def test_two_simultaneous_proctors_one_wins(client):
    sid, _ = new_session(client)
    e = event(client, sid)
    service = client.service
    actors = [service.security.decode(token(client, a)) for a in ("proctor_a", "proctor_b")]

    def update(i):
        try:
            service.review(e, actors[i], "DISMISSED", "Kiểm tra đồng thời", 1, "concurrent-" + str(i))
            return 200
        except Problem as problem:
            return problem.status

    with ThreadPoolExecutor(2) as pool:
        assert sorted(pool.map(update, [0, 1])) == [200, 409]
    assert service.store.row("SELECT count(*) n FROM reviews")["n"] == 1


def test_evidence_authorization_encryption_expiry_and_deletion(client, raw_jpeg):
    sid, _ = new_session(client)
    enroll = client.post(
        P + "/profiles/demo-candidate-a",
        headers=headers(client, "admin"),
        files={"image": ("reference.jpg", raw_jpeg)},
        data={"source_ref": "synthetic-expiry-test-reference", "expires_at": utc_add(utcnow(), 86400)},
    )
    assert enroll.status_code == 200
    assert client.service.store.row("SELECT count(*) n FROM profiles")["n"] == 1
    e = event(client, sid, raw_jpeg)
    eid = e["evidence_ids"][0]
    proctor = headers(client, "proctor_a")
    own_candidate = headers(client, "candidate_a")
    other = headers(client, "candidate_b")
    outsider = headers(client, "outsider")
    assert client.get(f"{P}/evidence/{eid}/access", headers=other).status_code == 404
    assert client.get(f"{P}/evidence/{eid}/access", headers=own_candidate).status_code == 404
    assert client.get(f"{P}/evidence/{eid}/access", headers=outsider).status_code == 404
    access = client.get(f"{P}/evidence/{eid}/access", headers=proctor).json()
    assert client.get(access["url"], headers=proctor).content == raw_jpeg
    assert client.get(access["url"]).status_code == 401
    expired = jwt.encode(
        {"eid": eid, "sub": "demo-proctor-a", "purpose": "evidence", "exp": time.time() - 1},
        client.service.security.key,
        algorithm="HS256",
    )
    assert client.get(f"{P}/evidence/{eid}/content?signature={expired}", headers=proctor).status_code == 403
    row = client.service.store.row("SELECT * FROM evidence WHERE id=?", (eid,))
    file = client.service.vault.root / row["object_key"]
    assert file.read_bytes() != raw_jpeg
    assert client.get("/runtime/private-evidence/" + row["object_key"]).status_code == 404
    assert client.service.vault.purge(utc_add(utcnow(), 31 * 86400)) == 1
    assert not file.exists()
    assert client.service.store.row("SELECT status FROM evidence WHERE id=?", (eid,))["status"] == "DELETED"
    assert client.service.store.row("SELECT count(*) n FROM profiles")["n"] == 0


def test_evidence_failure_keeps_event(client, raw_jpeg, monkeypatch):
    sid, _ = new_session(client)

    def failed(*args):
        raise OSError("disk full")

    monkeypatch.setattr("proctor.evidence.os.replace", failed)
    e = event(client, sid, raw_jpeg)
    assert e["evidence_status"] == "MISSING"
    row = client.service.store.row("SELECT * FROM evidence WHERE event_id=?", (e["event_id"],))
    assert row["object_key"] is None and row["reason"] == "EVIDENCE_WRITE_FAILED"
    assert client.service.store.row("SELECT count(*) n FROM outbox")["n"] >= 1


def test_outbox_disconnect_retry_and_replay(client):
    sid, _ = new_session(client)
    event(client, sid)

    class Broken:
        async def send_text(self, payload):
            raise ConnectionError()

    service = client.service
    service.subscribers[sid] = {Broken()}
    client.portal.call(service.dispatch_once)
    row = service.store.row("SELECT * FROM outbox ORDER BY id LIMIT 1")
    assert row["sent_at"] is None and row["attempts"] == 1
    proctor = headers(client, "proctor_a")
    replay = client.get(f"{P}/sessions/{sid}/notifications", headers=proctor).json()["items"]
    assert replay[0]["message_id"] == row["message_id"]

    class Good:
        def __init__(self):
            self.messages = []

        async def send_text(self, payload):
            self.messages.append(json.loads(payload))

    good = Good()
    service.subscribers[sid] = {good}
    service.store.execute("UPDATE outbox SET next_attempt=0")
    client.portal.call(service.dispatch_once)
    assert service.store.row("SELECT sent_at FROM outbox ORDER BY id LIMIT 1")["sent_at"]
    assert good.messages[0]["message_id"] == row["message_id"]


def test_websocket_scope_and_no_url_token(client):
    sid, _ = new_session(client)
    with client.websocket_connect(f"{P}/sessions/{sid}/ws") as ws:
        ws.send_json({"token": token(client, "outsider")})
        with pytest.raises(WebSocketDisconnect):
            ws.receive_json()
    with client.websocket_connect(f"{P}/sessions/{sid}/ws") as ws:
        ws.send_json({"token": token(client, "proctor_a")})
        assert ws.receive_json()["type"] == "subscribed"


def test_end_repeat_closes_event_and_rejects_frame(client, raw_jpeg):
    sid, h, fh, _ = monitoring(client)
    e = event(client, sid, raw_jpeg)
    # This event is persisted directly to test recovery of DB-open events too.
    client.service.runtimes[sid].engine.tracks["PHONE_VISIBLE"].event = EngineEvent(
        e["event_id"], "PHONE_VISIBLE", 0, 1, e["start_at"], e["confirmed_at"], 1, e["last_observed_at"]
    )
    client.service.runtimes[sid].engine.tracks["PHONE_VISIBLE"].state = "ACTIVE"
    end_h = headers(client, "backend", "end-once")
    body = {"reason": "SESSION_ENDED"}
    first = client.post(f"{P}/sessions/{sid}/end", headers=end_h, json=body)
    again = client.post(f"{P}/sessions/{sid}/end", headers=end_h, json=body)
    assert first.status_code == again.status_code == 200
    assert first.json()["ended_at"] == again.json()["ended_at"]
    assert frame(client, sid, fh, raw_jpeg).json()["error"]["code"] == "SESSION_ENDED"
    assert client.service.store.event(e["event_id"])["close_reason"] == "SESSION_ENDED"


def test_network_loss_is_technical_and_resume_resets_stream(client, raw_jpeg):
    sid, h, fh, start = monitoring(client)
    service = client.service
    rt = service.runtimes[sid]
    assert frame(client, sid, fh, raw_jpeg).status_code == 202
    client.portal.call(service.process_one, sid)
    client.portal.call(service.heartbeat, rt.last_received + 5)
    assert service.store.session(sid)["resume_required"]
    types = [r["type"] for r in service.store.rows("SELECT type FROM events")]
    assert "CAMERA_INTERRUPTED" in types and "FACE_NOT_VISIBLE" not in types
    r = client.post(
        f"{P}/sessions/{sid}/resume",
        headers=h,
        json={"old_token": start["frames_token"], "stream_id": str(uuid4())},
    )
    assert r.status_code == 200, r.text
    assert frame(client, sid, fh, raw_jpeg, 2, 1).status_code == 401
    fresh = {"Authorization": "Bearer " + r.json()["frames_token"]}
    assert frame(client, sid, fresh, raw_jpeg, 1, 0).status_code == 202


def test_object_failure_unknown_and_independent_health(client, raw_jpeg):
    sid, h, fh, _ = monitoring(client)
    client.models.object_error = True
    frame(client, sid, fh, raw_jpeg)
    client.portal.call(client.service.process_one, sid)
    s = client.service.store.session(sid)
    assert s["state"] == "DEGRADED"
    assert s["latest_observation"]["signals"]["PHONE_VISIBLE"] == "UNKNOWN"
    assert s["health"]["face_detection"]["status"] == "AVAILABLE"


def test_config_snapshot_remains_frozen(client):
    sid, _ = new_session(client)
    old = client.service.store.session(sid)["config_hash"]
    cfg = Config(version="cfg-two", yaw_open=30).model_dump()
    r = client.post(
        P + "/configs",
        headers=headers(client, "admin"),
        json={"config": cfg, "reason": "Điều chỉnh trên tập phát triển"},
    )
    assert r.status_code == 201, r.text
    assert client.service.store.session(sid)["config_hash"] == old
    second, _ = new_session(client, "candidate_b")
    assert client.service.store.session(second)["config_version"] == "cfg-two"
    cfg["yaw_open"] = 32
    assert (
        client.post(
            P + "/configs",
            headers=headers(client, "admin"),
            json={"config": cfg, "reason": "Sửa đè cùng phiên bản"},
        ).status_code
        == 409
    )


def test_demo_host_origin_and_safe_error_envelope(client):
    assert client.get("/demo/info", headers={"Host": "attacker.example"}).status_code == 403
    assert (
        client.post(
            "/demo/login", headers={"Origin": "https://attacker.example"}, json={"account": "admin"}
        ).status_code
        == 403
    )
    r = client.post(
        P + "/sessions", headers=headers(client), json={"attempt_id": "not-uuid", "secret": "DO_NOT_ECHO"}
    )
    assert r.status_code == 400 and "DO_NOT_ECHO" not in r.text
    assert "request_id" in r.json() and "X-Request-ID" in r.headers


def test_resume_reused_stream_id_is_rejected_without_side_effect(client):
    sid, h, fh, started = monitoring(client)
    before = client.service.store.session(sid)["generation"]
    r = client.post(
        f"{P}/sessions/{sid}/resume",
        headers=h,
        json={"old_token": started["frames_token"], "stream_id": started["stream_id"]},
    )
    assert r.status_code == 409 and r.json()["error"]["code"] == "STREAM_REPLAY"
    assert client.service.store.session(sid)["generation"] == before


def test_camera_change_requires_recalibration(client, raw_jpeg):
    sid, h, fh, _ = monitoring(client)
    r = client.post(
        f"{P}/sessions/{sid}/frames",
        headers=fh,
        files={"image": ("x.jpg", raw_jpeg)},
        data={"seq": 1, "capture_offset_ms": 0, "camera_id": "changed-device"},
    )
    assert r.status_code == 409 and r.json()["error"]["code"] == "RECALIBRATION_REQUIRED"
    s = client.service.store.session(sid)
    assert s["state"] == "CALIBRATING" and s["baseline"] is None


def test_calibration_missing_eyes_cannot_pass(client, raw_jpeg, monkeypatch):
    sid, h = new_session(client)
    s = client.service.store.session(sid)
    s.update(state="CALIBRATING", verified=True, calibration_id=uid())
    with client.service.store.transaction() as db:
        client.service.store.save_session(s, db)
    original = client.models.inspect

    def no_eyes(*args):
        result = original(*args)
        result["pose"]["eyes_ok"] = False
        return result

    monkeypatch.setattr(client.models, "inspect", no_eyes)
    r = client.post(
        f"{P}/sessions/{sid}/calibration/samples",
        headers=h,
        files={"image": ("x.jpg", raw_jpeg)},
        data={"seq": 1, "capture_offset_ms": 0},
    )
    assert r.status_code == 202 and not r.json()["accepted"]
    complete = client.post(
        f"{P}/sessions/{sid}/calibration/complete", headers=h, json={"calibration_id": s["calibration_id"]}
    )
    assert complete.json()["status"] == "RETRY_REQUIRED"


def test_end_with_new_key_still_does_not_duplicate_notification(client):
    sid, h, fh, _ = monitoring(client)
    for key in ("first", "second"):
        r = client.post(
            f"{P}/sessions/{sid}/end",
            headers=headers(client, "backend", key),
            json={"reason": "SESSION_ENDED"},
        )
        assert r.status_code == 200
    assert (
        client.service.store.row("SELECT count(*) n FROM outbox WHERE event_type='proctor.session.ended'")[
            "n"
        ]
        == 1
    )
