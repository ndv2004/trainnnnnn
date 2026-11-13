"""Runtime boundary tests use explicit fake model results and a fake clock."""
from __future__ import annotations

import numpy as np
import pytest

from camera_app.camera import Frame
from camera_app.config import load_config
from camera_app.events import EventEngine
from camera_app.identity import Profile, normalize
from camera_app.pose import HeadGazeCalibrator, PoseEstimator
from camera_app.runtime import CameraRuntime
from camera_app.tracking import FaceTracker


def vector(index=0):
    result = np.zeros(128, np.float32)
    result[index] = 1
    return result


class MemoryStore:
    def __init__(self):
        self.values = []
        self.main_profile_id = None

    def profiles(self):
        return list(self.values)

    def add(self, name, embedding, model_version, make_main=False):
        profile = Profile(str(len(self.values)), name, embedding, model_version, "test-time")
        self.values.append(profile)
        if make_main:
            self.main_profile_id = profile.id
        return profile

    def set_main(self, key):
        self.main_profile_id = key


class FakeVision:
    model_version = "synthetic-test-model"
    recognizer = face = object()
    landmarker = None

    def __init__(self):
        self.vectors = []
        self.health = {"face_detection": None, "face_verification": None,
                       "head_pose": "TEST_UNAVAILABLE", "object_detection": None}

    def detect_faces(self, frame):
        rows = np.zeros((len(self.vectors), 15), np.float32)
        for index in range(len(rows)):
            rows[index, :4] = [20 + index * 150, 20, 110, 110]
            rows[index, 14] = .99
        return rows

    def embed(self, frame, row):
        return self.vectors[round((row[0] - 20) / 150)]

    def landmarks(self, frame):
        return None

    def detect_objects(self, frame):
        return []


@pytest.fixture
def harness(monkeypatch, tmp_path):
    cfg = load_config()
    runtime = CameraRuntime(cfg, tmp_path, tmp_path)
    runtime.store = MemoryStore()
    runtime.vision = FakeVision()
    runtime.engine = EventEngine(cfg)
    runtime.calibrator = HeadGazeCalibrator(cfg)
    runtime.pose_estimator = PoseEstimator(cfg)
    runtime.tracker = FaceTracker(cfg["recognition_threshold"], cfg["recognition_margin"])
    clock = [10.0]
    monkeypatch.setattr("camera_app.runtime.time.monotonic", lambda: clock[0])
    monkeypatch.setattr("camera_app.runtime.image_quality", lambda *args: {"ok": True, "reason": ""})
    monkeypatch.setattr("camera_app.runtime.face_quality", lambda *args: {"ok": True, "reason": ""})

    def step(vectors, fresh=True):
        clock[0] += .4
        runtime.vision.vectors = vectors
        packet = Frame(round(clock[0] * 10), clock[0], np.zeros((240, 400, 3), np.uint8))
        runtime.source.latest = packet if fresh else None
        runtime.source.error = None if fresh else "TEST_CAMERA_LOST"
        runtime._process(packet)
        return runtime.snapshot()

    return runtime, step


def test_registered_main_absent_while_other_visible_and_ambiguity_is_unknown(harness):
    runtime, step = harness
    a = runtime.store.add("A", vector(), runtime.vision.model_version, True)
    runtime.store.add("B", vector(1), runtime.vision.model_version)
    runtime.tracker.select_main(a.id)
    step([vector(), vector(1)])
    state = step([vector(), vector(1)])
    assert state["main_visible"]
    assert state["signals"]["FACE_NOT_VISIBLE"] == "NEGATIVE"
    state = step([vector(1)])
    assert not state["main_visible"]
    assert state["signals"]["FACE_NOT_VISIBLE"] == "POSITIVE"
    runtime.store.add("Duplicate A", vector(), runtime.vision.model_version)
    state = step([vector()])
    assert state["signals"]["FACE_NOT_VISIBLE"] == "UNKNOWN"


def test_anonymous_main_never_moves_to_new_face_at_old_position(harness):
    runtime, step = harness
    step([vector()])
    assert step([vector()])["main_visible"]
    state = step([vector(1)])
    assert not state["main_visible"]
    assert state["faces"][0]["name"] == "Người"
    assert state["signals"]["FACE_NOT_VISIBLE"] == "POSITIVE"


def test_cancel_discards_all_samples_save_is_explicit_and_acknowledged(harness):
    runtime, step = harness
    for cancel in (True, False):
        runtime.command("enroll_open")
        runtime.command("enroll_start", name="Nguyễn Duy Việt")
        runtime._commands()
        for index in range(15):
            sample = normalize(vector() + .13 * vector(index + 1))
            state = step([sample])
            assert state["events"]["suspended"]
            assert state["profiles"] == []
            assert state["saved_id"] == 0
        assert state["enrollment"]["ready"]
        if cancel:
            runtime.command("enroll_cancel")
            runtime._commands()
            assert runtime.collector is None and runtime.enrollment is None
            assert runtime.store.profiles() == []
        else:
            runtime.command("enroll_save", make_main=True)
            runtime._commands()
            state = step([vector()])
            assert state["saved_id"] == 1
            assert state["profiles"][0]["name"] == "Nguyễn Duy Việt"
            assert state["main_profile_id"] == state["profiles"][0]["id"]
            assert runtime.enrollment is None


def test_explicit_calibration_suspends_without_face_and_camera_loss_adds_no_time(harness):
    runtime, step = harness
    runtime.vision.landmarker = object()
    runtime.vision.health["head_pose"] = None
    runtime.command("calibrate")
    runtime._commands()
    for _ in range(12):
        state = step([])
        assert state["events"]["suspended"]
        assert not state["events"]["alerts"]
    runtime.explicit_calibration = False
    for _ in range(12):
        state = step([], fresh=False)
    assert state["faces"] == [] and state["objects"] == []
    assert state["at"] is None
    assert not state["events"]["alerts"]
    assert runtime.engine.tracks["FACE_NOT_VISIBLE"].duration == 0
    assert runtime.engine.tracks["HEAD_AWAY"].duration == 0
