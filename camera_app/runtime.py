from __future__ import annotations

import copy
import queue
import threading
import time

from .camera import CameraSource
from .events import EventEngine, Signal
from .identity import EnrollmentCollector, IdentityError, IdentityStore
from .pose import HeadGazeCalibrator, PoseEstimator
from .tracking import FaceDetection, FaceTracker
from .vision import CameraVision, face_quality, image_quality

UNKNOWN = "UNKNOWN"


def verdict(condition):
    return "POSITIVE" if condition else "NEGATIVE"


def match_landmarks(result, box, width, height):
    """Associate the primary to one unambiguous landmark set, never list order."""
    if result is None:
        return None, None
    candidates = []
    for index, points in enumerate(result.face_landmarks):
        nose = points[1]
        x, y = nose.x * width, nose.y * height
        if box[0] <= x <= box[2] and box[1] <= y <= box[3]:
            candidates.append(index)
    if len(candidates) != 1:
        return None, None
    index = candidates[0]
    matrices = result.facial_transformation_matrixes
    return result.face_landmarks[index], matrices[index] if len(matrices) > index else None


class CameraRuntime:
    """One reader + one serial CPU worker. Only immutable metadata crosses to UI.

    Commands own enrollment/store/calibration on the AI thread. No model, disk
    write, VideoCapture read or widget callback is executed on the other thread.
    """
    def __init__(self, cfg, models_dir, data_dir, video=None, camera=None):
        self.cfg, self.models_dir, self.data_dir = cfg, models_dir, data_dir
        self.source = CameraSource(cfg, video, camera)
        self.stop_event = threading.Event()
        self.commands = queue.Queue(maxsize=32)
        self.lock = threading.Lock()
        self.state = {"status": "Đang nạp mô hình CPU…", "at": None, "faces": [], "objects": [],
                      "profiles": [], "events": {}, "health": {}, "pose": {}, "enrollment": None,
                      "main_name": "Người", "notice": "", "notice_id": 0, "saved_id": 0}
        self.thread = threading.Thread(target=self._work, name="camera-vision", daemon=True)
        self.store = self.vision = None
        self.collector = None
        self.enrollment = None
        self.notice, self.notice_id, self.saved_id = "", 0, 0
        self.store_error = None
        self.active_subject = None
        self.last_object_at = -1e9
        self.object_cache = None
        self.last_shape = None
        self.explicit_calibration = False

    def start(self):
        self.source.start()
        self.thread.start()

    def command(self, action, **args):
        try:
            self.commands.put_nowait((action, args))
            return True
        except queue.Full:
            return False

    def snapshot(self):
        with self.lock:
            return copy.deepcopy(self.state)

    def _notice(self, text):
        self.notice, self.notice_id = text, self.notice_id + 1

    def _reset_subject(self):
        self.active_subject = None
        self.calibrator.reset()
        self.engine.reset()

    def _commands(self):
        while True:
            try:
                action, args = self.commands.get_nowait()
            except queue.Empty:
                return
            try:
                if action == "enroll_open":
                    if self.store is None or self.vision.recognizer is None or self.vision.face is None:
                        raise IdentityError(self.store_error or "Cần mô hình phát hiện và embedding để đăng ký.")
                    self.collector = EnrollmentCollector(
                        self.cfg["enrollment_samples"], self.cfg["enrollment_min_samples"],
                        self.cfg["enrollment_same_person_threshold"], self.cfg["enrollment_min_interval_seconds"],
                        self.cfg["enrollment_duplicate_similarity"])
                    self.enrollment = {"phase": "idle", "count": 0, "ready": False,
                                       "message": "Nhập tên và bấm Bắt đầu thu mẫu."}
                    self.engine.reset()
                elif action == "enroll_start" and self.enrollment is not None:
                    name = str(args.get("name", "")).strip()
                    if not name or len(name) > 100 or any(ord(c) < 32 for c in name):
                        raise IdentityError("Tên cần có 1–100 ký tự hợp lệ.")
                    self.collector.clear()
                    self.enrollment.update(phase="collecting", name=name, count=0, ready=False,
                                           message="Chỉ một khuôn mặt; nhìn thẳng rồi đổi góc nhẹ.")
                elif action == "enroll_save":
                    if not self.enrollment or not self.collector.ready:
                        raise IdentityError("Chưa đủ mẫu hợp lệ; chưa tạo hồ sơ.")
                    p = self.store.add(self.enrollment["name"], self.collector.embedding(),
                                       self.vision.model_version, bool(args.get("make_main")))
                    self.collector.clear()
                    self.collector = self.enrollment = None
                    self.saved_id += 1
                    self.tracker.reset()
                    self.tracker.select_main(self.store.main_profile_id)
                    self._reset_subject()
                    self._notice("Đã lưu hồ sơ: " + p.name)
                elif action == "enroll_cancel":
                    if self.collector:
                        self.collector.clear()
                    self.collector = self.enrollment = None
                    self._reset_subject()
                elif action == "select_main":
                    if self.store is None:
                        raise IdentityError(self.store_error or "Không mở được kho hồ sơ.")
                    self.store.set_main(args.get("profile_id"))
                    self.tracker.select_main(self.store.main_profile_id)
                    self._reset_subject()
                    self._notice("Đã chọn người chính; nhìn thẳng để căn chỉnh.")
                elif action == "delete":
                    self.store.delete(args["profile_id"])
                    self.tracker.reset()
                    self.tracker.select_main(self.store.main_profile_id)
                    self._reset_subject()
                    self._notice("Đã xóa hồ sơ được chọn.")
                elif action == "calibrate":
                    if self.vision.landmarker is None:
                        raise IdentityError("Chưa thể căn chỉnh: mô hình đầu/mắt chưa khả dụng.")
                    self.calibrator.reset()
                    self.engine.reset()
                    self.explicit_calibration = True
                    if self.store is None or self.store.main_profile_id is None:
                        self.tracker.reset_main()
                        self.active_subject = None
                    self._notice("Căn chỉnh: nhìn thẳng, giữ đầu ổn định và mở mắt.")
            except (IdentityError, OSError, KeyError, ValueError) as exc:
                self._notice(str(exc) if isinstance(exc, IdentityError) else "Thao tác chưa thành công; dữ liệu cũ được giữ.")

    def _publish(self, **values):
        profiles = self.store.profiles() if self.store else []
        metadata = [{"id": p.id, "name": p.name, "created_at": p.created_at,
                     "model_version": p.model_version} for p in profiles]
        main_id = self.store.main_profile_id if self.store else None
        with self.lock:
            self.state.update(values, profiles=metadata, main_profile_id=main_id,
                              enrollment=copy.deepcopy(self.enrollment), notice=self.notice,
                              notice_id=self.notice_id, saved_id=self.saved_id, store_error=self.store_error)

    def _work(self):
        try:
            self.engine = EventEngine(self.cfg)
            self.calibrator = HeadGazeCalibrator(self.cfg)
            self.pose_estimator = PoseEstimator(self.cfg)
            self.tracker = FaceTracker(self.cfg["recognition_threshold"], self.cfg["recognition_margin"],
                                       self.cfg["track_ttl_seconds"], self.cfg["recognition_ttl_seconds"],
                                       self.cfg["association_threshold"], self.cfg["recognition_confirmation_samples"])
            try:
                self.store = IdentityStore(self.data_dir)
                self.tracker.select_main(self.store.main_profile_id)
            except (IdentityError, OSError):
                self.store_error = "Không mở được hồ sơ. Giữ nguyên dữ liệu; kiểm tra tài khoản hoặc cửa sổ khác."
            self.vision = CameraVision(self.models_dir, self.cfg)
            last_seq = -1
            while not self.stop_event.is_set():
                started = time.monotonic()
                self._commands()
                packet, source_error = self.source.snapshot()
                if packet is None or time.monotonic() - packet.at > self.cfg["result_ttl_seconds"]:
                    self.engine.observe(started, {}, suspended=self.enrollment is not None or self.explicit_calibration,
                                        primary_key=self.active_subject)
                    self._publish(status=source_error or "Ảnh camera quá hạn — tạm dừng đo", at=None,
                                  faces=[], objects=[], pose={}, health=dict(self.vision.health),
                                  events=self.engine.snapshot())
                elif packet.seq != last_seq:
                    last_seq = packet.seq
                    self._process(packet)
                self.stop_event.wait(max(0.01, self.cfg["ai_interval_seconds"] - (time.monotonic()-started)))
        except Exception as exc:
            # No image/vector is logged, including errors. Module health remains visible.
            self._publish(status="Lỗi worker AI: " + type(exc).__name__, at=None, faces=[], objects=[])
        finally:
            if self.vision:
                self.vision.close()
            if self.store:
                self.store.close()

    def _process(self, packet):
        frame, at = packet.image, packet.at
        h, w = frame.shape[:2]
        if self.last_shape != (w, h):
            self.tracker.reset()
            self._reset_subject()
            self.last_shape = (w, h)
            self.object_cache = None
        base = image_quality(frame, self.cfg)
        rows = self.vision.detect_faces(frame)
        detections = []
        if rows is not None:
            for row in sorted(rows, key=lambda r: float(r[2]*r[3]), reverse=True)[:8]:
                q = face_quality(frame, row, self.cfg)
                vector = self.vision.embed(frame, row) if base["ok"] and q["ok"] else None
                detections.append(FaceDetection((float(row[0]), float(row[1]),
                                                  float(row[0]+row[2]), float(row[1]+row[3])),
                                                 vector, bool(base["ok"] and q["ok"]), {"quality": q}))
        if not self._packet_fresh(packet):
            self._stale_result()
            return
        profiles = self.store.profiles() if self.store else []
        tracks = self.tracker.update(detections, at, profiles, self.vision.model_version)
        main = self.tracker.main_track(tracks)
        main_id = self.store.main_profile_id if self.store else None
        if main_id:
            self.active_subject = "profile:" + main_id
        elif main is not None:
            self.active_subject = "track:" + str(main.id)
        primary_name = next((p.name for p in profiles if p.id == main_id), "Người")
        if main and not main_id:
            primary_name = main.name if main.status == "MATCH" else "Người"

        landmark_result = self.vision.landmarks(frame) if base["ok"] and (main or self.enrollment) else None
        if not self._packet_fresh(packet):
            self._stale_result()
            return
        measurement = None
        if main:
            points, matrix = match_landmarks(landmark_result, main.box, w, h)
            if points:
                measurement = self.pose_estimator.measure(points, w, h, main.payload["quality"]["ok"], matrix, frame=frame)
        pose = self.calibrator.update(measurement, at, self.active_subject)
        if pose.get("calibrated"):
            self.explicit_calibration = False

        if self.enrollment and self.enrollment["phase"] == "collecting":
            face_count = len(rows) if rows is not None else 0
            vector = detections[0].embedding if len(detections) == 1 else None
            sample_pose = None
            if len(detections) == 1:
                points, matrix = match_landmarks(landmark_result, detections[0].box, w, h)
                m = self.pose_estimator.measure(points, w, h, True, matrix, frame=frame) if points else None
                if m:
                    sample_pose = {"yaw": m.yaw, "pitch": m.pitch}
            _, message = self.collector.offer(vector, bool(vector is not None), face_count, at, pose=sample_pose)
            self.enrollment.update(count=self.collector.count, ready=self.collector.ready, message=message)

        object_new = False
        if at - self.last_object_at >= self.cfg["object_interval_seconds"]:
            self.last_object_at = at
            self.object_cache = self.vision.detect_objects(frame) if base["ok"] else None
            object_new = True
        objects_fresh = base["ok"] and at - self.last_object_at <= self.cfg["result_ttl_seconds"]
        objects = self.object_cache if objects_fresh else None
        valid = at + self.cfg["result_ttl_seconds"]
        signals = {}
        face_valid = base["ok"] and rows is not None
        signals["MULTIPLE_FACES"] = Signal(verdict(len(rows) >= 2) if face_valid else UNKNOWN, at, valid)
        # Failure to extract embeddings/quality is technical uncertainty, not absence.
        if face_valid and (len(rows) == 0 or main is not None):
            missing = main is None
            primary_valid = True
        elif face_valid and self.active_subject and self.vision.health.get("face_verification") is None:
            compatible = (not main_id or any(p.id == main_id and p.model_version == self.vision.model_version for p in profiles))
            primary_valid = compatible and all(t.embedding is not None and t.status not in (
                "PENDING", "AMBIGUOUS", "INSUFFICIENT") for t in tracks)
            missing = True
        else:
            primary_valid, missing = False, False
        signals["FACE_NOT_VISIBLE"] = Signal(verdict(missing) if primary_valid else UNKNOWN, at, valid)
        for key, positive in (("MULTIPLE_PEOPLE", objects is not None and sum(o["class_id"] == 0 for o in objects) >= 2),
                              ("PHONE_VISIBLE", objects is not None and any(o["class_id"] == 67 for o in objects))):
            # A cached detector sample retains its original time; it never adds samples.
            signals[key] = Signal(verdict(positive) if objects is not None else UNKNOWN,
                                  self.last_object_at, self.last_object_at+self.cfg["result_ttl_seconds"])
        signals["HEAD_AWAY"] = Signal(pose.get("head_signal", UNKNOWN), at, valid)
        signals["GAZE_AWAY"] = Signal(pose.get("gaze_signal", UNKNOWN), at, valid)
        # Slow inference cannot backfill behavioral time from an old capture.
        fresh = self._packet_fresh(packet)
        if not fresh:
            signals = {}
        auto_calibrating = bool(main and pose.get("calibrating") and self.vision.health.get("head_pose") is None)
        suspended = self.enrollment is not None or self.explicit_calibration or auto_calibrating
        self.engine.observe(time.monotonic(), signals, suspended=suspended, primary_key=self.active_subject)
        faces = [{"id": t.id, "box": t.box, "name": t.name, "status": t.status,
                  "primary": bool(main and t.id == main.id)} for t in tracks]
        status = "Đang đăng ký — tạm dừng cảnh báo" if self.enrollment else (
            pose.get("status", "Đang căn chỉnh") if suspended else (base["reason"] or "Đang quan sát"))
        self._publish(status=status if fresh else "Suy luận quá hạn — chưa đủ dữ liệu", at=at,
                      faces=faces if fresh else [], objects=(objects or []) if fresh else [],
                      main_name=primary_name, main_visible=main is not None and fresh, pose=pose if fresh else {},
                      events=self.engine.snapshot(), health=dict(self.vision.health),
                      image_size=(w, h), object_at=self.last_object_at, objects_new=object_new,
                      signals={k: s.value for k, s in signals.items()})

    def _packet_fresh(self, packet):
        latest, _ = self.source.snapshot()
        return (time.monotonic()-packet.at <= self.cfg["result_ttl_seconds"]
                and latest is not None and latest.at >= packet.at)

    def _stale_result(self):
        self.engine.observe(time.monotonic(), {}, suspended=self.enrollment is not None or self.explicit_calibration,
                            primary_key=self.active_subject)
        self._publish(status="Ảnh xử lý quá hạn hoặc mất camera — tạm dừng đo", at=None,
                      faces=[], objects=[], pose={}, health=dict(self.vision.health), events=self.engine.snapshot())

    def stop(self):
        self.stop_event.set()
        self.source.stop()

    def closed(self):
        return not self.thread.is_alive() and not self.source.thread.is_alive()

    def join(self, timeout=4):
        self.source.join(timeout/2)
        self.thread.join(timeout/2)
