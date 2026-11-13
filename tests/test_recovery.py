import time
from uuid import uuid4

from conftest import FakeModels, headers, monitoring
from fastapi.testclient import TestClient

from proctor.api import create_app
from proctor.config import Config, Settings
from proctor.domain import Change, EngineEvent, uid, utcnow
from proctor.process_guard import ProcessGuard

P = "/api/v1/proctoring"


def test_restart_preserves_events_outbox_keys_and_requires_resume(tmp_path, raw_jpeg):
    settings = Settings(tmp_path)
    settings.config_path = tmp_path / "cfg.json"
    settings.config_path.write_text(Config().model_dump_json())
    with TestClient(create_app(settings, FakeModels(), start_tasks=False)) as first:
        first.service = first.app.state.service
        sid, h, fh, started = monitoring(first)
        stamp = utcnow()
        eid = uid()
        first.service.persist_changes(
            first.service.store.session(sid),
            [Change("create", EngineEvent(eid, "PHONE_VISIBLE", 0, 1, stamp, stamp, 1, stamp))],
            raw_jpeg,
        )
        original = first.service.store.row("SELECT message_id FROM outbox ORDER BY id LIMIT 1")["message_id"]
        # Shutdown leaves active events persisted; recovery, rather than elapsed downtime, closes them.
    with TestClient(create_app(settings, FakeModels(), start_tasks=False)) as second:
        second.service = second.app.state.service
        service = second.service
        e = service.store.event(eid)
        assert e["close_reason"] == "WORKER_INTERRUPTED"
        assert e["end_at"] == stamp
        assert service.store.session(sid)["resume_required"]
        r = second.post(
            f"{P}/sessions/{sid}/frames",
            headers=fh,
            files={"image": ("x.jpg", raw_jpeg)},
            data={"seq": 1, "capture_offset_ms": 0},
        )
        assert r.status_code == 409 and r.json()["error"]["code"] == "RESUME_REQUIRED"
        replay = second.get(f"{P}/sessions/{sid}/notifications", headers=headers(second, "proctor_a")).json()
        assert any(x["message_id"] == original for x in replay["items"])
        old = service.security.decode(started["frames_token"])
        assert old["session_id"] == sid  # same persisted signing key after restart
        resume = second.post(
            f"{P}/sessions/{sid}/resume",
            headers=headers(second),
            json={"old_token": started["frames_token"], "stream_id": str(uuid4())},
        )
        assert resume.status_code == 200, resume.text
        assert service.runtimes[sid].engine.last_at == -1


def test_single_process_guard_is_explicit(tmp_path):
    guard = ProcessGuard(tmp_path / "worker.lock")
    try:
        try:
            ProcessGuard(tmp_path / "worker.lock")
            raise AssertionError("Second worker was accepted")
        except RuntimeError as exc:
            assert "DATA_DIR_IN_USE" in str(exc)
    finally:
        guard.close()


def test_readiness_missing_and_bad_checksum(tmp_path):
    from proctor.models import ModelSuite

    suite = ModelSuite(tmp_path)
    assert all(
        suite.health[m]["error_code"] == "WEIGHTS_MISSING"
        for m in ("face_detection", "object_detection", "face_verification", "head_pose")
    )
    import json

    (tmp_path / "fake.onnx").write_bytes(b"not-a-model")
    (tmp_path / "manifest.json").write_text(
        json.dumps({"models": [{"module": "face_detection", "file": "fake.onnx", "sha256": "0" * 64}]})
    )
    suite = ModelSuite(tmp_path)
    assert suite.health["face_detection"]["error_code"] == "CHECKSUM_MISMATCH"
    settings = Settings(tmp_path / "data")
    settings.config_path = tmp_path / "none.json"
    with TestClient(create_app(settings, suite, start_tasks=False)) as client:
        assert client.get("/health/live").status_code == 200
        assert client.get("/health/ready").status_code == 503


def test_stale_queued_result_never_advances_behavior(client, raw_jpeg):
    from dataclasses import replace

    sid, h, fh, _ = monitoring(client)
    r = client.post(
        f"{P}/sessions/{sid}/frames",
        headers=fh,
        files={"image": ("x.jpg", raw_jpeg)},
        data={"seq": 1, "capture_offset_ms": 0},
    )
    assert r.status_code == 202
    rt = client.service.runtimes[sid]
    rt.pending = replace(rt.pending, at=time.monotonic() - 2)
    client.portal.call(client.service.process_one, sid)
    assert client.models.calls == 0
    types = [r["type"] for r in client.service.store.rows("SELECT type FROM events")]
    assert types == ["PROCESSING_GAP"]


def test_manual_decision_retry_is_audited_with_reason(client, raw_jpeg):
    from conftest import new_session

    sid, h = new_session(client)
    client.post(f"{P}/sessions/{sid}/precheck", headers=h, files={"image": ("x.jpg", raw_jpeg)})
    body = {"decision": "ACCEPT_MANUALLY", "reason": "Đã đối soát trực tiếp tại chỗ", "expected_version": 1}
    proctor = headers(client, "proctor_a", "manual-once")
    r = client.post(f"{P}/sessions/{sid}/manual-decision", headers=proctor, json=body)
    assert r.status_code == 200, r.text
    assert r.json()["manual_override"]["reason"] == body["reason"]
    assert client.service.store.row("SELECT count(*) n FROM manual_decisions")["n"] == 1
    assert client.service.store.row("SELECT count(*) n FROM verifications")["n"] == 0


