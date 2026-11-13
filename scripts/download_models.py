"""Download immutable model URLs, verify locked SHA256 before atomic installation."""

import argparse
import base64
import hashlib
import json
import os
import struct
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def crc32c(raw):
    crc = 0xFFFFFFFF
    for byte in raw:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ (0x82F63B78 if crc & 1 else 0)
    return base64.b64encode(struct.pack(">I", crc ^ 0xFFFFFFFF)).decode()


def download(directory=ROOT / "models", verify_only=False, repair=False):
    directory = Path(directory)
    manifest = json.loads((ROOT / "models/manifest.json").read_text())
    directory.mkdir(parents=True, exist_ok=True)
    for model in manifest["models"]:
        name = model["file"]
        if Path(name).name != name:
            raise ValueError("Unsafe model filename")
        path = directory / name
        if path.exists():
            if hashlib.sha256(path.read_bytes()).hexdigest() == model["sha256"]:
                print("VERIFIED", name)
                continue
            if not repair:
                raise RuntimeError(
                    f"CHECKSUM_MISMATCH: {name}; preserve/rename it or use --repair (creates a backup)"
                )
        if verify_only:
            raise RuntimeError(f"WEIGHTS_MISSING: {name}")
        print("DOWNLOADING", name, flush=True)
        with urllib.request.urlopen(model["url"], timeout=120) as response:
            raw = response.read(100 * 1024 * 1024 + 1)
        if len(raw) > 100 * 1024 * 1024 or hashlib.sha256(raw).hexdigest() != model["sha256"]:
            raise RuntimeError(f"CHECKSUM_MISMATCH: downloaded {name}; existing file preserved")
        upstream = model.get("upstream_hash", "")
        if "crc32c=" in upstream and crc32c(raw) != upstream.split("crc32c=")[1].split(",")[0]:
            raise RuntimeError(f"UPSTREAM_CRC_MISMATCH: {name}")
        tmp = path.with_suffix(path.suffix + ".download")
        with open(tmp, "wb") as f:
            f.write(raw)
            f.flush()
            os.fsync(f.fileno())
        if path.exists():
            os.replace(path, path.with_suffix(path.suffix + f".backup-{time.time_ns()}"))
        os.replace(tmp, path)
        print("VERIFIED", name)
    if directory.resolve() != (ROOT / "models").resolve():
        (directory / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=ROOT / "models")
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument("--repair", action="store_true")
    args = parser.parse_args()
    download(args.directory, args.verify_only, args.repair)
