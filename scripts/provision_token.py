"""Local administrator tool. Writes a service/admin token to a private file, never stdout."""

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from proctor.config import Settings
from proctor.security import Security
from proctor.store import SQLiteStore

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--actor", required=True)
    parser.add_argument("--role", choices=["admin", "service"], default="service")
    parser.add_argument("--ttl", type=int, default=900)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not 60 <= args.ttl <= 86400:
        raise SystemExit("TTL must be 60..86400 seconds")
    settings = Settings()
    store = SQLiteStore(settings.data_dir / "proctor.sqlite3")
    security = Security(settings.data_dir, store)
    token = security.issue(args.actor, args.role, ttl=args.ttl)
    fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as file:
        file.write(token)
    store.close()
    print("Token file created. Keep it outside shared source archives.")
