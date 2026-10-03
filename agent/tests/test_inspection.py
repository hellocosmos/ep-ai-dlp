import json
from urllib.parse import urlencode
import pytest

from aidlp.detectors import detect, redact
from aidlp.inspection import inspect_request
from aidlp.store import Store, masked_preview


@pytest.fixture
def store(tmp_path):
    instance = Store(tmp_path / "events.db")
    yield instance
    instance.close()


def request(body="hello", content_type="text/plain", **kwargs):
    return dict(id="test-1", host="localhost", method="POST", path="/echo",
                content_type=content_type, body=body, **kwargs)


@pytest.mark.parametrize("suffix", ["\n-----END PRIVATE KEY-----", ""])
def test_pem_body_is_fully_masked(suffix):
    value = "-----BEGIN PRIVATE KEY-----\nSYNTHETIC_BODY_4111111111111111" + suffix
    findings = detect(value)
    assert [f.rule for f in findings] == ["private_key"]
    assert "SYNTHETIC_BODY" not in redact(value, findings)
    assert "SYNTHETIC_BODY" not in masked_preview(value, findings)


def test_repeated_zero_is_not_card():
    assert detect("0000000000000000") == []


def test_safe_request_is_allowed_without_content_storage(store):
    result = inspect_request(store, request("SYNTHETIC_PRIVATE_BUSINESS_TEXT"))
    assert result["action"] == "allow"
    assert store.get(result["event_id"]).preview == ""


@pytest.mark.parametrize("body,ctype", [
    ("alice@example.com", "text/plain"),
    ('{"prompt":"alice\\u0040example.com"}', "application/json"),
    ('{"alice@example.com":"value"}', "application/json"),
    ('{"messages":[{"content":"900101-1234567"}]}', "application/json"),
    ("-----BEGIN PRIVATE KEY-----\nSYNTHETIC_BODY", "text/plain"),
])
def test_sensitive_requests_are_blocked(store, body, ctype):
    result = inspect_request(store, request(body, ctype))
    assert result["action"] == "block"
    assert result["reason"] == "sensitive_data"
    event = store.get(result["event_id"])
    assert event.preview == ""
    assert all("masked" not in finding for finding in event.findings)


def test_percent_encoded_query_is_inspected(store):
    req = request("")
    req["path"] = "/echo?email=alice%40example.com"
    assert inspect_request(store, req)["action"] == "block"


@pytest.mark.parametrize("body,ctype", [
    ("{", "application/json"),
    ('{"prompt":"alice@example.com","prompt":"safe"}', "application/json"),
    ('{"value":NaN}', "application/json"),
    ("file", "multipart/form-data; boundary=test"),
    ("file", "application/octet-stream"),
    ("test", "text/plain; charset=utf-16"),
])
def test_unsupported_or_malformed_payload_is_blocked(store, body, ctype):
    assert inspect_request(store, request(body, ctype))["action"] == "block"


def test_empty_get_allowed(store):
    req = request("", "")
    req["method"] = "GET"
    assert inspect_request(store, req)["action"] == "allow"


def test_storage_failure_never_returns_allow(store):
    store.close()
    with pytest.raises(Exception):
        inspect_request(store, request())


def test_size_limit(store):
    assert inspect_request(store, request("x" * (1024 * 1024 + 1)))["action"] == "block"


def test_invalid_contract(store):
    with pytest.raises(ValueError):
        inspect_request(store, {"id": "bad"})


def test_metadata_database_does_not_contain_payload(tmp_path):
    path = tmp_path / "privacy.db"
    store = Store(path)
    inspect_request(store, request("alice@example.com TOP_SECRET_SYNTHETIC"))
    store.close()
    data = b"".join(p.read_bytes() for p in tmp_path.iterdir())
    assert b"alice@example.com" not in data
    assert b"TOP_SECRET_SYNTHETIC" not in data


FORM_TYPE = "application/x-www-form-urlencoded;charset=UTF-8"


@pytest.mark.parametrize("body", [
    "timezone_offset=-540&language=ko&empty=",
    "name=first&name=second",
    urlencode({"payload": json.dumps({"message": "안녕하세요", "count": 2})}),
    urlencode({"payload": json.dumps({"wrapped": json.dumps({"message": "hello"})})}),
])
def test_benign_urlencoded_form_is_inspected_without_content_storage(store, body):
    result = inspect_request(store, request(body, FORM_TYPE))
    assert result["action"] == "allow"
    assert result["reason"] == "clean"
    assert store.get(result["event_id"]).preview == ""


