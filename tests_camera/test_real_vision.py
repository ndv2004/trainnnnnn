"""Real CPU adapter smoke tests, NOT an accuracy or anti-spoofing benchmark.

Portrait transforms are a deterministic integration fixture. They do not replace
live multi-person, phone, registration, or gaze acceptance tests.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import cv2
import numpy as np
import pytest

from camera_app.config import load_config
from camera_app.identity import EnrollmentCollector, IdentityStore, recognize
from camera_app.pose import PoseEstimator
from camera_app.tracking import FaceDetection, FaceTracker
from camera_app.vision import CameraVision, face_quality, image_quality

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.real


class RealAssets:
    """Hide images from pytest's automatic fixture repr on assertion failures."""
    def __init__(self, suite, cfg, images):
        self.values = (suite, cfg, images)

    def __iter__(self):
        return iter(self.values)

    def __repr__(self):
        return "<verified public fixtures and real CPU adapters>"


@pytest.fixture(scope="module")
def real_vision():
    cfg = load_config()
    models = json.loads((ROOT / "models/manifest.json").read_text(encoding="utf-8"))["models"]
    if not all((ROOT / "models" / entry["file"]).is_file() for entry in models):
        pytest.skip("Thiếu trọng số thật: chạy scripts/check_camera_models.py --download trước.")
    fixtures = json.loads((ROOT / "tests/fixtures_manifest.json").read_text(encoding="utf-8"))
    images = {}
    for name in ("portrait.jpg", "lena.jpg"):
        path = ROOT / "test_data" / name
        if not path.is_file():
            pytest.skip("Thiếu ảnh thử công khai: chạy scripts/prepare_smoke_assets.py trước.")
        expected = next(item["sha256"] for item in fixtures if item["file"] == name)
        assert hashlib.sha256(path.read_bytes()).hexdigest() == expected
        images[name] = cv2.imread(str(path))
        assert images[name] is not None
    suite = CameraVision(ROOT / "models", cfg)
    assert all(reason is None for reason in suite.health.values()), suite.health
    yield RealAssets(suite, cfg, images)
    suite.close()


def extract(suite, cfg, image):
    rows = suite.detect_faces(image)
    assert rows is not None and len(rows) == 1
    assert image_quality(image, cfg)["ok"]
    assert face_quality(image, rows[0], cfg)["ok"]
    embedding = suite.embed(image, rows[0])
    assert embedding is not None and embedding.shape == (128,)
    assert np.isfinite(embedding).all()
    assert float(np.linalg.norm(embedding)) == pytest.approx(1, abs=1e-5)
    return rows[0], embedding


def test_real_detection_embedding_landmark_pose_and_objects(real_vision):
    suite, cfg, images = real_vision
    for image in images.values():
        row, _ = extract(suite, cfg, image)
        result = suite.landmarks(image)
        assert result is not None and len(result.face_landmarks) == 1
        assert len(result.face_landmarks[0]) >= 478
        pose = PoseEstimator(cfg).measure(
            result.face_landmarks[0], image.shape[1], image.shape[0], True,
            result.facial_transformation_matrixes[0], frame=image,
        )
        assert pose is not None
        assert np.isfinite((pose.yaw, pose.pitch, pose.roll)).all()
        objects = suite.detect_objects(image)
        assert objects is not None, suite.health
        assert all(o["class_id"] in {0, 39, 41, 63, 67, 73} for o in objects)
        assert all(0 <= o["confidence"] <= 1 for o in objects)
        assert row[2] > 0
    # The public portrait contains one prominent person. No phone claim is made.
    objects = suite.detect_objects(images["portrait.jpg"])
    assert any(item["class_id"] == 0 and item["name"] == "Người" for item in objects)


