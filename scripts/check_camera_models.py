"""Verify/download pinned camera weights without importing the legacy backend.

Default/--check only checks all hashes. --download uses the existing immutable
downloader and never replaces a bad existing model without explicit --repair.
--smoke additionally runs each real model on a blank image (not an accuracy test).
"""

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def check_models(directory: Path) -> bool:
    manifest = json.loads((ROOT / "models/manifest.json").read_text(encoding="utf-8"))
    valid = True
    for model in manifest["models"]:
        name = model["file"]
        if Path(name).name != name:
            raise ValueError("Unsafe filename in model manifest")
        path = directory / name
        if not path.is_file():
            print(f"MISSING {model['module']}: {name}")
            valid = False
        elif hashlib.sha256(path.read_bytes()).hexdigest() != model["sha256"]:
            print(f"CHECKSUM_MISMATCH {model['module']}: {name}; existing file preserved")
            valid = False
        else:
            print(f"VERIFIED {model['module']}: {name}")
    return valid


def smoke_models(directory: Path) -> bool:
    """Exercise runtime constructors and inference, reporting every module error."""
    import cv2
    import numpy as np

    cv2.setNumThreads(2)
    blank = np.zeros((480, 640, 3), dtype=np.uint8)
    valid = True
    for module in ("face_detection", "face_embedding", "object_detection", "head_landmarks"):
        try:
            if module == "face_detection":
                detector = cv2.FaceDetectorYN_create(
                    str(directory / "face_detection_yunet_2023mar.onnx"), "", (640, 480),
                    backend_id=cv2.dnn.DNN_BACKEND_OPENCV, target_id=cv2.dnn.DNN_TARGET_CPU,
                )
                detector.detect(blank)
            elif module == "face_embedding":
                recognizer = cv2.FaceRecognizerSF_create(
                    str(directory / "face_recognition_sface_2021dec.onnx"), "",
                    backend_id=cv2.dnn.DNN_BACKEND_OPENCV, target_id=cv2.dnn.DNN_TARGET_CPU,
                )
                feature = recognizer.feature(np.zeros((112, 112, 3), dtype=np.uint8))
                if feature.shape != (1, 128) or not np.isfinite(feature).all():
                    raise RuntimeError("Unexpected SFace output")
            elif module == "object_detection":
                net = cv2.dnn.readNetFromONNX(str(directory / "object_detection_nanodet_2022nov.onnx"))
                net.setPreferableBackend(cv2.dnn.DNN_BACKEND_OPENCV)
                net.setPreferableTarget(cv2.dnn.DNN_TARGET_CPU)
                net.setInput(cv2.dnn.blobFromImage(np.zeros((416, 416, 3), dtype=np.float32)))
                outputs = net.forward(net.getUnconnectedOutLayersNames())
                if not outputs or not all(np.isfinite(out).all() for out in outputs):
                    raise RuntimeError("Unexpected NanoDet output")
            else:
                import mediapipe as mp
                options = mp.tasks.vision.FaceLandmarkerOptions(
                    base_options=mp.tasks.BaseOptions(model_asset_path=str(directory / "face_landmarker.task")),
                    running_mode=mp.tasks.vision.RunningMode.IMAGE,
                    num_faces=3,
                    output_facial_transformation_matrixes=True,
                )
                with mp.tasks.vision.FaceLandmarker.create_from_options(options) as landmarker:
                    landmarker.detect(mp.Image(image_format=mp.ImageFormat.SRGB, data=blank))
            print(f"INFERENCE_OK {module} (CPU, blank input; no quality/accuracy claim)")
        except Exception as exc:
            print(f"MODEL_ERROR {module}: {type(exc).__name__}: {exc}", file=sys.stderr)
            valid = False
    return valid


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--check", action="store_true", help="Only verify all model hashes (default)")
    action.add_argument("--download", action="store_true", help="Download missing weights using pinned URLs")
    parser.add_argument("--repair", action="store_true", help="With --download, back up bad weights before replacement")
    parser.add_argument("--smoke", action="store_true", help="Also call each real model on a blank input")
    parser.add_argument("--directory", type=Path, default=ROOT / "models")
    args = parser.parse_args()
    if args.repair and not args.download:
        parser.error("--repair requires --download")
    if args.download:
        from download_models import download
        try:
            download(args.directory, repair=args.repair)
        except Exception as exc:
            print(f"DOWNLOAD_ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
            check_models(args.directory)
            return 1
    if not check_models(args.directory):
        print("Run with --download for missing weights; --download --repair preserves bad weights as backups.")
        return 1
    if args.smoke and not smoke_models(args.directory):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