@pytest.mark.parametrize("body", [
    "email=alice%40example.com",
    "alice%40example.com=safe",
    "message=safe&message=alice%40example.com",
    "message=alice%40example.com&message=safe",
    urlencode({"payload": r'{"message":"alice\u0040example.com"}'}),
    urlencode({"payload": r'{"alice\u0040example.com":"safe"}'}),
    urlencode({"payload": json.dumps({"wrapped": r'{"message":"alice\u0040example.com"}'})}),
    urlencode({"payload": r'"alice\u0040example.com"'}),
])
def test_urlencoded_keys_repeated_values_and_json_strings_cannot_hide_email(store, body):
    result = inspect_request(store, request(body, FORM_TYPE))
    assert result["action"] == "block"
    assert result["reason"] == "sensitive_data"
    assert "email" in result["rules"]
    event = store.get(result["event_id"])
    assert event.preview == ""
    assert all(set(finding) == {"rule", "label", "severity"} for finding in event.findings)


@pytest.mark.parametrize("body", [
    "message=%",
    "message=%1",
    "message=%GG",
    "%ZZ=safe",
    "message=%C3%28",
    "message=%FF",
    "message=%ED%A0%80",
    "message=safe&malformed",
    urlencode({"payload": '{"message":"safe","message":"other"}'}),
    urlencode({"payload": json.dumps({"wrapped": '{"message":"safe","message":"other"}'})}),
    urlencode({"payload": '{"value":NaN}'}),
    urlencode({"payload": '{"value":Infinity}'}),
    urlencode({"payload": r'{"message":"\ud800"}'}),
    urlencode({"payload": '{"unfinished":'}),
    urlencode({"payload": "[" * 34 + '"hello"' + "]" * 34}),
])
def test_malformed_form_or_embedded_json_fails_closed(store, body):
    result = inspect_request(store, request(body, FORM_TYPE))
    assert result["action"] == "block"
    assert result["reason"] == "uninspectable_request"


@pytest.mark.parametrize("charset,body", [
    ("utf-16", "message=safe"),
    ("iso-8859-1", "message=safe"),
    ("us-ascii", "message=%C3%A9"),
    ("us-ascii", "message=안녕하세요"),
])
def test_form_charset_is_enforced(store, charset, body):
    result = inspect_request(store, request(body, f"application/x-www-form-urlencoded; charset={charset}"))
    assert result["action"] == "block"
    assert result["reason"] == "uninspectable_request"


def test_form_field_limit_counts_occurrences_including_repeated_keys(store):
    allowed = inspect_request(store, request("&".join(["field=safe"] * 1024), FORM_TYPE))
    assert allowed["action"] == "allow"
    denied = inspect_request(store, request("&".join(["field=safe"] * 1025), FORM_TYPE))
    assert denied["action"] == "block"
    assert denied["reason"] == "uninspectable_request"


@pytest.mark.parametrize("content_type", [
    "invalid",
    "text/plain, application/json",
    "application /json",
    "application/json; charset",
    "application/json; charset=",
    'application/json; charset="utf-8',
    'application/json; charset="utf-8"suffix',
    "application/json; charset=utf-8 junk",
    "application/json;",
    "application/json\r\nX-Other: value",
    "application/json; charset=utf-8,application/json",
    "text/plain(comment)",
    "application/json; charset=utf-8; charset=utf-16",
    "application/json; Charset=utf-8; CHARSET=utf-8",
    "application/json; profile=first; PROFILE=second",
])
def test_malformed_or_ambiguous_content_type_never_falls_back_to_plain_text(store, content_type):
    result = inspect_request(store, request(r'{"prompt":"alice\u0040example.com"}', content_type))
    assert result["action"] == "block"
    assert result["reason"] == "uninspectable_request"
    assert store.get(result["event_id"]).preview == ""


@pytest.mark.parametrize("content_type,body", [
    ('Application/JSON; Charset="UTF-8"', '{"prompt":"hello"}'),
    ("\tapplication/json \t; charset = utf-8 ", '{"prompt":"hello"}'),
    ('application/x-www-form-urlencoded; charset="UTF-8"; profile="semi;colon"', "prompt=hello"),
    ("text/plain; charset=us-ascii", "hello"),
    ('text/plain; profile="escaped\\\"quote"', "hello"),
])
def test_valid_content_type_tokens_and_quoted_parameters_remain_supported(store, content_type, body):
    result = inspect_request(store, request(body, content_type))
    assert result["action"] == "allow"
    assert result["reason"] == "clean"


def test_strict_quoted_json_content_type_still_decodes_sensitive_fields(store):
    result = inspect_request(store, request(r'{"prompt":"alice\u0040example.com"}', 'Application/JSON; Charset="UTF-8"'))
    assert result["action"] == "block"
    assert result["reason"] == "sensitive_data"
    assert "email" in result["rules"]
