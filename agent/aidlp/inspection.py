"""Bounded, fail-closed request inspection shared by the proxy adapters.

This module does not call external models or persist request contents.
"""
from __future__ import annotations

import json
import re
from urllib.parse import parse_qsl, unquote

from .detectors import detect
from .store import Store

MAX_BODY_BYTES = 1024 * 1024
MAX_FORM_FIELDS = 1024
FIELDS = {"id", "host", "method", "path", "content_type", "body"}
_HTTP_TOKEN = r"[!#$%&'*+.^_\x60|~0-9A-Za-z-]+"
_QUOTED_PARAMETER = r'"(?:[\t\x20\x21\x23-\x5b\x5d-\x7e]|\\[\t\x20-\x7e])*"'
_MEDIA_TYPE = re.compile(rf"({_HTTP_TOKEN})/({_HTTP_TOKEN})")
_MEDIA_PARAMETER = re.compile(
    rf"[ \t]*;[ \t]*({_HTTP_TOKEN})[ \t]*=[ \t]*({_HTTP_TOKEN}|{_QUOTED_PARAMETER})"
)
_SUPPORTED_MEDIA = {"application/json", "text/plain", "application/x-www-form-urlencoded"}


def _content_type(value):
    """Consume one HTTP media type without MIME-parser fallback or ambiguity."""
    value = value.strip(" \t")
    matched = _MEDIA_TYPE.match(value)
    if matched is None:
        raise ValueError("invalid_content_type")
    media = (matched[1] + "/" + matched[2]).lower()
    if media not in _SUPPORTED_MEDIA:
        raise ValueError("unsupported_media_type")
    position = matched.end()
    parameters = {}
    while position < len(value):
        matched = _MEDIA_PARAMETER.match(value, position)
        if matched is None:
            raise ValueError("invalid_content_type_parameter")
        name, parameter = matched[1].lower(), matched[2]
        if name in parameters:
            raise ValueError("duplicate_content_type_parameter")
        if parameter.startswith('"'):
            parameter = re.sub(r"\\(.)", r"\1", parameter[1:-1])
        parameters[name] = parameter
        position = matched.end()
    charset = parameters.get("charset", "utf-8").lower()
    if charset not in {"utf-8", "utf8", "us-ascii"}:
        raise ValueError("unsupported_charset")
    return media, charset


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_key")
        result[key] = value
    return result


def _reject_constant(value):
    raise ValueError("non_json_number")


def _json_value(text):
    return json.loads(text, object_pairs_hook=_unique_object,
                      parse_constant=_reject_constant)


def _looks_like_json(text):
    stripped = text.lstrip()
    return stripped.startswith(("{", "[", '"')) or stripped in {"NaN", "Infinity", "-Infinity"}


def _strings(value, depth=0, *, embedded_json=False):
    if depth > 32:
        raise ValueError("json_depth")
    if isinstance(value, str):
        if embedded_json:
            # JSON escape sequences must not introduce invalid Unicode after
            # the form's strict UTF-8 decoding has completed.
            value.encode("utf-8")
        yield value
        if embedded_json and _looks_like_json(value):
            yield from _strings(_json_value(value), depth + 1, embedded_json=True)
    elif isinstance(value, dict):
        for key, item in value.items():
            if embedded_json:
                yield from _strings(key, depth + 1, embedded_json=True)
            else:
                yield key
            yield from _strings(item, depth + 1, embedded_json=embedded_json)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item, depth + 1, embedded_json=embedded_json)


def _form_strings(body, charset):
    # urllib otherwise leaves malformed percent escapes untouched. A list of
    # pairs preserves every repeated key instead of hiding earlier values.
    if re.search(r"%(?![0-9A-Fa-f]{2})", body):
        raise ValueError("malformed_form_escape")
    encoding = "ascii" if charset == "us-ascii" else "utf-8"
    body.encode(encoding)
    pairs = parse_qsl(body, keep_blank_values=True, strict_parsing=True,
                      encoding=encoding, errors="strict", max_num_fields=MAX_FORM_FIELDS)
    for pair in pairs:
        for text in pair:
            yield text
            if _looks_like_json(text):
                # JSON-shaped fields are parsed strictly. Malformed or
                # ambiguous JSON fails closed instead of falling back to text.
                yield from _strings(_json_value(text), embedded_json=True)


def inspect_request(store: Store, request: dict) -> dict:
    if not isinstance(request, dict) or set(request) != FIELDS:
        raise ValueError("invalid_contract")
    if any(not isinstance(value, str) for value in request.values()):
        raise ValueError("invalid_contract")
    if not re.fullmatch(r"[A-Za-z0-9._:-]{1,253}", request["host"]):
        raise ValueError("invalid_host")
    if not re.fullmatch(r"[A-Za-z0-9-]{1,64}", request["id"]):
        raise ValueError("invalid_id")
    body = request["body"]
    texts = []
    reason = "clean"
    try:
        if len(body.encode("utf-8")) > MAX_BODY_BYTES:
            raise ValueError("body_too_large")
        if len(request["path"]) > 16384:
            raise ValueError("path_too_large")
        if request["method"] not in {"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"}:
            raise ValueError("unsupported_method")
        texts.append(unquote(request["path"], errors="strict"))
        if body:
            media, charset = _content_type(request["content_type"])
            if media == "application/json":
                value = _json_value(body)
                # Scan both decoded fields and the raw representation (e.g. numbers).
                texts.extend(_strings(value))
                texts.append(body)
            elif media == "text/plain":
                texts.append(body)
            elif media == "application/x-www-form-urlencoded":
                texts.extend(_form_strings(body, charset))
            else:
                raise ValueError("unsupported_media_type")
        findings = detect("\n".join(texts))
        if findings:
            reason = "sensitive_data"
    except (ValueError, UnicodeError, RecursionError):
        reason = "uninspectable_request"
        findings = []
    action = "allow" if reason == "clean" else "block"
    # Commit before allowing. A disk/DB failure propagates to the worker's block path.
    event_id = store.add("ChatGPT" if request["host"].lower() == "chatgpt.com" else "AI DLP", "input", body, findings,
                         action="logged" if action == "allow" else "blocked",
                         host=request["host"], metadata_only=True)
    return {"id": request["id"], "action": action, "reason": reason,
            "rules": sorted({f.rule for f in findings}), "event_id": event_id}
