#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
if [ ! -f "${AIDLP_JEV_TOKEN_FILE:-$PWD/.local/jev-state/api.token}" ]; then
  echo "Jev API token file missing. Start scripts/start-jev.sh first, or set AIDLP_JEV_TOKEN_FILE for the remote API." >&2
  exit 1
fi
export PYTHONPATH="$PWD/judge"
exec judge/.venv/bin/python -m uvicorn aidlp_judge.api:app --host 127.0.0.1 --port 8310 --no-access-log
