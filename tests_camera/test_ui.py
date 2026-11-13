"""Native Tk widget integration, not desktop automation or visual acceptance QA."""
from __future__ import annotations

import copy
import gc
import subprocess
import sys
import time
import tkinter as tk
from pathlib import Path
from tkinter import ttk

import cv2
import numpy as np
import pytest

from camera_app.camera import Frame
from camera_app.ui import CameraWindow, fit_geometry, map_box

ROOT = Path(__file__).resolve().parents[1]


class PreviewSource:
    video = None

    def __init__(self):
        path = ROOT / "test_data/portrait.jpg"
        self.image = cv2.imread(str(path)) if path.exists() else None
        if self.image is None:
            # Public fixture is optional; an actual image buffer still exercises
            # OpenCV -> Pillow -> native Tk PhotoImage without any model outputs.
            self.image = np.full((240, 320, 3), (45, 90, 120), dtype=np.uint8)
        self.image = cv2.resize(self.image, (320, 240))
        self.error = None

    def snapshot(self):
        return (None if self.error else Frame(1, time.monotonic(), self.image)), self.error


class WidgetRuntime:
    """Fake boundary for testing widget commands; never creates a VideoCapture."""
    def __init__(self):
        self.source = PreviewSource()
        self.cfg = {"result_ttl_seconds": 1.5, "mirror_preview": True, "enrollment_samples": 15}
        self.commands = []
        self.stop_calls = 0
        self.is_closed = False
        self.state = {
            "faces": [{"box": (80, 40, 160, 150), "name": "Người", "primary": True}],
            "objects": [{"box": (200, 80, 240, 170), "class_id": 67, "name": "Điện thoại"}],
            "image_size": (320, 240), "main_name": "Người", "main_visible": True,
            "status": "Đang quan sát", "pose": {"head_direction": "Thẳng", "gaze_direction": "Chưa xác định"},
            "events": {"head_away_seconds": 1.2, "absence_seconds": 0, "turn_count": 1, "alerts": []},
            "health": {}, "enrollment": None, "saved_id": 0, "notice_id": 0, "notice": "",
            "profiles": [{"id": "profile-a", "name": "Nguyễn Duy Việt", "created_at": "2026-11-13T01:00:00Z"},
                         {"id": "profile-b", "name": "Đặng Ánh", "created_at": "2026-11-13T02:00:00Z"}],
            "main_profile_id": "profile-a",
        }

    def snapshot(self):
        result = copy.deepcopy(self.state)
        result["at"] = time.monotonic()
        result.setdefault("object_at", result["at"])
        return result

    def command(self, action, **args):
        self.commands.append((action, args))
        return True

    def stop(self):
        self.stop_calls += 1
        self.is_closed = True

    def closed(self):
        return self.is_closed


def descend(widget):
    for child in widget.winfo_children():
        yield child
        yield from descend(child)


def button(widget, text):
    return next(child for child in descend(widget)
                if isinstance(child, ttk.Button) and child.cget("text") == text)


@pytest.fixture
def main_thread_tk_cleanup():
    # Tcl interpreters/Variables form Python cycles. Collect them only on this
    # main thread, never opportunistically from a later model worker thread.
    gc.collect()
    yield
    gc.collect()


@pytest.fixture
def native_window(monkeypatch, main_thread_tk_cleanup):
    runtime = WidgetRuntime()
    # Any accidental camera creation by an enrollment UI path fails this test.
    def forbidden_capture(*args, **kwargs):
        raise AssertionError("Widget/enrollment paths must use the existing source")
    monkeypatch.setattr(cv2, "VideoCapture", forbidden_capture)
    root = tk.Tk()
    callback_errors = []
    root.report_callback_exception = lambda *args: callback_errors.append(args)
    window = CameraWindow(root, runtime)
    root.update_idletasks()
    yield root, window, runtime, callback_errors
    if not window.closing:
        window.close()
    window.photo = None


def pump(root, milliseconds=180):
    root.after(milliseconds, root.quit)
    root.mainloop()


def test_letterbox_resize_and_mirror_box_preserve_coordinates():
    scale, ox, oy, width, height = fit_geometry(640, 480, 1000, 600)
    assert (scale, ox, oy, width, height) == (1.25, 100, 0, 800, 600)
    assert map_box((100, 50, 200, 150), 640, scale, ox, oy, False) == (225, 62.5, 350, 187.5)
    assert map_box((100, 50, 200, 150), 640, scale, ox, oy, True) == (650, 62.5, 775, 187.5)
    _, ox, oy, width, height = fit_geometry(640, 480, 640, 1000)
    assert (ox, oy, width, height) == (0, 260, 640, 480)


