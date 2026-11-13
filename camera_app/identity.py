"""Local biometric profiles and enrollment; no camera, API, or image persistence.

Windows stores the entire document with current-user DPAPI. Other platforms use
Fernet and a private key file. A lifetime OS lock prevents concurrent writers.
Enrollment vectors stay in memory until the caller explicitly invokes ``add``.
"""
from __future__ import annotations

import ctypes
import json
import math
import os
import tempfile
import threading
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import numpy as np


class IdentityError(RuntimeError):
    """Safe user-facing error: never includes biometric data."""


def normalize(vector) -> np.ndarray:
    array = np.asarray(vector, dtype=np.float32).reshape(-1)
    if not 16 <= array.size <= 4096 or not np.isfinite(array).all():
        raise IdentityError("Embedding khuôn mặt không hợp lệ.")
    norm = float(np.linalg.norm(array))
    if norm < 1e-8:
        raise IdentityError("Embedding khuôn mặt không hợp lệ.")
    return array / norm


@dataclass(frozen=True)
class Profile:
    id: str
    name: str
    embedding: np.ndarray = field(repr=False)
    model_version: str
    created_at: str


@dataclass(frozen=True)
class Match:
    profile_id: str | None = None
    name: str = "Người"
    score: float | None = None
    status: str = "UNKNOWN"


def recognize(embedding, profiles, model_version, threshold=.48, margin=.08) -> Match:
    """Reject weak or ambiguous 1:N matches; thresholds are prototype settings.

    No 1:1 verification acceptance claim is transferred to gallery recognition.
    Different model versions are never compared.
    """
    if embedding is None:
        return Match(name="Chưa đủ dữ liệu", status="INSUFFICIENT")
    try:
        vector = normalize(embedding)
    except (IdentityError, TypeError, ValueError):
        return Match(name="Chưa đủ dữ liệu", status="INSUFFICIENT")
    scores = sorted(
        ((float(np.dot(vector, p.embedding)), p) for p in profiles
         if p.model_version == model_version and p.embedding.shape == vector.shape),
        key=lambda pair: pair[0], reverse=True,
    )
    if not scores or scores[0][0] < threshold:
        return Match(score=scores[0][0] if scores else None)
    if len(scores) > 1 and scores[0][0] - scores[1][0] < margin:
        return Match(score=scores[0][0], status="AMBIGUOUS")
    score, profile = scores[0]
    return Match(profile.id, profile.name, score, "MATCH")


def _dpapi(data: bytes, decrypt=False) -> bytes:
    class Blob(ctypes.Structure):
        _fields_ = [("size", ctypes.c_ulong), ("data", ctypes.POINTER(ctypes.c_byte))]

    source_buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(source_buffer, ctypes.POINTER(ctypes.c_byte)))
    output = Blob()
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    crypt.CryptProtectData.argtypes = [
        ctypes.POINTER(Blob), ctypes.c_wchar_p, ctypes.POINTER(Blob), ctypes.c_void_p,
        ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(Blob),
    ]
    crypt.CryptProtectData.restype = ctypes.c_int
    crypt.CryptUnprotectData.argtypes = [
        ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.POINTER(Blob), ctypes.c_void_p,
        ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(Blob),
    ]
    crypt.CryptUnprotectData.restype = ctypes.c_int
    # CRYPTPROTECT_UI_FORBIDDEN: no unexpected credential dialog in workers.
    if decrypt:
        success = crypt.CryptUnprotectData(
            ctypes.byref(source), None, None, None, None, 1, ctypes.byref(output)
        )
    else:
        success = crypt.CryptProtectData(
            ctypes.byref(source), "Camera biometric profiles", None, None, None, 1, ctypes.byref(output)
        )
    if not success:
        raise IdentityError("Không thể bảo vệ/đọc hồ sơ bằng tài khoản Windows hiện tại.")
    try:
        return ctypes.string_at(output.data, output.size)
    finally:
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.LocalFree.argtypes = [ctypes.c_void_p]
        kernel.LocalFree.restype = ctypes.c_void_p
        kernel.LocalFree(output.data)


