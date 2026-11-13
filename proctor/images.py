from __future__ import annotations

import io
import warnings

import cv2
import numpy as np
from PIL import Image, UnidentifiedImageError

from .config import Config


class Problem(Exception):
    def __init__(self, status: int, code: str, message: str, details=None):
        self.status, self.code, self.message, self.details = status, code, message, details or {}
        super().__init__(code)


def decode_jpeg(raw: bytes, cfg: Config) -> np.ndarray:
    if len(raw) > cfg.max_image_bytes:
        raise Problem(413, "IMAGE_TOO_LARGE", "Ảnh vượt 1 MiB")
    if not raw.startswith(b"\xff\xd8"):
        raise Problem(415, "UNSUPPORTED_IMAGE", "Chỉ nhận dữ liệu JPEG thực")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(raw)) as image:
                if image.format != "JPEG":
                    raise Problem(415, "UNSUPPORTED_IMAGE", "Chỉ nhận JPEG")
                w, h = image.size
                if w > cfg.max_width or h > cfg.max_height or w * h > cfg.max_width * cfg.max_height:
                    raise Problem(413, "IMAGE_TOO_LARGE", "Ảnh vượt kích thước cho phép")
                image.verify()
        frame = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
        if frame is None or not raw.endswith(b"\xff\xd9"):
            raise ValueError("Truncated JPEG")
        height, width = frame.shape[:2]
        if width > cfg.max_width or height > cfg.max_height:
            raise Problem(413, "IMAGE_TOO_LARGE", "Ảnh sau giải mã vượt kích thước cho phép")
        return frame
    except Problem:
        raise
    except (
        UnidentifiedImageError,
        OSError,
        ValueError,
        Image.DecompressionBombWarning,
        Image.DecompressionBombError,
        cv2.error,
    ):
        raise Problem(422, "IMAGE_UNDECODABLE", "Ảnh JPEG hỏng hoặc không giải mã được") from None


def quality(frame: np.ndarray, cfg: Config, faces=None) -> dict:
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    brightness = float(gray.mean())
    sharpness = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    reasons = []
    if brightness < cfg.min_brightness:
        reasons.append("TOO_DARK")
    if brightness > cfg.max_brightness:
        reasons.append("TOO_BRIGHT")
    if sharpness < cfg.min_sharpness:
        reasons.append("BLURRY")
    image_ok = not reasons
    face_ok = False
    if faces is None:
        reasons.append("FACE_MODEL_UNAVAILABLE")
    elif len(faces) != 1:
        reasons.append("NO_FACE" if len(faces) == 0 else "MULTIPLE_FACES")
    elif min(float(faces[0][2]), float(faces[0][3])) < cfg.min_face_pixels:
        reasons.append("FACE_TOO_SMALL")
    else:
        face_ok = image_ok
    return {
        "image_ok": image_ok,
        "face_ok": face_ok,
        "brightness": round(brightness, 2),
        "sharpness": round(sharpness, 2),
        "face_count": None if faces is None else len(faces),
        "reasons": reasons,
        "config_version": cfg.version,
    }
