from __future__ import annotations

import hashlib
import json
import threading
import time
from pathlib import Path

import cv2
import numpy as np

from .config import Config
from .domain import Observation, Signal, Verdict, utc_add, utcnow
from .images import quality


class NanoDet:
    """NanoDet-Plus 416, OpenCV Zoo. DFL decoding adapted from its Apache-2.0 demo.

    Only COCO class 0 (person) and 67 (cell phone). Class-aware NMS keeps a
    phone overlapping a person. Coordinates are mapped back to the original frame.
    See third_party/object_detection_nanodet_LICENSE.
    """

    def __init__(self, path):
        self.net = cv2.dnn.readNet(str(path))
        self.net.setPreferableBackend(cv2.dnn.DNN_BACKEND_OPENCV)
        self.net.setPreferableTarget(cv2.dnn.DNN_TARGET_CPU)
        self.anchors = []
        for stride in (8, 16, 32, 64):
            x, y = np.meshgrid(np.arange(416 // stride), np.arange(416 // stride))
            self.anchors.append(np.column_stack((x.flatten(), y.flatten())) * stride + 0.5 * (stride - 1))

    def infer(self, image, cfg):
        h, w = image.shape[:2]
        scale = min(416 / w, 416 / h)
        nw, nh = round(w * scale), round(h * scale)
        pad = np.zeros((416, 416, 3), np.float32)
        pad[:nh, :nw] = cv2.resize(image, (nw, nh))
        pad = (pad - np.array([103.53, 116.28, 123.675], np.float32)) / np.array(
            [57.375, 57.12, 58.395], np.float32
        )
        self.net.setInput(cv2.dnn.blobFromImage(pad))
        outputs = self.net.forward(self.net.getUnconnectedOutLayersNames())
        boxes, scores = [], []
        for stride, cls, reg, anchors in zip((8, 16, 32, 64), outputs[::2], outputs[1::2], self.anchors):
            cls, reg = cls.reshape(-1, 80), reg.reshape(-1, 4, 8)
            z = reg - reg.max(axis=2, keepdims=True)
            p = np.exp(z)
            distances = ((p / p.sum(axis=2, keepdims=True)) * np.arange(8)).sum(axis=2) * stride
            b = np.column_stack((anchors - distances[:, :2], anchors + distances[:, 2:]))
            boxes.append(b)
            scores.append(cls[:, [0, 67]])
        boxes, scores = np.concatenate(boxes), np.concatenate(scores)
        result = []
        for col, label, threshold in (
            (0, "person", cfg.confidence_person),
            (1, "phone", cfg.confidence_phone),
        ):
            selected = np.where(scores[:, col] >= threshold)[0]
            b = np.clip(boxes[selected], 0, 416)
            xywh = b.copy()
            xywh[:, 2:] -= xywh[:, :2]
            keep = cv2.dnn.NMSBoxes(xywh.tolist(), scores[selected, col].tolist(), threshold, 0.6)
            for index in np.array(keep).reshape(-1):
                coords = b[index] / scale
                coords[[0, 2]] = np.clip(coords[[0, 2]], 0, w)
                coords[[1, 3]] = np.clip(coords[[1, 3]], 0, h)
                result.append(
                    {
                        "label": label,
                        "confidence": float(scores[selected[index], col]),
                        "box": [round(float(x), 1) for x in coords],
                    }
                )
        return result


class ModelSuite:
    """One CPU suite, serialized across workers and preflight requests."""

    def __init__(self, models_dir: Path):
        self.directory = Path(models_dir)
        self.lock = threading.RLock()
        self.health = {}
        self.versions = {}
        self.face = self.recognizer = self.landmarker = self.objects = None
        cv2.setNumThreads(2)
        self._load()

    def _load(self):
        try:
            manifest = json.loads((self.directory / "manifest.json").read_text())
            entries = {m["module"]: m for m in manifest["models"]}
        except (OSError, ValueError, KeyError):
            entries = {}
        for module in ("face_detection", "object_detection", "face_verification", "head_pose"):
            entry = entries.get(module)
            try:
                if not entry:
                    raise FileNotFoundError
                path = self.directory / entry["file"]
                actual = hashlib.sha256(path.read_bytes()).hexdigest()
                if actual != entry["sha256"]:
                    self.health[module] = {"status": "UNAVAILABLE", "error_code": "CHECKSUM_MISMATCH"}
                    continue
                if module == "face_detection":
                    self.face = cv2.FaceDetectorYN.create(str(path), "", (640, 480), 0.8, 0.3, 5000)
                elif module == "face_verification":
                    self.recognizer = cv2.FaceRecognizerSF.create(str(path), "")
                elif module == "object_detection":
                    self.objects = NanoDet(path)
                else:
                    import mediapipe as mp

                    self.mp = mp
                    options = mp.tasks.vision.FaceLandmarkerOptions(
                        base_options=mp.tasks.BaseOptions(
                            model_asset_path=str(path), delegate=mp.tasks.BaseOptions.Delegate.CPU
                        ),
                        running_mode=mp.tasks.vision.RunningMode.IMAGE,
                        num_faces=3,
                        output_facial_transformation_matrixes=True,
                    )
                    self.landmarker = mp.tasks.vision.FaceLandmarker.create_from_options(options)
                self.health[module] = {"status": "AVAILABLE", "error_code": None}
                self.versions[module] = {
                    "name": entry["file"],
                    "sha256": actual,
                    "runtime": "CPU",
                    "license_ref": entry.get("license"),
                    "source": entry.get("source"),
                    "metrics_ref": None,
                }
            except FileNotFoundError:
                self.health[module] = {"status": "UNAVAILABLE", "error_code": "WEIGHTS_MISSING"}
            except Exception:
                self.health[module] = {"status": "UNAVAILABLE", "error_code": "MODEL_LOAD_FAILED"}
        self.health["gaze"] = {"status": "DISABLED", "error_code": "EXPERIMENTAL_UNVALIDATED"}
        self.health["liveness"] = {"status": "DISABLED", "error_code": "ANTI_SPOOF_NOT_VALIDATED"}

    def _call(self, module, function):
        started = time.perf_counter()
        try:
            result = function()
            self.health[module] = {
                "status": "AVAILABLE",
                "error_code": None,
                "inference_ms": round((time.perf_counter() - started) * 1000, 2),
                "last_observed_at": utcnow(),
                "valid_until": utc_add(utcnow(), 1.5),
            }
            return result
        except Exception:
            self.health[module] = {"status": "UNAVAILABLE", "error_code": "MODEL_INFERENCE_FAILED"}
            return None

    def inspect(self, frame, cfg: Config, landmarks=True):
        with self.lock:
            base = quality(frame, cfg)
            faces = None
            if self.face is not None:

                def detect():
                    self.face.setInputSize((frame.shape[1], frame.shape[0]))
                    self.face.setScoreThreshold(cfg.face_confidence)
                    found = self.face.detect(frame)[1]
                    return found if found is not None else np.empty((0, 15), np.float32)

                # Still detect multiple faces for precheck, even when image quality fails.
                faces = self._call("face_detection", detect)
            q = quality(frame, cfg, faces)
            pose = None
            if landmarks and base["image_ok"] and q["face_ok"] and self.landmarker is not None:

                def locate():
                    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                    result = self.landmarker.detect(
                        self.mp.Image(image_format=self.mp.ImageFormat.SRGB, data=rgb)
                    )
                    if len(result.face_landmarks) != 1 or not result.facial_transformation_matrixes:
                        return None
                    lm = result.face_landmarks[0]
                    r = np.array(result.facial_transformation_matrixes[0])[:3, :3]
                    if not np.isfinite(r).all():
                        raise ValueError("Non-finite model output")
                    u, _, vt = np.linalg.svd(r)
                    angles = cv2.RQDecomp3x3(u @ vt)[0]
                    pitch, yaw, roll = (float(v) for v in angles)
                    if not np.isfinite([pitch, yaw, roll]).all():
                        raise ValueError("Non-finite model output")
                    if max(abs(pitch), abs(yaw)) > cfg.max_pose_angle:
                        return None
                    xy = np.array([[p.x, p.y] for p in lm])
                    if not np.isfinite(xy).all():
                        raise ValueError("Non-finite model output")

                    def eye(a, b, upper, lower, iris):
                        width = np.linalg.norm(xy[a] - xy[b])
                        ratio = np.linalg.norm(xy[upper] - xy[lower]) / max(width, 1e-6)
                        direction = xy[b] - xy[a]
                        horizontal = np.dot(xy[iris] - xy[a], direction) / max(
                            np.dot(direction, direction), 1e-6
                        )
                        return float(ratio), float(horizontal)

                    left_eye, r_eye = eye(33, 133, 159, 145, 468), eye(362, 263, 386, 374, 473)
                    return {
                        "yaw": yaw,
                        "pitch": pitch,
                        "roll": roll,
                        "eyes_ok": min(left_eye[0], r_eye[0]) >= cfg.min_eye_ratio,
                        "iris_x": (left_eye[1] + r_eye[1]) / 2,
                        "face_center": [
                            float(faces[0][0] + faces[0][2] / 2) / frame.shape[1],
                            float(faces[0][1] + faces[0][3] / 2) / frame.shape[0],
                        ],
                    }

                pose = self._call("head_pose", locate)
            return {"quality": q, "faces": faces, "pose": pose}

    def embedding(self, frame, faces):
        with self.lock:
            if self.recognizer is None or faces is None or len(faces) != 1:
                return None

            def extract():
                crop = self.recognizer.alignCrop(frame, faces[0])
                vector = self.recognizer.feature(crop).reshape(-1).astype(np.float32)
                norm = np.linalg.norm(vector)
                if norm < 1e-8 or not np.isfinite(vector).all():
                    raise ValueError("Invalid embedding")
                return vector / norm

            return self._call("face_verification", extract)

    def observe(self, frame, cfg: Config, at: float, utc: str, seq: int, baseline=None, run_objects=True):
        with self.lock:
            inspected = self.inspect(frame, cfg)
            q, faces, pose = inspected["quality"], inspected["faces"], inspected["pose"]
            objects = None
            if q["image_ok"] and run_objects and self.objects is not None:
                objects = self._call("object_detection", lambda: self.objects.infer(frame, cfg))
            valid = at + cfg.max_gap_ms / 1000
            signals = {}

            def signal(kind, available, positive):
                signals[kind] = (
                    Signal(Verdict.POSITIVE if positive else Verdict.NEGATIVE, at, valid)
                    if available
                    else Signal(Verdict.UNKNOWN, at, at, "INSUFFICIENT_DATA")
                )

            signal(
                "FACE_NOT_VISIBLE", q["image_ok"] and faces is not None, faces is not None and len(faces) == 0
            )
            signal(
                "MULTIPLE_FACES", q["image_ok"] and faces is not None, faces is not None and len(faces) >= 2
            )
            signal(
                "MULTIPLE_PEOPLE",
                objects is not None,
                objects is not None and sum(x["label"] == "person" for x in objects) >= 2,
            )
            signal(
                "PHONE_VISIBLE",
                objects is not None,
                objects is not None and any(x["label"] == "phone" for x in objects),
            )
            if not run_objects and q["image_ok"]:
                for kind in ("MULTIPLE_PEOPLE", "PHONE_VISIBLE"):
                    signals[kind] = Signal(Verdict.UNKNOWN, at, at, "NOT_SCHEDULED")
            head = gaze = None
            if pose and baseline:
                yaw = pose["yaw"] - baseline["yaw"]
                pitch = pose["pitch"] - baseline["pitch"]
                head = {"yaw_relative": yaw, "pitch_relative": pitch}
                away = abs(yaw) > cfg.yaw_open or abs(pitch) > cfg.pitch_open
                neutral = abs(yaw) < cfg.yaw_clear and abs(pitch) < cfg.pitch_clear
                # The hysteresis band is UNKNOWN: it never qualifies a return to neutral.
                signal("HEAD_AWAY", away or neutral, away)
                if cfg.gaze_enabled and baseline.get("eyes_ok") and pose["eyes_ok"] and neutral:
                    delta = pose["iris_x"] - baseline["iris_x"]
                    gaze = {"relative_horizontal": delta, "status": "EXPERIMENTAL"}
                    signal("GAZE_AWAY", True, abs(delta) > cfg.gaze_delta)
            for kind in ("HEAD_AWAY", "GAZE_AWAY"):
                signals.setdefault(kind, Signal(Verdict.UNKNOWN, at, at, "NOT_CALIBRATED_OR_DISABLED"))
            face_boxes = (
                []
                if faces is None
                else [
                    {
                        "box": [float(f[0]), float(f[1]), float(f[0] + f[2]), float(f[1] + f[3])],
                        "confidence": float(f[14]),
                    }
                    for f in faces
                ]
            )
            data = {
                "quality": q,
                "face_boxes": face_boxes,
                "objects": objects,
                "head_pose": head,
                "gaze": gaze,
                "pose": pose,
                "signals": {k: v.value.value for k, v in signals.items()},
                "modules": json.loads(json.dumps(self.health)),
                "model_versions": self.versions,
            }
            if cfg.gaze_enabled:
                data["modules"]["gaze"] = {
                    "status": "EXPERIMENTAL" if gaze else "UNKNOWN",
                    "error_code": "UNVALIDATED_RELATIVE_IRIS_HEURISTIC"
                    if gaze
                    else "EYE_OR_CALIBRATION_INSUFFICIENT",
                }
            return Observation(at, utc, seq, signals, data=data), inspected

    def close(self):
        if self.landmarker is not None:
            self.landmarker.close()
