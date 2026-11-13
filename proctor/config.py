from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator

ROOT = Path(__file__).resolve().parents[1]


class Rule(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    open_ms: int = Field(ge=0, le=60000)
    samples: int = Field(ge=1, le=1000)
    clear_ms: int = Field(default=1000, ge=0, le=60000)


class Config(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: str = "1.0"
    version: str = "cfg-cpu-1"
    max_gap_ms: int = Field(default=1500, ge=100, le=5000)
    cooldown_ms: int = Field(default=3000, ge=0, le=60000)
    camera_interrupted_ms: int = Field(default=3000, ge=1500, le=60000)
    max_requests_per_second: int = Field(default=5, ge=1, le=5)
    max_active_sessions: int = Field(default=2, ge=1, le=8)
    max_queue_age_ms: int = Field(default=1500, ge=100, le=1500)
    max_offset_skew_ms: int = Field(default=1500, ge=100, le=5000)
    max_image_bytes: int = Field(default=1048576, ge=1024, le=1048576)
    max_width: int = Field(default=1920, ge=320, le=1920)
    max_height: int = Field(default=1080, ge=240, le=1080)
    min_brightness: float = Field(default=40, ge=0, le=255)
    max_brightness: float = Field(default=225, ge=0, le=255)
    min_sharpness: float = Field(default=30, ge=0)
    min_face_pixels: int = Field(default=70, ge=20)
    face_confidence: float = Field(default=0.8, gt=0, lt=1)
    confidence_person: float = Field(default=0.60, gt=0, lt=1)
    confidence_phone: float = Field(default=0.65, gt=0, lt=1)
    object_interval_ms: int = Field(default=500, ge=0, le=1500)
    yaw_open: float = Field(default=25, gt=0, le=80)
    pitch_open: float = Field(default=20, gt=0, le=80)
    yaw_clear: float = Field(default=15, gt=0, le=60)
    pitch_clear: float = Field(default=12, gt=0, le=60)
    max_pose_angle: float = Field(default=65, gt=0, le=80)
    min_eye_ratio: float = Field(default=0.12, gt=0, lt=1)
    gaze_enabled: bool = False
    gaze_delta: float = Field(default=0.20, gt=0, lt=1)
    calibration_samples: int = Field(default=10, ge=3, le=100)
    calibration_duration_ms: int = Field(default=2000, ge=1000, le=30000)
    calibration_max_std_deg: float = Field(default=5, gt=0, le=20)
    verify_attempt_limit: int = Field(default=3, ge=1, le=10)
    verification_interval_ms: int = Field(default=60000, ge=1000)
    identity_window_ms: int = Field(default=180000, ge=1000)
    verification_cosine_threshold: float = Field(default=0.363, ge=-1, le=1)
    turn_min_ms: int = Field(default=500, ge=100)
    neutral_min_ms: int = Field(default=500, ge=100)
    turn_window_ms: int = Field(default=60000, ge=1000)
    turn_count: int = Field(default=4, ge=2)
    evidence_days: int = Field(default=30, ge=1, le=365)
    evidence_max_per_event: int = Field(default=3, ge=1, le=3)
    evidence_interval_ms: int = Field(default=10000, ge=1000)
    required_modules: tuple[str, ...] = (
        "face_detection",
        "object_detection",
        "face_verification",
        "head_pose",
    )
    rules: dict[str, Rule] = Field(
        default_factory=lambda: {
            "FACE_NOT_VISIBLE": Rule(open_ms=3000, samples=5),
            "MULTIPLE_PEOPLE": Rule(open_ms=2000, samples=3),
            "MULTIPLE_FACES": Rule(open_ms=2000, samples=3),
            "PHONE_VISIBLE": Rule(open_ms=1000, samples=3),
            "HEAD_AWAY": Rule(open_ms=4000, samples=5),
            "GAZE_AWAY": Rule(open_ms=5000, samples=5),
        }
    )

    @model_validator(mode="after")
    def consistent(self):
        if self.yaw_clear >= self.yaw_open or self.pitch_clear >= self.pitch_open:
            raise ValueError("Neutral angles must be below opening angles")
        if self.min_brightness >= self.max_brightness:
            raise ValueError("Invalid brightness range")
        if set(self.rules) != {
            "FACE_NOT_VISIBLE",
            "MULTIPLE_PEOPLE",
            "MULTIPLE_FACES",
            "PHONE_VISIBLE",
            "HEAD_AWAY",
            "GAZE_AWAY",
        }:
            raise ValueError("All six duration rules are required")
        if not set(self.required_modules) <= {
            "face_detection",
            "object_detection",
            "face_verification",
            "head_pose",
        }:
            raise ValueError("Unknown required module")
        return self

    @property
    def digest(self):
        return hashlib.sha256(self.model_dump_json().encode()).hexdigest()


class Settings:
    def __init__(self, data_dir: Path | None = None, demo: bool | None = None):
        self.data_dir = Path(data_dir or os.getenv("PROCTOR_DATA_DIR", ROOT / "runtime")).resolve()
        self.models_dir = Path(os.getenv("PROCTOR_MODELS_DIR", ROOT / "models")).resolve()
        self.config_path = Path(os.getenv("PROCTOR_CONFIG", ROOT / "config" / "cpu-v1.json"))
        self.demo = os.getenv("PROCTOR_DEMO", "1") == "1" if demo is None else demo
        self.token_ttl = 900
        self.data_dir.mkdir(parents=True, exist_ok=True)

    def load_config(self):
        return (
            Config.model_validate_json(self.config_path.read_text())
            if self.config_path.exists()
            else Config()
        )


def canonical_hash(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
