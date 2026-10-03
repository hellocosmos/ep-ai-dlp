"""민감정보 탐지 규칙.

각 규칙은 정규식으로 후보를 찾고, 가능한 경우 검증 함수(체크섬·날짜 등)로 오탐을 줄인다.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from datetime import date
from typing import Callable, Iterable

SEVERITY_ORDER = {"low": 0, "medium": 1, "high": 2, "critical": 3}


@dataclass(frozen=True)
class Finding:
    rule: str
    label: str
    severity: str
    masked: str
    start: int
    end: int

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class Rule:
    id: str
    label: str
    severity: str
    pattern: re.Pattern
    validate: Callable[[str], bool] | None = None
    group: int = 0


def mask(value: str, keep_head: int = 2, keep_tail: int = 2) -> str:
    if len(value) <= keep_head + keep_tail:
        return "*" * len(value)
    return value[:keep_head] + "*" * (len(value) - keep_head - keep_tail) + value[-keep_tail:]


def _luhn_ok(value: str) -> bool:
    digits = [int(c) for c in value if c.isdigit()]
    if not 13 <= len(digits) <= 19 or len(set(digits)) == 1:
        return False
    total = 0
    for i, d in enumerate(reversed(digits)):
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def _rrn_ok(value: str) -> bool:
    """주민등록번호: 생년월일과 성별 자리 검증 (2020년 10월 이후 번호는 체크섬이 없어 체크섬은 보지 않는다)."""
    digits = re.sub(r"\D", "", value)
    if len(digits) != 13:
        return False
    yy, mm, dd, g = int(digits[0:2]), int(digits[2:4]), int(digits[4:6]), int(digits[6])
    century = {1: 1900, 2: 1900, 5: 1900, 6: 1900, 3: 2000, 4: 2000, 7: 2000, 8: 2000, 9: 1800, 0: 1800}
    if g not in century:
        return False
    try:
        date(century[g] + yy, mm, dd)
    except ValueError:
        return False
    return True


RULES: list[Rule] = [
    Rule(
        "kr_rrn", "주민등록번호", "critical",
        re.compile(r"(?<!\d)(\d{6}[-\s]?[0-9]\d{6})(?!\d)"), _rrn_ok, 1,
    ),
    Rule(
        "credit_card", "신용카드번호", "critical",
        re.compile(r"(?<!\d)((?:\d[ -]?){12,18}\d)(?!\d)"), _luhn_ok, 1,
    ),
    Rule(
        "private_key", "개인키(PEM)", "critical",
        re.compile(
            r"-----BEGIN (?P<kind>(?:RSA |EC |OPENSSH |DSA |ENCRYPTED )?PRIVATE KEY)-----"
            r"[\s\S]*?(?:-----END (?P=kind)-----|\Z)"
        ),
    ),
    Rule("anthropic_key", "Anthropic API 키", "critical", re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{20,}")),
    Rule("openai_key", "OpenAI API 키", "critical", re.compile(r"\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_\-]{20,}")),
    Rule("aws_access_key", "AWS 액세스 키", "critical", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    Rule("github_token", "GitHub 토큰", "critical", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b")),
    Rule("slack_token", "Slack 토큰", "high", re.compile(r"\bxox[abprs]-[A-Za-z0-9\-]{10,}")),
    Rule("google_api_key", "Google API 키", "high", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    Rule(
        "kr_phone", "휴대전화번호", "medium",
        re.compile(r"(?<!\d)(01[016789][-\s]?\d{3,4}[-\s]?\d{4})(?!\d)"), None, 1,
    ),
    Rule(
        "email", "이메일 주소", "low",
        re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"),
    ),
]

RULES_BY_ID = {r.id: r for r in RULES}


def detect(text: str, enabled: Iterable[str] | None = None) -> list[Finding]:
    if not text:
        return []
    enabled_set = set(enabled) if enabled is not None else None
    findings: list[Finding] = []
    taken: list[tuple[int, int]] = []
    # 심각도 높은 규칙부터 적용해, 같은 구간을 낮은 규칙이 중복 탐지하지 않게 한다
    # Consume entire PEM blocks before number-like fragments inside their payload.
    for rule in sorted(RULES, key=lambda r: (r.id != "private_key", -SEVERITY_ORDER[r.severity])):
        if enabled_set is not None and rule.id not in enabled_set:
            continue
        for m in rule.pattern.finditer(text):
            value = m.group(rule.group)
            start, end = m.start(rule.group), m.end(rule.group)
            if any(s < end and start < e for s, e in taken):
                continue
            if rule.validate and not rule.validate(value):
                continue
            taken.append((start, end))
            masked = "[PRIVATE KEY]" if rule.id == "private_key" else mask(value)
            findings.append(Finding(rule.id, rule.label, rule.severity, masked, start, end))
    findings.sort(key=lambda f: f.start)
    return findings


def max_severity(findings: Iterable[Finding]) -> str | None:
    best = None
    for f in findings:
        if best is None or SEVERITY_ORDER[f.severity] > SEVERITY_ORDER[best]:
            best = f.severity
    return best


def redact(text: str, findings: Iterable[Finding]) -> str:
    """탐지 구간을 [REDACTED:label]로 치환한다 (차단 대신 마스킹 모드용)."""
    out = text
    for f in sorted(findings, key=lambda f: -f.start):
        out = out[: f.start] + f"[REDACTED:{f.rule}]" + out[f.end:]
    return out
