"""Temporary real TCP server and client in the same environment; real model readiness."""

import json
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path

import httpx
import uvicorn

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from proctor.api import create_app
from proctor.config import Settings


def run():
    report = {"scope": "Real TCP/HTTP liveness, readiness, HTML and OpenAPI with CPU model loading"}
    server = None
    try:
        with tempfile.TemporaryDirectory(prefix="exam-watch-tcp-") as tmp:
            settings = Settings(Path(tmp))
            settings.config_path = ROOT / "config/cpu-v1.json"
            app = create_app(settings)
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
            server = uvicorn.Server(
                uvicorn.Config(app, host="127.0.0.1", port=port, access_log=False, log_level="error")
            )
            thread = threading.Thread(target=lambda: server.run(sockets=[sock]), daemon=True)
            thread.start()
            deadline = time.monotonic() + 20
            while not server.started and thread.is_alive() and time.monotonic() < deadline:
                time.sleep(0.05)
            if not server.started:
                raise RuntimeError("SERVER_DID_NOT_START")
            with httpx.Client(base_url=f"http://127.0.0.1:{port}", trust_env=False, timeout=5) as client:
                for route in ("/health/live", "/health/ready", "/", "/openapi.json", "/assets/app.js"):
                    response = client.get(route)
                    assert response.status_code == 200, (route, response.status_code)
                report.update(status="PASS", readiness="READY", endpoints=5)
            server.should_exit = True
            thread.join(timeout=10)
            assert not thread.is_alive(), "SERVER_DID_NOT_STOP"
    except (OSError, RuntimeError) as exc:
        report.update(status="BLOCKED", reason=f"{type(exc).__name__}: {exc}")
        if server:
            server.should_exit = True
    (ROOT / "reports/http-smoke.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    return 0 if report["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(run())