def test_liveness_is_explicitly_unavailable(client):
    from conftest import new_session

    sid, h = new_session(client)
    r = client.post(f"{P}/sessions/{sid}/liveness/challenge", headers=h)
    assert r.status_code == 503 and r.json()["error"]["code"] == "LIVENESS_UNAVAILABLE"
    assert client.get(P + "/capabilities", headers=h).json()["liveness"]["status"] == "UNAVAILABLE"


def test_chunked_request_body_limit(client):
    def chunks():
        yield b"a" * 700000
        yield b"b" * 700000

    r = client.post("/demo/login", content=chunks(), headers={"Content-Type": "application/json"})
    assert r.status_code == 413


def test_audit_and_error_logs_do_not_contain_secrets(client, raw_jpeg, caplog):
    sid, h, fh, started = monitoring(client)
    client.post(
        f"{P}/sessions/{sid}/frames",
        headers=fh,
        files={"image": ("private.jpg", raw_jpeg)},
        data={"seq": 1, "capture_offset_ms": 0},
    )
    audit = str(client.service.store.rows("SELECT * FROM audit"))
    assert started["frames_token"] not in audit + caplog.text
    assert "private.jpg" not in audit + caplog.text
    assert "embedding" not in audit + caplog.text


def test_native_process_crash_recovers_committed_event_and_outbox(tmp_path):
    import json
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    code = """
import os,json,sys
from pathlib import Path
from proctor.service import Service
from proctor.config import Settings
from proctor.domain import Change,EngineEvent,uid,utcnow
sys.path.insert(0,str(Path.cwd()/"tests"))
from conftest import FakeModels
settings=Settings(Path(sys.argv[1]));settings.config_path=Path(sys.argv[1])/"none.json"
s=Service(settings,FakeModels())
actor=s.security.decode(s.security.issue("demo-backend","service"))
created,_=s.create(actor,"10000000-0000-4000-8000-000000000001","crash-test")
session=s.store.session(created["session_id"])
session.update(state="MONITORING",started_at=utcnow())
with s.store.transaction() as db:s.store.save_session(session,db)
stamp=utcnow();eid=uid()
s.persist_changes(session,[Change("create",EngineEvent(eid,"PHONE_VISIBLE",0,1,stamp,stamp,1,stamp))],None)
Path(sys.argv[1],"crash-meta.json").write_text(json.dumps({"sid":session["session_id"],"eid":eid,"stamp":stamp}))
os._exit(23)
"""
    completed = subprocess.run(
        [sys.executable, "-c", code, str(tmp_path)], cwd=root, capture_output=True, text=True
    )
    assert completed.returncode == 23, completed.stderr
    meta = json.loads((tmp_path / "crash-meta.json").read_text())
    settings = Settings(tmp_path)
    settings.config_path = tmp_path / "none.json"
    with TestClient(create_app(settings, FakeModels(), start_tasks=False)) as restarted:
        service = restarted.app.state.service
        event = service.store.event(meta["eid"])
        assert event["close_reason"] == "WORKER_INTERRUPTED"
        assert event["end_at"] == meta["stamp"]
        assert service.store.row("SELECT count(*) n FROM outbox WHERE session_id=?", (meta["sid"],))["n"] >= 2
        assert service.store.session(meta["sid"])["resume_required"]


def test_worker_supervises_model_exception_and_recovers(client, raw_jpeg, monkeypatch):
    sid, h, fh, _ = monitoring(client)
    original = client.models.observe
    counter = [0]

    def crash_once(*args):
        counter[0] += 1
        if counter[0] == 1:
            raise RuntimeError("model failure")
        return original(*args)

    monkeypatch.setattr(client.models, "observe", crash_once)
    client.portal.call(client.service.start)
    rt = client.service.runtimes[sid]
    for seq in (1, 2):
        client.post(
            f"{P}/sessions/{sid}/frames",
            headers=fh,
            files={"image": ("x.jpg", raw_jpeg)},
            data={"seq": seq, "capture_offset_ms": round((time.monotonic() - rt.origin) * 1000)},
        )
        for _ in range(100):
            if counter[0] >= seq:
                break
            time.sleep(0.01)
        time.sleep(0.03)
        if seq == 1:
            assert client.service.store.session(sid)["health"]["pipeline"]["error_code"] == "PIPELINE_FAILED"
    assert client.service.store.session(sid)["state"] == "MONITORING"
    assert client.service.store.rows("SELECT type FROM events WHERE type='PROCESSING_GAP'")


def test_active_config_survives_restart(tmp_path):
    settings = Settings(tmp_path)
    settings.config_path = tmp_path / "none.json"
    with TestClient(create_app(settings, FakeModels(), start_tasks=False)) as client:
        cfg = Config(version="persisted-config", yaw_open=30).model_dump()
        r = client.post(
            P + "/configs",
            headers=headers(client, "admin"),
            json={"config": cfg, "reason": "Khóa cấu hình đã điều chỉnh"},
        )
        assert r.status_code == 201
    with TestClient(create_app(settings, FakeModels(), start_tasks=False)) as restarted:
        assert restarted.app.state.service.cfg.version == "persisted-config"
