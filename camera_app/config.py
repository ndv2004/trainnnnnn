from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_config(path=None):
    value = json.loads(Path(path or ROOT / "config/camera-v1.json").read_text(encoding="utf-8"))
    if value.get("version") != "camera-v1" or value.get("schema_version") != 1:
        raise ValueError("Cấu hình camera không đúng phiên bản")
    # Bound resources and reject unsafe timing rather than silently invent defaults.
    for key in ("result_ttl_seconds", "max_gap_seconds", "ai_interval_seconds", "object_interval_seconds"):
        if not 0 < value[key] <= 10:
            raise ValueError("Thời gian cấu hình không hợp lệ: " + key)
    for key in ("recognition_threshold", "recognition_margin"):
        if not 0 < value[key] < 1:
            raise ValueError("Ngưỡng danh tính không hợp lệ: " + key)
    return value
