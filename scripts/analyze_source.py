"""Offline CPU observation runner. Does not create an exam identity or auto-enroll a face."""

import argparse
import json
import platform
import sys
import time
from dataclasses import asdict
from pathlib import Path

import cv2
import numpy as np
import psutil

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from proctor.config import Settings
from proctor.domain import utcnow
from proctor.event_engine import EventEngine
from proctor.models import ModelSuite


def run(args):
    settings = Settings()
    cfg = settings.load_config()
    models = ModelSuite(settings.models_dir)
    missing = [m for m in cfg.required_modules if models.health[m]["status"] != "AVAILABLE"]
    if missing:
        raise SystemExit("MODEL_UNAVAILABLE: " + ",".join(missing))
    cap = cv2.VideoCapture(args.camera if args.camera is not None else str(args.video))
    if not cap.isOpened():
        models.close()
        raise SystemExit("SOURCE_UNAVAILABLE: cannot open camera/video")
    engine = EventEngine(cfg)
    baseline = json.loads(args.baseline.read_text()) if args.baseline else None
    args.output.parent.mkdir(parents=True, exist_ok=True)
    summary = {
        "host": platform.platform(),
        "processor": platform.processor(),
        "python": platform.python_version(),
        "runtime": "CPU",
        "config_hash": cfg.digest,
        "config_version": cfg.version,
        "models": models.versions,
        "source_type": "camera" if args.camera is not None else "video_replay",
        "identity": "NOT_EVALUATED",
        "head_pose": "CALIBRATED_EXTERNAL" if baseline else "UNKNOWN_NO_BASELINE",
        "temperature_c": None,
        "timings_ms": {},
        "frames": 0,
        "created_events": 0,
        "power_note": args.power_note,
    }
    times = []
    start = time.monotonic()
    fps = cap.get(cv2.CAP_PROP_FPS) or 25
    source_index = 0
    next_sample = 0.0
    last_object = -1e9
    # Exclusive output avoids overwriting someone's previous test results.
    try:
        with open(args.output, "x", encoding="utf-8") as file:
            while time.monotonic() - start < args.seconds:
                ok, image = cap.read()
                if not ok:
                    break
                source_t = source_index / fps
                source_index += 1
                if args.camera is None and source_t < next_sample:
                    continue
                if args.realtime and args.camera is None:
                    wait = start + source_t - time.monotonic()
                    if wait > 0:
                        time.sleep(min(wait, 0.2))
                        while time.monotonic() < start + source_t:
                            time.sleep(0.01)
                if args.camera is not None and time.monotonic() - start < next_sample:
                    continue
                next_sample += 0.2
                if image.shape[1] > 640 or image.shape[0] > 480:
                    ratio = min(640 / image.shape[1], 480 / image.shape[0])
                    image = cv2.resize(image, (round(image.shape[1] * ratio), round(image.shape[0] * ratio)))
                # Offline replay time is media time, explicitly not proof of a live camera.
                at = time.monotonic() - start if args.camera is not None else source_t
                before = time.perf_counter()
                o, _ = models.observe(
                    image,
                    cfg,
                    at,
                    utcnow(),
                    summary["frames"] + 1,
                    baseline,
                    (at - last_object) * 1000 >= cfg.object_interval_ms,
                )
                if (at - last_object) * 1000 >= cfg.object_interval_ms:
                    last_object = at
                changes = engine.observe(o)
                duration = (time.perf_counter() - before) * 1000
                times.append(duration)
                summary["frames"] += 1
                summary["created_events"] += sum(c.action == "create" for c in changes)
                record = {
                    "seq": o.seq,
                    "source_time_s": at,
                    "observed_at": o.utc,
                    "quality": o.data["quality"],
                    "objects": o.data["objects"],
                    "signals": o.data["signals"],
                    "inference_ms": round(duration, 2),
                    "cpu_percent": psutil.cpu_percent(),
                    "rss_bytes": psutil.Process().memory_info().rss,
                    "changes": [asdict(c) for c in changes],
                }
                file.write(json.dumps(record, ensure_ascii=False) + "\n")
                if args.preview:
                    cv2.imshow("Exam Watch - offline observations (q: stop)", image)
                    if cv2.waitKey(1) & 0xFF == ord("q"):
                        break
            summary["elapsed_s"] = round(time.monotonic() - start, 3)
            summary["timings_ms"] = (
                {"p50": float(np.percentile(times, 50)), "p95": float(np.percentile(times, 95))}
                if times
                else {}
            )
            file.write(json.dumps({"summary": summary}, ensure_ascii=False) + "\n")
    finally:
        cap.release()
        models.close()
        if args.preview:
            cv2.destroyAllWindows()
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("--camera", type=int)
    group.add_argument("--video", type=Path)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--seconds", type=float, default=60)
    p.add_argument("--realtime", action="store_true")
    p.add_argument("--preview", action="store_true")
    p.add_argument(
        "--baseline", type=Path, help="Previously calibrated yaw/pitch/iris_x JSON, not an identity profile"
    )
    p.add_argument("--power-note", default="NOT_RECORDED")
    run(p.parse_args())
