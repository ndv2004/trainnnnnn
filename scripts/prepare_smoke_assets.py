"""Fetch public upstream fixtures for local testing only. No test faces are bundled."""

import argparse
import hashlib
import json
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--videos",
        action="store_true",
        help="Also generate browser/video replay fixtures; requires ffmpeg on PATH",
    )
    args = parser.parse_args()
    output = ROOT / "test_data"
    output.mkdir(exist_ok=True)
    for fixture in json.loads((ROOT / "tests/fixtures_manifest.json").read_text()):
        dest = output / fixture["file"]
        if dest.exists():
            raw = dest.read_bytes()
        else:
            raw = urllib.request.urlopen(fixture["url"], timeout=60).read()
        if hashlib.sha256(raw).hexdigest() != fixture["sha256"]:
            raise SystemExit("FIXTURE_CHECKSUM_MISMATCH: " + fixture["file"])
        if not dest.exists():
            dest.write_bytes(raw)
        print("VERIFIED", fixture["file"])
    if args.videos:
        import shutil
        import subprocess

        import cv2
        import numpy as np

        if not shutil.which("ffmpeg"):
            raise SystemExit("Optional video fixtures require ffmpeg on PATH; image/model tests do not.")
        original = cv2.imread(str(output / "portrait.jpg"))
        h, w = original.shape[:2]
        scale = min(640 / w, 480 / h)
        small = cv2.resize(original, (round(w * scale), round(h * scale)))
        reference = np.zeros((480, 640, 3), np.uint8)
        x, y = (640 - small.shape[1]) // 2, (480 - small.shape[0]) // 2
        reference[y : y + small.shape[0], x : x + small.shape[1]] = small
        absence = np.random.default_rng(17).integers(50, 210, (480, 640, 3), np.uint8)
        for name, image in (("reference", reference), ("absence", absence)):
            jpg, video = output / (name + ".jpg"), output / (name + ".webm")
            if not jpg.exists():
                cv2.imwrite(str(jpg), image)
            if not video.exists():
                subprocess.run(
                    [
                        "ffmpeg",
                        "-hide_banner",
                        "-loglevel",
                        "error",
                        "-n",
                        "-loop",
                        "1",
                        "-i",
                        str(jpg),
                        "-t",
                        "8",
                        "-r",
                        "10",
                        "-c:v",
                        "libvpx-vp9",
                        "-pix_fmt",
                        "yuv420p",
                        str(video),
                    ],
                    check=True,
                )