def test_real_enrollment_unicode_encryption_restart_and_two_identity_tracking(real_vision, tmp_path):
    suite, cfg, images = real_vision
    original = images["portrait.jpg"]
    height, width = original.shape[:2]
    collector = EnrollmentCollector(
        cfg["enrollment_samples"], cfg["enrollment_min_samples"], cfg["enrollment_same_person_threshold"]
    )
    _, second_vector = extract(suite, cfg, images["lena.jpg"])
    for index in range(30):
        angle = -6 + (index % 7) * 2
        scale = .94 + (index // 7) * .03
        matrix = cv2.getRotationMatrix2D((width / 2, height / 2), angle, scale)
        matrix[0, 2] += ((index % 3) - 1) * 6
        transformed = cv2.warpAffine(original, matrix, (width, height), borderMode=cv2.BORDER_REFLECT)
        transformed = cv2.convertScaleAbs(transformed, alpha=.90 + (index % 5) * .04, beta=(index % 3) * 2)
        row, vector = extract(suite, cfg, transformed)
        collector.offer(vector, face_quality(transformed, row, cfg)["ok"], 1, index * .4)
        if index == 4:
            # A different person's real embedding cannot enter this live batch.
            before = collector.count
            assert not collector.offer(second_vector, True, 1, index * .4 + .35)[0]
            assert collector.count == before
        if collector.count == cfg["enrollment_samples"]:
            break
    assert collector.count == cfg["enrollment_samples"]
    assert collector.ready
    _, first_vector = extract(suite, cfg, original)
    store = IdentityStore(tmp_path / "test-profiles")
    try:
        first = store.add("Nguyễn Duy Việt", collector.embedding(), suite.model_version, make_main=True)
        # Before adding B, the real B fixture must be rejected by A-only gallery.
        assert recognize(second_vector, store.profiles(), suite.model_version,
                         cfg["recognition_threshold"], cfg["recognition_margin"]).profile_id is None
        second = store.add("Người thử B", second_vector, suite.model_version)
    finally:
        store.close()
    store = IdentityStore(tmp_path / "test-profiles")
    try:
        assert store.main_profile_id == first.id
        assert first.name.encode("utf-8") not in store.path.read_bytes()
        profiles = store.profiles()
        assert recognize(first_vector, profiles, suite.model_version,
                         cfg["recognition_threshold"], cfg["recognition_margin"]).profile_id == first.id
        assert recognize(second_vector, profiles, suite.model_version,
                         cfg["recognition_threshold"], cfg["recognition_margin"]).profile_id == second.id
        tracker = FaceTracker(cfg["recognition_threshold"], cfg["recognition_margin"])
        tracker.select_main(first.id)
        for at in (1, 1.3):
            tracks = tracker.update([
                FaceDetection((0, 0, 100, 100), first_vector),
                FaceDetection((200, 0, 300, 100), second_vector),
            ], at, profiles, suite.model_version)
        ids = {t.profile_id: t.id for t in tracks}
        swapped = tracker.update([
            FaceDetection((0, 0, 100, 100), second_vector),
            FaceDetection((200, 0, 300, 100), first_vector),
        ], 1.6, profiles, suite.model_version)
        assert {t.profile_id: t.id for t in swapped} == ids
        assert tracker.main_track(swapped).profile_id == first.id
        absent = tracker.update([FaceDetection((200, 0, 300, 100), second_vector)], 1.9,
                                profiles, suite.model_version)
        assert tracker.main_track(absent) is None
    finally:
        store.close()


def test_missing_models_return_unavailable_without_fake_results(tmp_path):
    cfg = load_config()
    suite = CameraVision(tmp_path, cfg)
    try:
        frame = np.zeros((240, 320, 3), np.uint8)
        assert all(reason for reason in suite.health.values())
        assert suite.model_version == "unavailable"
        assert suite.detect_faces(frame) is None
        assert suite.embed(frame, np.zeros(15, np.float32)) is None
        assert suite.detect_objects(frame) is None
        assert suite.landmarks(frame) is None
    finally:
        suite.close()
