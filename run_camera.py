"""Open the standalone CPU camera window; no server or browser involved."""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from camera_app.config import ROOT, load_config


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--camera", type=int, help="Camera index; default 0")
    parser.add_argument("--video", type=Path, help="Replay a local test video instead of a live camera")
    parser.add_argument("--config", type=Path)
    parser.add_argument("--models", type=Path, default=ROOT / "models")
    parser.add_argument("--data-dir", type=Path, default=ROOT / ".camera-data")
    parser.add_argument("--headless", action="store_true", help="Exercise the same real pipeline without a window")
    parser.add_argument("--seconds", type=float, default=15, help="Headless run duration")
    parser.add_argument("--report", type=Path, help="New JSON diagnostic report; contains no images/embeddings/names")
    parser.add_argument("--smoke-ui-seconds", type=float, help="Test only: close the real window after this many seconds")
    args = parser.parse_args()
    if args.video and not args.video.is_file():
        parser.error("Video không tồn tại: " + str(args.video))
    if args.report and args.report.exists():
        parser.error("Không ghi đè báo cáo đã có: " + str(args.report))
    cfg = load_config(args.config)
    from camera_app.runtime import CameraRuntime
    runtime = CameraRuntime(cfg, args.models, args.data_dir, args.video, args.camera)
    root = None
    try:
        if not args.headless:
            import tkinter as tk
            try:
                root = tk.Tk()
            except tk.TclError:
                print("Không mở được Tkinter. Kiểm tra Python 3.12 x64 có tcl/tk, phiên desktop và quyền chạy ứng dụng.", file=sys.stderr)
                return 1
            from camera_app.ui import CameraWindow
            window = CameraWindow(root, runtime)
            if args.smoke_ui_seconds:
                root.after(max(100, round(args.smoke_ui_seconds*1000)), window.close)
        runtime.start()
        if root is not None:
            root.mainloop()
            return 0
        deadline = time.monotonic()+max(0.1, args.seconds)
        observations, max_faces, classes, modules = set(), 0, set(), {}
        while time.monotonic() < deadline:
            state = runtime.snapshot()
            if state["at"] is not None:
                observations.add(state["at"])
                max_faces = max(max_faces, len(state["faces"]))
                classes.update(o["name"] for o in state["objects"])
            modules = state.get("health", {})
            time.sleep(.05)
        packet, source_error = runtime.source.snapshot()
        state = runtime.snapshot()
        report = {"config_version": cfg["version"], "source": "video" if args.video else "camera",
                  "fresh_observations": len(observations), "max_detected_faces": max_faces,
                  "observed_object_classes": sorted(classes), "module_errors": modules,
                  "source_error": source_error, "worker_status": state["status"],
                  "scope": "Functional runtime smoke only; no accuracy/FPS/identity claim"}
        if args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            with args.report.open("x", encoding="utf-8") as file:
                json.dump(report, file, ensure_ascii=False, indent=2)
        print(json.dumps(report, ensure_ascii=True, indent=2))
        return 0 if observations else 2
    finally:
        runtime.stop()
        if runtime.thread.ident is not None:
            runtime.join(6)
        if root is not None:
            try:
                root.destroy()
            except Exception:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
