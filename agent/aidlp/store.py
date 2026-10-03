"""탐지 이벤트 저장소 (SQLite).

엔진(서비스)이 쓰고 UI가 읽는다. 원문 대신 마스킹된 미리보기만 저장한다.
"""
from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

from .detectors import Finding, max_severity

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts        REAL    NOT NULL,
    service   TEXT    NOT NULL,
    host      TEXT    NOT NULL DEFAULT '',
    direction TEXT    NOT NULL,          -- input | output
    action    TEXT    NOT NULL,          -- logged | redacted | blocked
    severity  TEXT,                      -- NULL이면 탐지 없음
    findings  TEXT    NOT NULL DEFAULT '[]',
    preview   TEXT    NOT NULL DEFAULT '',
    chars     INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);
"""

PREVIEW_LEN = 400


@dataclass
class Event:
    id: int
    ts: float
    service: str
    host: str
    direction: str
    action: str
    severity: str | None
    findings: list[dict]
    preview: str
    chars: int


def _row_to_event(row: sqlite3.Row) -> Event:
    return Event(
        id=row["id"], ts=row["ts"], service=row["service"], host=row["host"],
        direction=row["direction"], action=row["action"], severity=row["severity"],
        findings=json.loads(row["findings"]), preview=row["preview"], chars=row["chars"],
    )


def masked_preview(text: str, findings: list[Finding]) -> str:
    out = text
    for f in sorted(findings, key=lambda f: -f.start):
        out = out[: f.start] + f.masked + out[f.end:]
    out = " ".join(out.split())
    return out[:PREVIEW_LEN] + ("…" if len(out) > PREVIEW_LEN else "")


class Store:
    def __init__(self, path: Path | str):
        self.path = str(path)
        self.conn = sqlite3.connect(self.path, timeout=5, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)

    def close(self) -> None:
        self.conn.close()

    def add(
        self, service: str, direction: str, text: str, findings: list[Finding],
        action: str = "logged", host: str = "", ts: float | None = None,
        *, metadata_only: bool = False,
    ) -> int:
        cur = self.conn.execute(
            "INSERT INTO events (ts, service, host, direction, action, severity, findings, preview, chars)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (
                time.time() if ts is None else ts, service, host, direction, action, max_severity(findings),
                json.dumps([
                    {"rule": f.rule, "label": f.label, "severity": f.severity}
                    if metadata_only else f.to_dict() for f in findings
                ], ensure_ascii=False),
                "" if metadata_only else masked_preview(text, findings), len(text),
            ),
        )
        self.conn.commit()
        return cur.lastrowid

    def recent(self, limit: int = 200, only_findings: bool = False, after_id: int = 0) -> list[Event]:
        sql = "SELECT * FROM events WHERE id > ?"
        if only_findings:
            sql += " AND severity IS NOT NULL"
        sql += " ORDER BY id DESC LIMIT ?"
        return [_row_to_event(r) for r in self.conn.execute(sql, (after_id, limit))]

    def get(self, event_id: int) -> Event | None:
        row = self.conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
        return _row_to_event(row) if row else None

    def stats(self, since: float) -> dict:
        row = self.conn.execute(
            "SELECT COUNT(*) AS total,"
            " SUM(severity IS NOT NULL) AS detected,"
            " SUM(severity IN ('critical','high')) AS high,"
            " SUM(action = 'blocked') AS blocked"
            " FROM events WHERE ts >= ?",
            (since,),
        ).fetchone()
        return {k: (row[k] or 0) for k in ("total", "detected", "high", "blocked")}

    def by_service(self, since: float) -> list[tuple[str, int, int]]:
        rows = self.conn.execute(
            "SELECT service, COUNT(*) AS n, SUM(severity IS NOT NULL) AS d"
            " FROM events WHERE ts >= ? GROUP BY service ORDER BY n DESC",
            (since,),
        )
        return [(r["service"], r["n"], r["d"] or 0) for r in rows]
