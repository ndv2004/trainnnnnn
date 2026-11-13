from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass

import cv2


@dataclass(frozen=True)
class Frame:
    seq: int
    at: float
    image: object


class CameraSource:
    """Exactly one VideoCapture, owned/released by its reader thread.

    One latest-frame slot rather than an accumulating queue. Inference never
    holds this lock while running. Failed reads invalidate the slot immediately.
    """
    def __init__(self, cfg, video=None, camera=None):
        self.cfg, self.video = cfg, video
        self.index = cfg["camera_index"] if camera is None else camera
        self.stop_event = threading.Event()
        self.lock = threading.Lock()
        self.latest = None
        self.error = "Đang mở nguồn ảnh…"
        self.thread = threading.Thread(target=self._read, name="camera-reader", daemon=True)
        self.capture = None

    def start(self):
        self.thread.start()

    def snapshot(self):
        with self.lock:
            return self.latest, self.error

    def _read(self):
        cap = None
        try:
            cap = cv2.VideoCapture(str(self.video)) if self.video else cv2.VideoCapture(
                self.index, cv2.CAP_DSHOW if os.name == "nt" else cv2.CAP_ANY)
            self.capture = cap
            if not cap.isOpened():
                raise RuntimeError("Không mở được camera. Kiểm tra thiết bị, quyền camera hoặc ứng dụng đang dùng.")
            if not self.video:
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.cfg["width"])
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.cfg["height"])
                cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            fps = cap.get(cv2.CAP_PROP_FPS) if self.video else 0
            interval = 1 / fps if 0 < fps <= 120 else 1 / 30
            seq = 0
            while not self.stop_event.is_set():
                started = time.monotonic()
                ok, image = cap.read()
                if not ok or image is None:
                    with self.lock:
                        self.latest = None
                        self.error = "Hết video kiểm thử" if self.video else "Mất ảnh camera — tạm dừng đo hành vi"
                    if self.video:
                        break
                    self.stop_event.wait(0.2)
                    continue
                seq += 1
                with self.lock:
                    self.latest = Frame(seq, time.monotonic(), image)
                    self.error = None
                # Video replay follows its timeline. Live camera backend normally blocks.
                self.stop_event.wait(max(0.001, interval - (time.monotonic() - started)))
        except Exception as exc:
            with self.lock:
                self.latest = None
                self.error = str(exc) if isinstance(exc, RuntimeError) else "Lỗi kỹ thuật nguồn ảnh"
        finally:
            if cap:
                cap.release()
            self.capture = None

    def stop(self):
        self.stop_event.set()

    def join(self, timeout=2):
        self.thread.join(timeout)