def test_scheduled_refresh_creates_native_image_and_unicode_overlays(native_window):
    root, window, runtime, errors = native_window
    pump(root)
    assert window.photo is not None
    assert window.canvas.itemcget(window.image_item, "image")
    texts = [window.canvas.itemcget(item, "text") for item in window.canvas.find_withtag("overlay")
             if window.canvas.type(item) == "text"]
    assert any("Người chính: Người" in text for text in texts)
    assert any("Điện thoại" in text for text in texts)
    assert any("1,2 giây" in text for text in texts)
    assert len([child for child in root.winfo_children() if isinstance(child, tk.Canvas)]) == 1
    assert not errors
    assert runtime.commands == []


def test_stale_objects_are_hidden_despite_current_camera_preview(native_window):
    root, window, runtime, errors = native_window
    runtime.state["object_at"] = time.monotonic() - runtime.cfg["result_ttl_seconds"] - 1
    pump(root)
    assert window.photo is not None
    texts = [window.canvas.itemcget(item, "text") for item in window.canvas.find_withtag("overlay")
             if window.canvas.type(item) == "text"]
    assert not any("Điện thoại" in text for text in texts)
    assert any("Người chính: Người" in text for text in texts)
    assert not errors


def test_enrollment_explicit_start_unicode_name_save_gating_and_cancel(native_window):
    root, window, runtime, errors = native_window
    window.open_enrollment()
    assert runtime.commands == [("enroll_open", {})]
    assert str(window.save_samples.cget("state")) == "disabled"
    window.start_samples.invoke()
    assert runtime.commands == [("enroll_open", {})]
    assert "1–100" in window.enroll_message.get()
    window.enroll_name.set(" Nguyễn Duy Việt ")
    window.start_samples.invoke()
    assert runtime.commands[-1] == ("enroll_start", {"name": "Nguyễn Duy Việt"})
    runtime.state["enrollment"] = {"count": 5, "ready": False, "message": "Cần thêm góc mặt"}
    pump(root)
    assert window.progress["value"] == 5
    assert str(window.save_samples.cget("state")) == "disabled"
    runtime.state["enrollment"] = {"count": 15, "ready": True, "message": "Đủ mẫu"}
    pump(root)
    assert str(window.save_samples.cget("state")) == "normal"
    window.make_main.set(True)
    window.save_samples.invoke()
    assert runtime.commands[-1] == ("enroll_save", {"make_main": True})
    # No save-success notification: closing must still discard unsaved samples.
    button(window.dialog, "Hủy").invoke()
    assert runtime.commands[-1] == ("enroll_cancel", {})
    assert window.dialog is None and window.enroll_dialog is None
    assert not errors


def test_save_success_closes_dialog_without_sending_cancel(native_window):
    root, window, runtime, errors = native_window
    window.open_enrollment()
    runtime.state["saved_id"] = 1
    pump(root)
    assert window.dialog is None and window.enroll_dialog is None
    assert runtime.commands == [("enroll_open", {})]
    assert not errors


def test_profile_choices_and_calibration_commands(native_window):
    _, window, runtime, errors = native_window
    window.choose_main()
    listing = next(child for child in descend(window.dialog) if isinstance(child, tk.Listbox))
    assert "Nguyễn Duy Việt" in listing.get(0)
    assert "Đặng Ánh" in listing.get(1)
    listing.selection_set(1)
    button(window.dialog, "Chọn người này").invoke()
    assert runtime.commands[-1] == ("select_main", {"profile_id": "profile-b"})
    assert window.dialog is None
    window.choose_main()
    button(window.dialog, "Theo dõi tạm “Người”").invoke()
    assert runtime.commands[-1] == ("select_main", {"profile_id": None})
    window.calibrate()
    assert runtime.commands[-1] == ("calibrate", {})
    assert window.dialog.title() == "Căn chỉnh đầu và mắt"
    button(window.dialog, "Trở lại camera").invoke()
    assert window.dialog is None
    assert not errors


