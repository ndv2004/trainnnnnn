"""Head rotation and independent, experimental iris-position estimates.

All computations use the ORIGINAL, unmirrored camera image. Labels describe the
subject's anatomical left/right and do not change when preview is mirrored.
Camera-coordinate yaw > 0 is a turn to the subject's right; pitch > 0 is down.
MediaPipe's metric y-up/z-forward rotation is converted before decomposition.
"""
from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np


@dataclass(frozen=True)
class PoseMeasurement:
    yaw: float
    pitch: float
    roll: float
    eyes_ok: bool = False
    iris_x: float | None = None
    eye_distance: float = 0.0
    source: str = "solvePnP"


class PoseEstimator:
    # Approximate metric face, x image-right, y down, z away from camera.
    INDICES = (1, 152, 33, 263, 61, 291)
    MODEL = np.array(((0, 0, 0), (0, 330, 65), (-225, -170, 135),
                      (225, -170, 135), (-150, 150, 125), (150, 150, 125)), np.float64)

    def __init__(self, config: dict | None = None):
        self.config = config or {}

    def measure(self, landmarks, width: int, height: int, quality_ok: bool = True,
                transformation_matrix=None, frame=None) -> PoseMeasurement | None:
        if not quality_ok or landmarks is None or len(landmarks) < 468:
            return None
        try:
            xy = np.array([[p.x, p.y] if hasattr(p, "x") else p[:2]
                           for p in landmarks], dtype=np.float64)
            if not np.isfinite(xy).all() or width < 1 or height < 1:
                return None
            pixels = xy * (width, height)
            if transformation_matrix is not None:
                matrix = np.asarray(transformation_matrix, np.float64)
                if matrix.shape != (4, 4) or not np.isfinite(matrix).all():
                    return None
                u, _, vt = np.linalg.svd(matrix[:3, :3])
                rotation = u @ vt
                if np.linalg.det(rotation) < 0.9:
                    return None
                basis = np.diag([1.0, -1.0, -1.0])
                rotation = basis @ rotation @ basis
                source = "MediaPipe facial transformation"
            else:
                focal = float(max(width, height))
                camera = np.array(((focal, 0, width / 2), (0, focal, height / 2), (0, 0, 1)))
                image_points = np.ascontiguousarray(pixels[list(self.INDICES)])
                ok, rvec, tvec = cv2.solvePnP(self.MODEL, image_points, camera, None,
                                            flags=cv2.SOLVEPNP_ITERATIVE)
                if not ok or float(tvec[2, 0]) <= 0:
                    return None
                reprojection = cv2.projectPoints(self.MODEL, rvec, tvec, camera, None)[0].reshape(-1, 2)
                eye_span = float(np.linalg.norm(pixels[33] - pixels[263]))
                error = float(np.sqrt(np.mean(np.sum((reprojection - image_points) ** 2, axis=1))))
                if error > max(float(self.config.get("pose_min_reprojection_pixels", 4.0)),
                               eye_span * float(self.config.get("pose_max_reprojection_ratio", .18))):
                    return None
                rotation = cv2.Rodrigues(rvec)[0]
                source = "solvePnP"
            pitch, yaw, roll = (float(angle) for angle in cv2.RQDecomp3x3(rotation)[0])
            if not np.isfinite([pitch, yaw, roll]).all():
                return None
            if max(abs(pitch), abs(yaw)) > float(self.config.get("max_pose_angle", 70)):
                return None
            eyes_ok, iris_x, distance = self._eyes(pixels)
            if eyes_ok and frame is not None and not self._eye_image_quality(frame, pixels):
                eyes_ok, iris_x = False, None
            return PoseMeasurement(yaw, pitch, roll, eyes_ok, iris_x, distance, source)
        except (ValueError, TypeError, IndexError, cv2.error, np.linalg.LinAlgError):
            return None

    def _eyes(self, xy):
        distance = float(np.linalg.norm(xy[33] - xy[263]))
        if len(xy) < 478:
            return False, None, distance
        measurements = []
        # Both axes run from image-left to image-right, not anatomical eye order.
        for a, b, top, bottom, iris in ((33, 133, 159, 145, 468), (362, 263, 386, 374, 473)):
            axis = xy[b] - xy[a]
            width = float(np.linalg.norm(axis))
            if width < float(self.config.get("min_eye_pixels", 13)):
                return False, None, distance
            opening = float(np.linalg.norm(xy[top] - xy[bottom])) / width
            if opening < float(self.config.get("min_eye_ratio", .12)):
                return False, None, distance
            ratio = float(np.dot(xy[iris] - xy[a], axis) / np.dot(axis, axis))
            if not float(self.config.get("iris_min_fraction", .05)) <= ratio <= float(
                    self.config.get("iris_max_fraction", .95)):
                return False, None, distance
            measurements.append(ratio)
        if abs(measurements[0] - measurements[1]) > float(self.config.get("max_eye_disagreement", .18)):
            return False, None, distance
        return True, float(np.mean(measurements)), distance

    def _eye_image_quality(self, frame, xy):
        """Reject dark, featureless or blurred eye regions, including many covers.

        This is a conservative observability gate, not an occlusion classifier;
        opaque eyewear can still fool face landmarks and remains a limitation.
        """
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if frame.ndim == 3 else frame
        height, width = gray.shape[:2]
        for a, b in ((33, 133), (362, 263)):
            eye_width = np.linalg.norm(xy[a] - xy[b])
            center = (xy[a] + xy[b]) / 2
            x0, x1 = max(0, int(center[0] - eye_width * .55)), min(width, int(center[0] + eye_width * .55))
            y0, y1 = max(0, int(center[1] - eye_width * .3)), min(height, int(center[1] + eye_width * .3))
            region = gray[y0:y1, x0:x1]
            if region.size < int(self.config.get("min_eye_region_pixels", 50)):
                return False
            if not float(self.config.get("min_eye_brightness", 30)) <= float(region.mean()) <= float(
                    self.config.get("max_eye_brightness", 235)):
                return False
            if float(region.std()) < float(self.config.get("min_eye_texture_std", 8)):
                return False
            if float(cv2.Laplacian(region, cv2.CV_64F).var()) < float(self.config.get("min_eye_sharpness", 15)):
                return False
        return True


