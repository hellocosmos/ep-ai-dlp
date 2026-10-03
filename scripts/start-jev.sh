#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
export PYTHONPATH="$PWD/judge"
# Keep inference loopback-only; put a TLS reverse proxy in front for remote GPU use.
exec judge/.venv/bin/python -m uvicorn aidlp_judge.jev_api:app --host 127.0.0.1 --port "${AIDLP_JEV_PORT:-8311}" --no-access-log
