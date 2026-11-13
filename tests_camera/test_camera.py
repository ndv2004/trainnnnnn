"""Video capture tests use a generated color pattern, not biometric footage."""
from __future__ import annotations

import time
from unittest.mock import patch

import cv2
import numpy as np

from camera_app.camera import CameraSource
from camera_app.config import load_config


def test_single_real_capture_latest_frame_eof_and_release(tmp_path):
    path = tmp_path / "source.avi"
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), 10, (320, 240))
    assert writer.isOpened(), "OpenCV MJPG video writer unavailable"
    try:
        for index in range(8):
            frame = np.full((240, 320, 3), 45 + index * 20, np.uint8)
            cv2.rectangle(frame, (20 + index * 5, 20), (80 + index * 5, 100), (30, 200, 80), -1)
            writer.write(frame)
    finally:
        writer.release()
    real_capture = cv2.VideoCapture
    captures = []

    def counted_capture(*args, **kwargs):
        capture = real_capture(*args, **kwargs)
        captures.append(capture)
        return capture

    with patch("camera_app.camera.cv2.VideoCapture", side_effect=counted_capture):
        source = CameraSource(load_config(), video=path)
        source.start()
        packets = []
        deadline = time.monotonic() + 4
        while source.thread.is_alive() and time.monotonic() < deadline:
            packet, _ = source.snapshot()
            if packet is not None and (not packets or packets[-1].seq != packet.seq):
                packets.append(packet)
            time.sleep(.02)
        source.stop()
        source.join()
    assert len(captures) == 1
    assert not captures[0].isOpened()
    assert not source.thread.is_alive()
    assert len(packets) >= 2
    assert all(p.image.shape == (240, 320, 3) for p in packets)
    assert all(a.at < b.at and a.seq < b.seq for a, b in zip(packets, packets[1:]))
    packet, error = source.snapshot()
    assert packet is None
    assert error == "Hết video kiểm thử"


def test_failed_video_source_invalidates_frame_and_reports_technical_error(tmp_path):
    source = CameraSource(load_config(), video=tmp_path / "does-not-exist.avi")
    source.start()
    source.join()
    packet, error = source.snapshot()
    assert packet is None
    assert "Không mở được camera" in error
    assert source.capture is None
