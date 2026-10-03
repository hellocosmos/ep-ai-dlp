"""에이전트 설정과 데이터 경로."""
from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .detectors import RULES

MODES = ("monitor", "redact", "block")
MODE_LABELS = {"monitor": "모니터링", "redact": "마스킹", "block": "차단"}


def data_dir() -> Path:
    env = os.environ.get("AIDLP_HOME")
    if env:
        base = Path(env)
    elif sys.platform == "win32":
        base = Path(os.environ.get("ProgramData", r"C:\ProgramData")) / "AIDLP"
    else:
        base = Path.home() / ".aidlp"
    base.mkdir(parents=True, exist_ok=True)
    return base


@dataclass
class Config:
    mode: str = "monitor"
    enabled_rules: list[str] = field(default_factory=lambda: [r.id for r in RULES])
    notify_min_severity: str = "high"

    @classmethod
    def load(cls, path: Path | None = None) -> "Config":
        path = path or data_dir() / "config.json"
        if not path.exists():
            return cls()
        raw = json.loads(path.read_text(encoding="utf-8"))
        cfg = cls()
        for k, v in raw.items():
            if hasattr(cfg, k):
                setattr(cfg, k, v)
        if cfg.mode not in MODES:
            cfg.mode = "monitor"
        return cfg

    def save(self, path: Path | None = None) -> None:
        path = path or data_dir() / "config.json"
        path.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8")
