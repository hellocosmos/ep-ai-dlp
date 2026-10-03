"""Private stdio inspection worker. Stdout is a JSON-lines protocol only."""
from __future__ import annotations

import argparse
import json
import sys

from .inspection import inspect_request
from .store import Store

# Worst-case JSON escaping of a bounded 1 MiB UTF-8 body plus envelope.
MAX_LINE = 7 * 1024 * 1024


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    args = parser.parse_args()
    store = Store(args.db)
    try:
        while True:
            line = sys.stdin.buffer.readline(MAX_LINE + 1)
            if not line:
                return 0
            if len(line) > MAX_LINE or not line.endswith(b"\n"):
                return 2  # Stop: never interpret trailing fragments as another request.
            req = None
            try:
                req = json.loads(line)
                result = inspect_request(store, req)
            except Exception:
                result = {"id": req.get("id", "") if isinstance(req, dict) else "",
                          "action": "block", "reason": "inspection_error",
                          "rules": [], "event_id": None}
            sys.stdout.buffer.write(json.dumps(result, ensure_ascii=True).encode() + b"\n")
            sys.stdout.buffer.flush()
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