class IdentityStore:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        if os.name != "nt":
            self.directory.chmod(0o700)
        self.path = self.directory / "profiles.enc"
        self._mutex = threading.RLock()
        self._profiles: list[Profile] = []
        self._main_profile_id: str | None = None
        self._lock_file = open(self.directory / ".profiles.lock", "a+b")
        self._closed = False
        try:
            self._acquire_lock()
            self._load()
        except BaseException:
            self._lock_file.close()
            self._closed = True
            raise

    def _acquire_lock(self):
        try:
            if os.name == "nt":
                import msvcrt
                self._lock_file.seek(0)
                self._lock_file.write(b"\0")
                self._lock_file.flush()
                self._lock_file.seek(0)
                msvcrt.locking(self._lock_file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, BlockingIOError):
            raise IdentityError("Hồ sơ đang được một cửa sổ camera khác sử dụng.") from None

    def _fernet(self, create=False):
        from cryptography.fernet import Fernet
        key_path = self.directory / ".profiles.key"
        if not key_path.exists() and create:
            # Never replace a key when encrypted profiles already exist.
            if self.path.exists():
                raise IdentityError("Thiếu khóa hồ sơ. Giữ nguyên dữ liệu để khôi phục từ bản sao lưu.")
            descriptor = os.open(key_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(descriptor, "wb") as file:
                file.write(Fernet.generate_key())
                file.flush()
                os.fsync(file.fileno())
        return Fernet(key_path.read_bytes())

    def _encrypt(self, data: bytes) -> bytes:
        if os.name == "nt":
            return b"CAMERA-DPAPI-1\n" + _dpapi(data)
        return b"CAMERA-FERNET-1\n" + self._fernet(create=True).encrypt(data)

    def _decrypt(self, data: bytes) -> bytes:
        header, payload = data.split(b"\n", 1)
        if header == b"CAMERA-DPAPI-1" and os.name == "nt":
            return _dpapi(payload, decrypt=True)
        if header == b"CAMERA-FERNET-1":
            return self._fernet().decrypt(payload)
        raise IdentityError("Định dạng bảo vệ hồ sơ không phù hợp với máy/tài khoản này.")

    def _load(self):
        if not self.path.exists():
            return
        try:
            value = json.loads(self._decrypt(self.path.read_bytes()))
            if value["schema_version"] != 1:
                raise ValueError
            profiles = []
            for item in value["profiles"]:
                if not item["name"].strip() or not item["model_version"]:
                    raise ValueError
                profiles.append(Profile(
                    str(item["id"]), str(item["name"]), normalize(item["embedding"]),
                    str(item["model_version"]), str(item["created_at"]),
                ))
            if len({p.id for p in profiles}) != len(profiles):
                raise ValueError
            main = value.get("main_profile_id")
            if main is not None and main not in {p.id for p in profiles}:
                raise ValueError
            self._profiles = profiles
            self._main_profile_id = main
        except Exception:
            raise IdentityError(
                "Không đọc được hồ sơ mã hóa. Dữ liệu được giữ nguyên; kiểm tra tài khoản Windows/bản sao lưu."
            ) from None

    def _save(self, profiles, main):
        if self._closed:
            raise IdentityError("Kho hồ sơ đã đóng.")
        value = {
            "schema_version": 1, "main_profile_id": main,
            "profiles": [{
                "id": p.id, "name": p.name, "embedding": p.embedding.tolist(),
                "model_version": p.model_version, "created_at": p.created_at,
            } for p in profiles],
        }
        temporary = None
        try:
            encrypted = self._encrypt(json.dumps(value, ensure_ascii=False).encode("utf-8"))
            descriptor, temporary = tempfile.mkstemp(prefix=".profiles-", dir=self.directory)
            with os.fdopen(descriptor, "wb") as file:
                file.write(encrypted)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, self.path)
            temporary = None
        except Exception:
            raise IdentityError("Không lưu được hồ sơ. Hồ sơ cũ được giữ nguyên.") from None
        finally:
            if temporary is not None:
                Path(temporary).unlink(missing_ok=True)
        self._profiles = profiles
        self._main_profile_id = main

    def profiles(self) -> list[Profile]:
        with self._mutex:
            return [Profile(p.id, p.name, p.embedding.copy(), p.model_version, p.created_at)
                    for p in self._profiles]

    @property
    def main_profile_id(self):
        with self._mutex:
            return self._main_profile_id

    def add(self, name, embedding, model_version, make_main=False) -> Profile:
        name = str(name).strip()
        if not name or len(name) > 100 or any(ord(c) < 32 for c in name):
            raise IdentityError("Tên phải có 1–100 ký tự, không có ký tự điều khiển.")
        if not model_version:
            raise IdentityError("Chưa xác định phiên bản mô hình embedding.")
        profile = Profile(
            str(uuid.uuid4()), name, normalize(embedding), str(model_version),
            datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        )
        with self._mutex:
            self._save(self._profiles + [profile], profile.id if make_main else self._main_profile_id)
        return Profile(profile.id, profile.name, profile.embedding.copy(), profile.model_version, profile.created_at)

    def delete(self, profile_id):
        with self._mutex:
            if not any(p.id == profile_id for p in self._profiles):
                raise IdentityError("Không tìm thấy hồ sơ cần xóa.")
            self._save([p for p in self._profiles if p.id != profile_id],
                       None if self._main_profile_id == profile_id else self._main_profile_id)

    def set_main(self, profile_id):
        with self._mutex:
            if profile_id is not None and not any(p.id == profile_id for p in self._profiles):
                raise IdentityError("Không tìm thấy hồ sơ người chính.")
            self._save(list(self._profiles), profile_id)

    def close(self):
        with self._mutex:
            if self._closed:
                return
            self._closed = True
            try:
                if os.name == "nt":
                    import msvcrt
                    self._lock_file.seek(0)
                    msvcrt.locking(self._lock_file.fileno(), msvcrt.LK_UNLCK, 1)
            finally:
                self._lock_file.close()