class HeadGazeCalibrator:
    def __init__(self, config: dict | None = None):
        self.config = config or {}
        self.subject_key: str | None = None
        self.baseline: dict | None = None
        self.samples: list[tuple[float, PoseMeasurement]] = []
        self.last_at: float | None = None
        self.head_state = "Chưa xác định"
        self.gaze_state = "Chưa xác định"

    def reset(self, subject_key: str | None = None):
        self.subject_key = subject_key
        self.baseline = None
        self.samples.clear()
        self.last_at = None
        self.head_state = self.gaze_state = "Chưa xác định"

    @property
    def calibrated(self):
        return self.baseline is not None

    def update(self, measurement: PoseMeasurement | None, now: float,
               subject_key: str | None = None) -> dict:
        if subject_key != self.subject_key:
            self.reset(subject_key)
        result = {
            "head_direction": "Chưa xác định", "gaze_direction": "Chưa xác định",
            "head_signal": "UNKNOWN", "gaze_signal": "UNKNOWN",
            "calibrated": self.calibrated, "calibrating": not self.calibrated,
            "status": "Nhìn gần thẳng, giữ đầu ổn định để căn chỉnh",
            "yaw_relative": None, "pitch_relative": None, "gaze_relative": None,
            "calibration_progress": 0.0,
        }
        if measurement is None:
            if not self.calibrated:
                self.samples.clear()
            result["status"] = ("Thiếu dữ liệu đầu/mắt" if self.calibrated
                                else "Cần khuôn mặt rõ, đủ sáng để căn chỉnh")
            return result
        if self.last_at is not None and now <= self.last_at:
            result["status"] = "Đang chờ ảnh mới"
            return result
        gap = self.last_at is not None and now - self.last_at > float(self.config.get("max_gap_seconds", 1.5))
        self.last_at = now
        if not self.calibrated:
            if (abs(measurement.yaw) > float(self.config.get("calibration_max_yaw", 20))
                    or abs(measurement.pitch) > float(self.config.get("calibration_max_pitch", 25))
                    or abs(measurement.roll) > float(self.config.get("calibration_max_roll", 20))):
                self.samples.clear()
                result["status"] = "Nhìn gần thẳng vào camera để căn chỉnh"
                return result
            if gap:
                self.samples.clear()
            self.samples.append((now, measurement))
            duration = float(self.config.get("calibration_seconds", 2.0))
            count = int(self.config.get("calibration_samples", 10))
            # Keep a bounded, recent stable window instead of waiting for a bad old sample.
            while self.samples and now - self.samples[0][0] > max(duration * 2.0, 4.0):
                self.samples.pop(0)
            angles = np.array([[m.yaw, m.pitch, m.roll] for _, m in self.samples])
            if len(angles) >= 3 and float(np.max(np.std(angles, axis=0))) > float(
                    self.config.get("calibration_max_std_deg", 4.0)):
                self.samples = [(now, measurement)]
            elapsed = now - self.samples[0][0]
            result["calibration_progress"] = min(1.0, len(self.samples) / count, elapsed / max(duration, .01))
            if len(self.samples) < count or elapsed + 1e-8 < duration:
                result["status"] = f"Đang căn chỉnh: {round(result['calibration_progress'] * 100)}%"
                return result
            eye_values = [m.iris_x for _, m in self.samples if m.eyes_ok and m.iris_x is not None]
            self.baseline = {
                "yaw": float(np.median([m.yaw for _, m in self.samples])),
                "pitch": float(np.median([m.pitch for _, m in self.samples])),
                "iris_x": float(np.median(eye_values)) if len(eye_values) >= max(
                    int(self.config.get("calibration_eye_min_samples", 3)),
                    int(np.ceil(count * float(self.config.get("calibration_eye_fraction", .5))))) else None,
            }
            self.samples.clear()
        result.update(calibrated=True, calibrating=False, calibration_progress=1.0, status="Đã căn chỉnh hướng đầu")
        yaw = measurement.yaw - self.baseline["yaw"]
        pitch = measurement.pitch - self.baseline["pitch"]
        result.update(yaw_relative=yaw, pitch_relative=pitch)
        yaw_open = float(self.config.get("yaw_open", 25))
        pitch_open = float(self.config.get("pitch_open", 20))
        neutral = (abs(yaw) < float(self.config.get("yaw_clear", 15))
                   and abs(pitch) < float(self.config.get("pitch_clear", 12)))
        if abs(yaw) > yaw_open or abs(pitch) > pitch_open:
            if abs(yaw) / yaw_open >= abs(pitch) / pitch_open:
                self.head_state = "Quay phải" if yaw > 0 else "Quay trái"
            else:
                self.head_state = "Cúi" if pitch > 0 else "Ngẩng"
            result["head_signal"] = "POSITIVE"
        elif neutral:
            self.head_state = "Thẳng"
            result["head_signal"] = "NEGATIVE"
        else:
            self.head_state = "Chưa xác định"
        result["head_direction"] = self.head_state
        # Gaze is based solely on iris/corner geometry, gated by observable eyes.
        if (self.config.get("gaze_enabled", True) and measurement.eyes_ok
                and measurement.iris_x is not None and self.baseline["iris_x"] is not None
                and abs(yaw) < float(self.config.get("gaze_max_head_yaw", 15))
                and abs(pitch) < float(self.config.get("gaze_max_head_pitch", 12))):
            delta = measurement.iris_x - self.baseline["iris_x"]
            result["gaze_relative"] = delta
            threshold = float(self.config.get("gaze_delta", .16))
            clear = float(self.config.get("gaze_clear", threshold * .65))
            if abs(delta) > threshold:
                # Iris moving image-right is anatomical left in an unmirrored image.
                self.gaze_state = "Trái (ước lượng)" if delta > 0 else "Phải (ước lượng)"
                result["gaze_signal"] = "POSITIVE"
            elif abs(delta) < clear:
                self.gaze_state = "Thẳng (ước lượng)"
                result["gaze_signal"] = "NEGATIVE"
            else:
                self.gaze_state = "Chưa xác định"
            result["gaze_direction"] = self.gaze_state
            result["status"] = "Hướng mắt là ước lượng thử nghiệm"
        else:
            self.gaze_state = "Chưa xác định"
            result["status"] = ("Mô-đun hướng mắt đang tắt" if not self.config.get("gaze_enabled", True)
                                else "Hướng mắt chưa đủ điều kiện / chưa có mốc mắt")
        return result
