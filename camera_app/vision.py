"""Real CPU adapters; NanoDet decoder adapted from OpenCV Zoo Apache-2.0 demo.

Weights, checksums and upstream license copies remain in models/ and third_party/.
Inputs are unmirrored BGR. Face embeddings are SFace features, never landmarks.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import cv2
import numpy as np

OBJECT_NAMES = {0: "Người", 39: "Chai", 41: "Cốc", 63: "Laptop", 67: "Điện thoại", 73: "Sách"}


class ObjectDetector:
    def __init__(self, path):
        self.net = cv2.dnn.readNet(str(path))
        self.net.setPreferableBackend(cv2.dnn.DNN_BACKEND_OPENCV)
        self.net.setPreferableTarget(cv2.dnn.DNN_TARGET_CPU)
        self.anchors = []
        for stride in (8, 16, 32, 64):
            x, y = np.meshgrid(np.arange(416 // stride), np.arange(416 // stride))
            self.anchors.append(np.column_stack((x.ravel(), y.ravel())) * stride + 0.5 * (stride - 1))

    def infer(self, image, cfg):
        h, w = image.shape[:2]
        scale = min(416 / w, 416 / h)
        pad = np.zeros((416, 416, 3), np.float32)
        small = cv2.resize(image, (round(w * scale), round(h * scale)))
        pad[:small.shape[0], :small.shape[1]] = small
        pad = (pad - np.array([103.53, 116.28, 123.675], np.float32)) / np.array(
            [57.375, 57.12, 58.395], np.float32)
        self.net.setInput(cv2.dnn.blobFromImage(pad))
        outputs = self.net.forward(self.net.getUnconnectedOutLayersNames())
        if len(outputs) not in (6, 8):
            raise ValueError("NanoDet output mismatch")
        boxes, scores = [], []
        for stride, cls, reg, anchors in zip((8, 16, 32, 64), outputs[::2], outputs[1::2], self.anchors):
            cls, reg = cls.reshape(-1, 80), reg.reshape(-1, 4, 8)
            z = reg - reg.max(axis=2, keepdims=True)
            p = np.exp(z)
            d = ((p / p.sum(axis=2, keepdims=True)) * np.arange(8)).sum(axis=2) * stride
            boxes.append(np.column_stack((anchors - d[:, :2], anchors + d[:, 2:])))
            scores.append(cls)
        boxes, scores = np.concatenate(boxes), np.concatenate(scores)
        if not np.isfinite(boxes).all() or not np.isfinite(scores).all():
            raise ValueError("Nonfinite object output")
        result = []
        for label, name in OBJECT_NAMES.items():
            threshold = cfg["person_confidence"] if label == 0 else (
                cfg["phone_confidence"] if label == 67 else cfg["object_confidence"])
            selected = np.where(scores[:, label] >= threshold)[0]
            b = np.clip(boxes[selected], 0, 416)
            xywh = b.copy()
            xywh[:, 2:] -= xywh[:, :2]
            keep = cv2.dnn.NMSBoxes(xywh.tolist(), scores[selected, label].tolist(), threshold, 0.6)
            for index in np.asarray(keep).reshape(-1):
                coords = b[index] / scale
                coords[[0, 2]] = np.clip(coords[[0, 2]], 0, w)
                coords[[1, 3]] = np.clip(coords[[1, 3]], 0, h)
                result.append({"class_id": label, "name": name, "confidence": float(scores[selected[index], label]),
                               "box": tuple(float(v) for v in coords)})
        return result


def image_quality(frame, cfg):
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    brightness = float(gray.mean())
    sharpness = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    reason = ""
    if not cfg["min_brightness"] <= brightness <= cfg["max_brightness"]:
        reason = "Cần ánh sáng đều hơn"
    elif sharpness < cfg["min_sharpness"]:
        reason = "Ảnh mờ, giữ camera và mặt ổn định"
    return {"ok": not reason, "reason": reason, "brightness": brightness, "sharpness": sharpness}


def face_quality(frame, face, cfg):
    x, y, w, h = (int(v) for v in face[:4])
    fh, fw = frame.shape[:2]
    if min(w, h) < cfg["min_face_pixels"] or x < 0 or y < 0 or x + w > fw or y + h > fh:
        return {"ok": False, "reason": "Đưa mặt gần hơn và nằm trọn khung"}
    return image_quality(frame[y:y+h, x:x+w], cfg)


class CameraVision:
    def __init__(self, directory, cfg):
        self.cfg = cfg
        self.health = {}
        self.face = self.recognizer = self.objects = self.landmarker = None
        self.model_version = "unavailable"
        cv2.setNumThreads(2)
        directory = Path(directory)
        try:
            entries = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))["models"]
        except (OSError, ValueError, KeyError):
            entries = []
        entries = {e["module"]: e for e in entries}
        for module in ("face_detection", "face_verification", "object_detection", "head_pose"):
            try:
                entry = entries[module]
                path = directory / entry["file"]
                if path.name != entry["file"] or hashlib.sha256(path.read_bytes()).hexdigest() != entry["sha256"]:
                    raise ValueError("CHECKSUM_MISMATCH")
                if module == "face_detection":
                    self.face = cv2.FaceDetectorYN.create(str(path), "", (640, 480), cfg["face_confidence"], 0.3, 5000)
                elif module == "face_verification":
                    self.recognizer = cv2.FaceRecognizerSF.create(str(path), "")
                    self.model_version = entry["file"] + ":" + entry["sha256"]
                elif module == "object_detection":
                    self.objects = ObjectDetector(path)
                else:
                    import mediapipe as mp
                    self.mp = mp
                    self.landmarker = mp.tasks.vision.FaceLandmarker.create_from_options(
                        mp.tasks.vision.FaceLandmarkerOptions(
                            base_options=mp.tasks.BaseOptions(model_asset_path=str(path),
                                                             delegate=mp.tasks.BaseOptions.Delegate.CPU),
                            running_mode=mp.tasks.vision.RunningMode.IMAGE,
                            num_faces=5, output_facial_transformation_matrixes=True))
                self.health[module] = None
            except FileNotFoundError:
                self.health[module] = "Thiếu trọng số"
            except KeyError:
                self.health[module] = "Thiếu manifest mô hình"
            except Exception as exc:
                self.health[module] = "Sai checksum" if str(exc) == "CHECKSUM_MISMATCH" else "Không nạp được mô hình"

    def _call(self, module, adapter, callback):
        if adapter is None:
            return None
        try:
            value = callback()
            self.health[module] = None
            return value
        except Exception:
            self.health[module] = "Lỗi suy luận mô hình"
            return None

    def detect_faces(self, frame):
        def call():
            self.face.setInputSize((frame.shape[1], frame.shape[0]))
            rows = self.face.detect(frame)[1]
            return np.empty((0, 15), np.float32) if rows is None else rows
        return self._call("face_detection", self.face, call)

    def embed(self, frame, face):
        def call():
            vector = self.recognizer.feature(self.recognizer.alignCrop(frame, face)).reshape(-1).astype(np.float32)
            norm = np.linalg.norm(vector)
            if norm < 1e-8 or vector.shape != (128,) or not np.isfinite(vector).all():
                raise ValueError("Invalid SFace output")
            return vector / norm
        return self._call("face_verification", self.recognizer, call)

    def detect_objects(self, frame):
        return self._call("object_detection", self.objects, lambda: self.objects.infer(frame, self.cfg))

    def landmarks(self, frame):
        return self._call("head_pose", self.landmarker, lambda: self.landmarker.detect(
            self.mp.Image(image_format=self.mp.ImageFormat.SRGB, data=cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))))

    def close(self):
        if self.landmarker:
            self.landmarker.close()