class EnrollmentCollector:
    """Accept separate, quality-gated samples of one consistent person.

    ``pose`` may contain yaw/pitch. It is used only for diversity, never identity.
    Identity continuity uses real embedding similarities against every sample.
    """
    def __init__(self, target_samples=15, min_samples=10, same_person_threshold=.42,
                 min_interval=.30, duplicate_similarity=.997):
        if not 2 <= min_samples <= target_samples <= 30:
            raise ValueError("Invalid enrollment sample count")
        self.target_samples = target_samples
        self.min_samples = min_samples
        self.same_person_threshold = same_person_threshold
        self.min_interval = min_interval
        self.duplicate_similarity = duplicate_similarity
        self.clear()

    def clear(self):
        self._samples: list[np.ndarray] = []
        self._poses: list[tuple[float, float] | None] = []
        self._last_at = -math.inf

    @property
    def count(self):
        return len(self._samples)

    @property
    def ready(self):
        return self.count >= self.min_samples

    def offer(self, embedding, quality_ok, face_count, at, box=None, pose=None):
        del box  # Geometry never authenticates identity.
        if self.count >= self.target_samples:
            return False, "Đã đủ mẫu. Bấm Lưu để tạo hồ sơ."
        if face_count != 1:
            return False, "Chỉ để một khuôn mặt trong khung."
        if not quality_ok or embedding is None:
            return False, "Đưa mặt gần hơn, đủ sáng và giữ ảnh rõ."
        if not math.isfinite(at) or at - self._last_at < self.min_interval:
            return False, "Giữ ổn định, rồi đổi góc nhẹ."
        try:
            vector = normalize(embedding)
        except (IdentityError, ValueError, TypeError):
            return False, "Chưa lấy được embedding hợp lệ."
        orientation = None
        if pose is not None:
            try:
                orientation = (float(pose["yaw"]), float(pose["pitch"]))
                if not all(math.isfinite(x) for x in orientation):
                    orientation = None
            except (KeyError, ValueError, TypeError):
                pass
        if self._samples:
            if vector.shape != self._samples[0].shape:
                return False, "Mô hình embedding đã thay đổi; hãy Hủy và thu lại."
            similarities = np.array([float(vector @ s) for s in self._samples])
            if float(similarities.min()) < self.same_person_threshold:
                return False, "Có thể đã đổi người. Hãy Hủy và thu lại cùng một người."
            # Near-identical embeddings + similar pose do not count as new samples.
            for similarity, old_pose in zip(similarities, self._poses):
                pose_changed = (orientation is not None and old_pose is not None and
                                max(abs(a - b) for a, b in zip(orientation, old_pose)) >= 3.0)
                if similarity >= self.duplicate_similarity and not pose_changed:
                    return False, "Đổi góc mặt nhẹ để mẫu đa dạng hơn."
        self._samples.append(vector.copy())
        self._poses.append(orientation)
        self._last_at = at
        return True, f"Đã thu {self.count}/{self.target_samples} mẫu. Nhìn thẳng hoặc đổi góc nhẹ."

    def embedding(self):
        if not self.ready:
            raise IdentityError("Chưa đủ mẫu hợp lệ để lưu hồ sơ.")
        return normalize(np.mean(self._samples, axis=0))
