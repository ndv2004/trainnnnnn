"""Logic fixtures are deliberately synthetic. They are NOT model-accuracy evidence."""

from copy import deepcopy

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from proctor.api import create_app
from proctor.config import Config, Settings
from proctor.domain import Observation, Signal, Verdict


class FakeModels:
    def __init__(self):
        self.health = {
            k: {"status": "AVAILABLE", "error_code": None}
            for k in ("face_detection", "object_detection", "face_verification", "head_pose")
        }
        self.health.update(gaze={"status": "DISABLED"}, liveness={"status": "DISABLED"})
        self.versions = {k: {"name": "TEST_ONLY", "sha256": "test-only-model"} for k in self.health}
        self.result = "one_face"
        self.object_error = False
        self.signal_overrides = {}
        self.calls = 0
        self.vector = np.ones(128, np.float32) / np.sqrt(128)

    def inspect(self, frame, cfg, landmarks=True):
        faces = np.zeros((1, 15), np.float32)
        faces[0, :4] = [100, 100, 200, 200]
        faces[0, 14] = 0.99
        if self.result == "no_face":
            faces = np.empty((0, 15), np.float32)
        ok = self.result == "one_face"
        return {
            "faces": faces,
            "quality": {"image_ok": True, "face_ok": ok, "face_count": len(faces), "reasons": []},
            "pose": {
                "yaw": 0,
                "pitch": 0,
                "roll": 0,
                "eyes_ok": True,
                "iris_x": 0.5,
                "face_center": [0.5, 0.5],
            },
        }

    def embedding(self, frame, faces):
        return self.vector.copy()

    def observe(self, frame, cfg, at, utc, seq, baseline=None, run_objects=True):
        self.calls += 1
        inspected = self.inspect(frame, cfg)
        signals = {k: Signal(self.signal_overrides.get(k, Verdict.NEGATIVE), at, at + 1.5) for k in cfg.rules}
        if self.object_error:
            self.health["object_detection"] = {
                "status": "UNAVAILABLE",
                "error_code": "MODEL_INFERENCE_FAILED",
            }
            for k in ("MULTIPLE_PEOPLE", "PHONE_VISIBLE"):
                signals[k] = Signal(Verdict.UNKNOWN, at, at, "MODEL_UNAVAILABLE")
        return Observation(
            at,
            utc,
            seq,
            signals,
            data={
                "quality": inspected["quality"],
                "modules": deepcopy(self.health),
                "objects": None if self.object_error else [],
                "face_boxes": [],
                "signals": {k: v.value.value for k, v in signals.items()},
            },
        ), inspected

    def close(self):
        pass


@pytest.fixture
def raw_jpeg():
    rng = np.random.default_rng(123)
    image = rng.integers(60, 200, (480, 640, 3), np.uint8)
    return cv2.imencode(".jpg", image)[1].tobytes()


@pytest.fixture
def client(tmp_path):
    models = FakeModels()
    settings = Settings(tmp_path)
    settings.config_path = tmp_path / "config.json"
    settings.config_path.write_text(
        Config(calibration_samples=3, calibration_duration_ms=1000).model_dump_json()
    )
    app = create_app(settings, models, start_tasks=False)
    with TestClient(app) as c:
        c.models = models
        c.service = app.state.service
        yield c


def token(client, account):
    return client.post("/demo/login", json={"account": account}).json()["access_token"]


def headers(client, account="candidate_a", key=None):
    h = {"Authorization": "Bearer " + token(client, account)}
    if key:
        h["Idempotency-Key"] = key
    return h


def new_session(client, account="candidate_a"):
    attempt = client.get("/demo/info").json()["attempts"][account]
    h = headers(client, account, "create-" + account)
    response = client.post("/api/v1/proctoring/sessions", json={"attempt_id": attempt}, headers=h)
    assert response.status_code in (200, 201), response.text
    sid = response.json()["session_id"]
    client.post(f"/api/v1/proctoring/sessions/{sid}/consent", json={"accepted": True}, headers=h)
    return sid, h


def monitoring(client):
    sid, h = new_session(client)
    s = client.service.store.session(sid)
    s.update(
        state="READY",
        verified=True,
        baseline={
            "width": 640,
            "height": 480,
            "camera_id": "default",
            "yaw": 0,
            "pitch": 0,
            "iris_x": 0.5,
            "eyes_ok": True,
        },
    )
    with client.service.store.transaction() as db:
        client.service.store.save_session(s, db)
    r = client.post(f"/api/v1/proctoring/sessions/{sid}/start", headers=headers(client, "backend"))
    assert r.status_code == 200, r.text
    fh = {"Authorization": "Bearer " + r.json()["frames_token"]}
    return sid, h, fh, r.json()