def test_profile_delete_uses_explicit_selected_id_and_confirmation(native_window, monkeypatch):
    _, window, runtime, errors = native_window
    window.open_profiles()
    listing = next(child for child in descend(window.dialog) if isinstance(child, tk.Listbox))
    listing.selection_set(0)
    monkeypatch.setattr("camera_app.ui.messagebox.askyesno", lambda *a, **kw: False)
    button(window.dialog, "Xóa hồ sơ đã chọn").invoke()
    assert not runtime.commands
    monkeypatch.setattr("camera_app.ui.messagebox.askyesno", lambda *a, **kw: True)
    button(window.dialog, "Xóa hồ sơ đã chọn").invoke()
    assert runtime.commands == [("delete", {"profile_id": "profile-a"})]
    assert listing.size() == 1 and "Đặng Ánh" in listing.get(0)
    assert not errors


def test_camera_failure_clears_old_image_and_close_stops_runtime(native_window):
    root, window, runtime, errors = native_window
    pump(root)
    assert window.canvas.itemcget(window.image_item, "image")
    runtime.source.error = "Mất camera thử nghiệm"
    pump(root)
    assert window.canvas.itemcget(window.image_item, "image") == ""
    window.close()
    assert runtime.stop_calls == 1 and runtime.is_closed and window.closing
    assert not root.tk.call("info", "commands", ".")
    assert not errors


@pytest.mark.real
def test_native_tk_with_real_runtime_video_models_and_release(tmp_path, main_thread_tk_cleanup):
    """Run the actual reader/AI/Tk pipeline; static public video is not live QA."""
    if not (ROOT / "test_data/portrait.jpg").exists():
        pytest.skip("Run scripts/prepare_smoke_assets.py for the public fixture")
    required = ("face_detection_yunet_2023mar.onnx", "face_recognition_sface_2021dec.onnx",
                "face_landmarker.task", "object_detection_nanodet_2022nov.onnx")
    if not all((ROOT / "models" / name).exists() for name in required):
        pytest.skip("Real-model integration requires the downloaded model manifest")
    original = cv2.imread(str(ROOT / "test_data/portrait.jpg"))
    h, w = original.shape[:2]
    scale = min(640 / w, 480 / h)
    resized = cv2.resize(original, (round(w * scale), round(h * scale)))
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    x, y = (640-resized.shape[1]) // 2, (480-resized.shape[0]) // 2
    frame[y:y+resized.shape[0], x:x+resized.shape[1]] = resized
    video = tmp_path / "native_tk_fixture.avi"
    writer = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*"MJPG"), 15, (640, 480))
    assert writer.isOpened(), "The test video codec must be available"
    try:
        for _ in range(15 * 12):
            writer.write(frame)
    finally:
        writer.release()
    # A native Tk interpreter belongs to its creation thread. Isolate this
    # application's interpreter from earlier widget tests and their Python GC
    # cycles while retaining strict assertions and native-crash return codes.
    code = ("import sys; from tests_camera.test_ui import _run_native_runtime_check; "
            "_run_native_runtime_check(sys.argv[1], sys.argv[2])")
    result = subprocess.run([sys.executable, "-X", "faulthandler", "-c", code,
                             str(video), str(tmp_path / "profiles")], cwd=ROOT,
                            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=35)
    assert result.returncode == 0, f"Native Tk runtime exited {result.returncode}\n{result.stdout}\n{result.stderr}"
    assert "NATIVE_TK_REAL_RUNTIME_OK" in result.stdout


def _run_native_runtime_check(video, data_dir):
    """Child-process entry point; any assertion/native crash fails its parent."""
    from camera_app.config import load_config
    from camera_app.runtime import CameraRuntime

    gc.collect()
    runtime = CameraRuntime(load_config(), ROOT / "models", Path(data_dir), video=Path(video))
    root = tk.Tk()
    errors, observations = [], []
    root.report_callback_exception = lambda *args: errors.append(args)
    window = CameraWindow(root, runtime)
    gc.collect()
    runtime.start()

    def record():
        state = runtime.snapshot()
        observations.append((window.photo is not None, len(state.get("faces", [])), state.get("health")))
        if not window.closing:
            root.after(100, record)
    root.after(100, record)
    root.after(8000, window.close)
    try:
        root.mainloop()
    finally:
        runtime.stop()
        runtime.join(5)
        if not window.closing:
            window.close()
    assert not errors
    assert any(image_exists and faces >= 1 for image_exists, faces, _ in observations)
    assert any(health and all(value is None for value in health.values()) for _, _, health in observations)
    assert runtime.closed() and runtime.source.capture is None
    assert not runtime.thread.is_alive() and not runtime.source.thread.is_alive()
    assert not root.tk.call("info", "commands", ".")
    window.photo = None
    gc.collect()
    print("NATIVE_TK_REAL_RUNTIME_OK", flush=True)
