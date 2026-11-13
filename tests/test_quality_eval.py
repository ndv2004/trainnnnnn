import importlib.util
from pathlib import Path

import cv2
import numpy as np

from proctor.config import Config
from proctor.images import quality


def test_quality_separates_dark_blur_small_and_multiple(raw_jpeg):
    cfg = Config()
    dark = np.zeros((480, 640, 3), np.uint8)
    assert "TOO_DARK" in quality(dark, cfg, [])["reasons"]
    blur = np.full((480, 640, 3), 100, np.uint8)
    assert "BLURRY" in quality(blur, cfg, [])["reasons"]
    frame = cv2.imdecode(np.frombuffer(raw_jpeg, np.uint8), cv2.IMREAD_COLOR)
    small = np.zeros((1, 15), np.float32)
    small[0, 2:4] = 30
    assert "FACE_TOO_SMALL" in quality(frame, cfg, small)["reasons"]
    assert "MULTIPLE_FACES" in quality(frame, cfg, np.zeros((2, 15)))["reasons"]
    assert quality(frame, cfg, [])["image_ok"] and not quality(frame, cfg, [])["face_ok"]


def test_temporal_matching_duplicates_are_false_positives():
    path = Path(__file__).resolve().parents[1] / "scripts/evaluate_events.py"
    spec = importlib.util.spec_from_file_location("evaluate_events", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    label = {"clip_id": "synthetic-logic-only", "event_type": "PHONE_VISIBLE", "start_ms": 0, "end_ms": 1000}
    result = module.evaluate([label.copy(), label.copy()], [label])
    assert result["tp"] == 1 and result["fp"] == 1 and result["fn"] == 0
    assert result["false_alerts_per_normal_hour"] is None
