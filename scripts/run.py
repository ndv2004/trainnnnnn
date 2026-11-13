import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

if __name__ == "__main__":
    if sys.version_info[:2] != (3, 12):
        raise SystemExit("Use Python 3.12 (64-bit); the Windows dependency set targets this version.")
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    if os.getenv("PROCTOR_DEMO", "1") == "1" and args.host not in ("127.0.0.1", "localhost", "::1"):
        raise SystemExit("LOCALHOST_ONLY: demo must bind to loopback")
    import uvicorn

    print(f"Exam Watch: http://{args.host}:{args.port} — standalone CPU demo; Ctrl+C to stop")
    uvicorn.run(
        "proctor.main:app",
        host=args.host,
        port=args.port,
        workers=1,
        access_log=False,
        log_level="warning",
        limit_concurrency=32,
        ws="websockets-sansio",
        timeout_keep_alive=5,
    )
