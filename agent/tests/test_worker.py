import json
from pathlib import Path
import subprocess
import sys


def test_worker_protocol_blocks_errors_and_keeps_stdout_clean(tmp_path):
    worker = subprocess.run(
        [sys.executable, "-m", "aidlp.worker", "--db", str(tmp_path / "worker.db")],
        input='not-json\n' + json.dumps(dict(
            id="req-1", host="localhost", method="POST", path="/echo",
            content_type="application/json", body='{"prompt":"alice@example.com"}'
        )) + '\n',
        text=True, capture_output=True, timeout=5,
        cwd=Path(__file__).resolve().parents[1],
    )
    assert worker.returncode == 0
    output = [json.loads(line) for line in worker.stdout.splitlines()]
    assert len(output) == 2
    assert output[0]["action"] == "block"
    assert output[1]["id"] == "req-1"
    assert output[1]["reason"] == "sensitive_data"
    assert "alice@example.com" not in worker.stdout + worker.stderr


def test_truncated_protocol_line_exits_without_decision(tmp_path):
    worker = subprocess.run(
        [sys.executable, "-m", "aidlp.worker", "--db", str(tmp_path / "worker.db")],
        input='{"id":"partial"}', text=True, capture_output=True, timeout=5,
        cwd=Path(__file__).resolve().parents[1],
    )
    assert worker.returncode == 2
    assert worker.stdout == ""
