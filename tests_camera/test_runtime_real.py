"""Actual runtime -> video -> models -> enrollment -> DPAPI -> recognition.

The input is a transformed public portrait fixture, not a live-human accuracy
test. Temporary video and profiles remain in the test's private temporary dir.
"""
from __future__ import annotations

import time
from pathlib import Path

import cv2
import pytest

from camera_app.config import load_config
from camera_app.identity import IdentityStore
from camera_app.runtime import CameraRuntime

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.real


def wait_state(runtime, predicate, timeout):
    deadline = time.monotonic() + timeout
    last = {}
    while time.monotonic() < deadline:
        last = runtime.snapshot()
        assert not last.get("status", "").startswith("Lỗi worker"), last.get("status")
        if predicate(last):
            return last
        time.sleep(.04)
    # Metadata only: never include a frame or embedding in failure output.
    pytest.fail("Runtime wait timed out: " + str({
        "status": last.get("status"), "enrollment": last.get("enrollment"),
        "saved_id": last.get("saved_id"), "health": last.get("health"),
    }))


def test_actual_video_registration_save_recognition_shutdown_and_restart(tmp_path):
    source = ROOT / "test_data/portrait.jpg"
    if not source.is_file() or not (ROOT / "models/face_landmarker.task").is_file():
        pytest.skip("Real fixture/weights missing; run model downloader and prepare_smoke_assets.py.")
    original = cv2.imread(str(source))
    assert original is not None
    # Preserve detailed facial regions while reducing CPU input size.
    height, width = original.shape[:2]
    image = cv2.resize(original, (round(width * 640 / height), 640))
    height, width = image.shape[:2]
    # MJPG needs even dimensions on Windows.
    width -= width % 2
    image = image[:, :width]
    path = tmp_path / "runtime-enrollment.avi"
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), 10, (width, height))
    assert writer.isOpened()
    try:
        for index in range(240):
            # Hold each transformed view for 0.4 sec, enough for sample cadence.
            view = (index // 4) % 30
            angle = -6 + (view % 7) * 2
            scale = .94 + (view // 7) * .03
            matrix = cv2.getRotationMatrix2D((width / 2, height / 2), angle, scale)
            matrix[0, 2] += ((view % 3) - 1) * 4
            frame = cv2.warpAffine(image, matrix, (width, height), borderMode=cv2.BORDER_REFLECT)
            frame = cv2.convertScaleAbs(frame, alpha=.90 + (view % 5) * .04, beta=(view % 3) * 2)
            writer.write(frame)
    finally:
        writer.release()
    cfg = load_config()
    runtime = CameraRuntime(cfg, ROOT / "models", tmp_path / "profiles", video=path)
    runtime.start()
    saved_id = None
    try:
        initial = wait_state(runtime, lambda state: bool(state.get("faces")), 6)
        assert initial["profiles"] == []
        assert all(face["name"] in ("Người", "Đang xác định", "Chưa đủ dữ liệu") for face in initial["faces"])
        assert runtime.command("enroll_open")
        wait_state(runtime, lambda state: state.get("enrollment") is not None, 2)
        assert runtime.command("enroll_start", name="Nguyễn Duy Việt")
        collecting = wait_state(runtime, lambda state: (state.get("enrollment") or {}).get("ready", False), 14)
        assert collecting["enrollment"]["count"] >= cfg["enrollment_min_samples"]
        assert collecting["profiles"] == [] and collecting["saved_id"] == 0
        assert collecting["events"]["suspended"]
        assert collecting["events"]["alerts"] == []
        assert runtime.command("enroll_save", make_main=True)
        saved = wait_state(runtime, lambda state: state.get("saved_id") == 1, 3)
        saved_id = saved["profiles"][0]["id"]
        assert saved["profiles"][0]["name"] == "Nguyễn Duy Việt"
        assert saved["main_profile_id"] == saved_id
        recognized = wait_state(runtime, lambda state: state.get("main_visible") and any(
            face["name"] == "Nguyễn Duy Việt" for face in state.get("faces", [])), 4)
        assert recognized["enrollment"] is None
    finally:
        runtime.stop()
        runtime.join(8)
    assert runtime.closed()
    assert runtime.source.capture is None
    reopened = IdentityStore(tmp_path / "profiles")
    try:
        assert reopened.main_profile_id == saved_id
        assert reopened.profiles()[0].name == "Nguyễn Duy Việt"
    finally:
        reopened.close()
