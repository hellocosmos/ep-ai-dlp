from aidlp.detectors import detect, mask, max_severity, redact


def rules(text):
    return [f.rule for f in detect(text)]


def test_rrn_valid_and_invalid_date():
    assert rules("주민번호 900101-1234567 입니다") == ["kr_rrn"]
    assert rules("9001011234567") == ["kr_rrn"]
    # 13월은 존재하지 않는 날짜
    assert rules("901301-1234567") == []


def test_credit_card_luhn():
    assert rules("카드 4111-1111-1111-1111") == ["credit_card"]
    assert rules("카드 4111-1111-1111-1112") == []


def test_api_keys():
    assert rules("key=sk-ant-api03-abcdefghijklmnopqrstuvwxyz012345") == ["anthropic_key"]
    assert rules("OPENAI sk-proj-abcdefghijklmnopqrstuvwxyz0123") == ["openai_key"]
    assert rules("AKIAABCDEFGHIJKLMNOP") == ["aws_access_key"]
    assert rules("ghp_" + "a" * 36) == ["github_token"]


def test_private_key_header():
    assert rules("-----BEGIN RSA PRIVATE KEY-----\nMIIE...") == ["private_key"]


def test_phone_and_email():
    assert rules("연락처 010-1234-5678, mail a.b@example.co.kr") == ["kr_phone", "email"]


def test_no_overlap_between_rules():
    # 주민번호 숫자열이 카드번호/전화번호로 중복 탐지되지 않아야 한다
    findings = detect("900101-1234567")
    assert len(findings) == 1


def test_enabled_filter():
    assert [f.rule for f in detect("a@b.com 010-1111-2222", enabled=["email"])] == ["email"]


def test_mask_and_severity_and_redact():
    assert mask("4111111111111111") == "41************11"
    text = "번호 900101-1234567 끝"
    found = detect(text)
    assert max_severity(found) == "critical"
    assert redact(text, found) == "번호 [REDACTED:kr_rrn] 끝"


def test_empty():
    assert detect("") == []
    assert max_severity([]) is None
