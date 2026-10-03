import time

from aidlp.config import Config
from aidlp.detectors import detect
from aidlp.store import Store, masked_preview


def test_add_and_query(tmp_path):
    s = Store(tmp_path / "e.db")
    text = "고객 주민번호 900101-1234567 정리해줘"
    eid = s.add("ChatGPT", "input", text, detect(text), host="example")
    s.add("Claude", "input", "오늘 날씨 알려줘", [])

    ev = s.get(eid)
    assert ev.severity == "critical"
    assert ev.findings[0]["rule"] == "kr_rrn"
    # 원문 민감정보는 저장되지 않는다
    assert "900101-1234567" not in ev.preview
    assert ev.chars == len(text)

    assert [e.service for e in s.recent()] == ["Claude", "ChatGPT"]
    assert [e.service for e in s.recent(only_findings=True)] == ["ChatGPT"]
    assert [e.service for e in s.recent(after_id=eid)] == ["Claude"]

    st = s.stats(time.time() - 60)
    assert st == {"total": 2, "detected": 1, "high": 1, "blocked": 0}
    assert s.by_service(0) == [("ChatGPT", 1, 1), ("Claude", 1, 0)] or s.by_service(0) == [("Claude", 1, 0), ("ChatGPT", 1, 1)]


def test_masked_preview_truncates():
    assert masked_preview("a" * 1000, []).endswith("…")


def test_config_roundtrip(tmp_path):
    p = tmp_path / "config.json"
    c = Config(mode="block", enabled_rules=["email"])
    c.save(p)
    loaded = Config.load(p)
    assert loaded.mode == "block" and loaded.enabled_rules == ["email"]
    p.write_text('{"mode": "bogus"}', encoding="utf-8")
    assert Config.load(p).mode == "monitor"
