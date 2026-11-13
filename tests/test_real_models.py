"""Real CPU model smoke tests. These are not a labeled acceptance dataset or FAR/FRR estimate."""

import json
import time

import cv2
import numpy as np
import pytest
from conftest import headers, new_session
from fastapi.testclient import TestClient

from proctor.api import create_app
from proctor.config import ROOT, Config, Settings
from proctor.domain import utc_add, utcnow
from proctor.models import ModelSuite

P = "/api/v1/proctoring"


def require_assets():
    if not (ROOT / "test_data/portrait.jpg").exists() or not (ROOT / "models/face_landmarker.task").exists():
        pytest.skip("Run scripts/download_models.py and scripts/prepare_smoke_assets.py first")


def image():
    original = cv2.imread(str(ROOT / "test_data/portrait.jpg"))
    h, w = original.shape[:2]
    scale = min(640 / w, 480 / h)
    canvas = np.zeros((480, 640, 3), np.uint8)
    small = cv2.resize(original, (round(w * scale), round(h * scale)))
    x, y = (640 - small.shape[1]) // 2, (480 - small.shape[0]) // 2
    canvas[y : y + small.shape[0], x : x + small.shape[1]] = small
    return canvas


@pytest.mark.real
def test_true_models_face_pose_person_and_sface_pair():
    require_assets()
    suite = ModelSuite(ROOT / "models")
    try:
        assert all(suite.health[m]["status"] == "AVAILABLE" for m in Config().required_modules)
        frame = image()
        now = time.monotonic()
        o, inspected = suite.observe(frame, Config(), now, utcnow(), 1)
        assert inspected["quality"]["face_ok"]
        assert inspected["pose"] is not None
        assert o.data["objects"] is not None
        assert any(x["label"] == "person" for x in o.data["objects"])
        a = suite.embedding(frame, inspected["faces"])
        b = suite.embedding(frame.copy(), inspected["faces"])
        assert a.shape == (128,) and np.dot(a, b) > 0.99
        lena = cv2.imread(str(ROOT / "test_data/lena.jpg"))
        other = suite.inspect(lena, Config(), False)
        c = suite.embedding(lena, other["faces"])
        assert c is not None and np.dot(a, c) < Config().verification_cosine_threshold
        assert o.data["signals"]["GAZE_AWAY"] == "UNKNOWN"
    finally:
        suite.close()


@pytest.mark.real
def test_real_e2e_api_reference_calibration_events_evidence_review(tmp_path):
    require_assets()
    settings = Settings(tmp_path)
    settings.config_path = ROOT / "config/cpu-v1.json"
    face = image()
    raw = cv2.imencode(".jpg", face)[1].tobytes()
    models = ModelSuite(ROOT / "models")
    app = create_app(settings, models, start_tasks=True)
    with TestClient(app) as client:
        client.service = app.state.service
        sid, h = new_session(client)
        admin = headers(client, "admin")
        enroll = client.post(
            P + "/profiles/demo-candidate-a",
            headers=admin,
            files={"image": ("reference.jpg", raw)},
            data={
                "source_ref": "public-mediapipe-smoke-only-fixture",
                "expires_at": utc_add(utcnow(), 86400),
            },
        )
        assert enroll.status_code == 200, enroll.text
        dark = cv2.imencode(".jpg", np.zeros((480, 640, 3), np.uint8))[1].tobytes()
        bad = client.post(f"{P}/sessions/{sid}/precheck", headers=h, files={"image": ("bad.jpg", dark)})
        assert bad.status_code == 200 and not bad.json()["quality"]["face_ok"]
        assert "BLURRY" in bad.json()["quality"]["reasons"]
        assert (
            client.post(
                f"{P}/sessions/{sid}/verify", headers=h, files={"image": ("bad.jpg", dark)}
            ).status_code
            == 409
        )
        r = client.post(f"{P}/sessions/{sid}/precheck", headers=h, files={"image": ("f.jpg", raw)})
        assert r.json()["quality"]["face_ok"], r.text
        r = client.post(f"{P}/sessions/{sid}/verify", headers=h, files={"image": ("f.jpg", raw)})
        assert r.json()["result"] == "MATCH", r.text
        cid = r.json()["calibration_id"]
        start = time.monotonic()
        for seq in range(12):
            r = client.post(
                f"{P}/sessions/{sid}/calibration/samples",
                headers=h,
                files={"image": ("f.jpg", raw)},
                data={"seq": seq, "capture_offset_ms": round((time.monotonic() - start) * 1000)},
            )
            assert r.status_code == 202 and r.json()["accepted"], r.text
            time.sleep(0.205)
        r = client.post(f"{P}/sessions/{sid}/calibration/complete", headers=h, json={"calibration_id": cid})
        assert r.json()["status"] == "PASSED", r.text
        backend = headers(client, "backend", "real-e2e-end")
        r = client.post(f"{P}/sessions/{sid}/start", headers=backend)
        assert r.status_code == 200, r.text
        fh = {"Authorization": "Bearer " + r.json()["frames_token"]}
        noise = np.random.default_rng(7).integers(50, 210, (480, 640, 3), np.uint8)
        absence = cv2.imencode(".jpg", noise)[1].tobytes()
        start = time.monotonic()
        for seq in range(21):
            r = client.post(
                f"{P}/sessions/{sid}/frames",
                headers=fh,
                files={"image": ("scene.jpg", absence)},
                data={"seq": seq, "capture_offset_ms": round((time.monotonic() - start) * 1000)},
            )
            assert r.status_code == 202, r.text
            time.sleep(0.215)
        time.sleep(0.15)
        proctor = headers(client, "proctor_a", "real-e2e-review")
        history = client.get(f"{P}/sessions/{sid}/verification-history", headers=proctor).json()
        assert len(history["prechecks"]) == 2 and len(history["attempts"]) == 1
        assert history["prechecks"][0]["quality"]["face_ok"] is False
        r = client.get(f"{P}/sessions/{sid}/events", headers=proctor)
        face_events = [e for e in r.json()["items"] if e["type"] == "FACE_NOT_VISIBLE"]
        assert len(face_events) == 1, r.text
        e = face_events[0]
        assert e["evidence_status"] == "READY"
        access = client.get(f"{P}/evidence/{e['evidence_ids'][0]}/access", headers=proctor).json()
        assert client.get(access["url"], headers=proctor).headers["content-type"] == "image/jpeg"
        end = client.post(f"{P}/sessions/{sid}/end", headers=backend, json={"reason": "E2E_COMPLETED"})
        assert end.status_code == 200, end.text
        e = client.get(f"{P}/sessions/{sid}/events", headers=proctor).json()["items"][0]
        r = client.patch(
            f"{P}/events/{e['event_id']}/review",
            headers=proctor,
            json={
                "status": "CONFIRMED_OBSERVATION",
                "reason": "Đã kiểm tra ảnh minh chứng trong smoke test",
                "expected_version": e["version"],
            },
        )
        assert r.status_code == 200, r.text
        assert (
            client.post(
                f"{P}/sessions/{sid}/frames",
                headers=fh,
                files={"image": ("x.jpg", raw)},
                data={"seq": 99, "capture_offset_ms": 9000},
            ).status_code
            == 409
        )
        report = {
            "scope": "functional smoke, not model quality acceptance",
            "real_models": models.versions,
            "actual_event_types": [x["type"] for x in face_events],
            "evidence": "encrypted and read through authorized API",
            "identity": "same public reference image reused; not FAR/FRR",
            "calibration": "12 real MediaPipe samples",
            "host": "Linux container CPU, not the target Windows laptop",
        }
        (ROOT / "reports/real-e2e.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
