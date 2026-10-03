"""Pure access-probe classifiers; returned evidence never contains page content.

Challenge markers are a reason to stop automation, not proof of who issued an
HTTP error. ``prompt_visible`` describes the UI only and does not establish that
a prompt was submitted, inspected, or answered.
"""
from collections.abc import Iterable, Mapping
from urllib.parse import urlsplit


_CHALLENGE_TEXT = (
    "just a moment",
    "verify you are human",
    "checking your browser",
    "performing security verification",
    "cloudflare security challenge",
    "사람인지 확인하십시오",
)


def _has_challenge_text(value: str) -> bool:
    normalized = " ".join(value.casefold().split())
    return any(marker in normalized for marker in _CHALLENGE_TEXT)


def classify_challenge(
    title: str = "", body: str = "", frames: Iterable[Mapping[str, str]] = ()
) -> bool:
    """Recognize observed challenge copy or iframe metadata without browser I/O.

    ``frames`` contains only ``url`` and ``title`` metadata supplied by the
    caller. No input text, URL, token, or challenge contents are returned or
    persisted. An unrelated HTTP 403 is not evidence of a human challenge.
    """
    if _has_challenge_text(title) or _has_challenge_text(body):
        return True
    for frame in frames:
        if _has_challenge_text(frame.get("title", "")):
            return True
        try:
            parsed = urlsplit(frame.get("url", ""))
            if parsed.scheme in {"https", "http"} and parsed.hostname == "challenges.cloudflare.com":
                return True
        except ValueError:
            # Invalid metadata supplies no positive evidence; it cannot be
            # upgraded into success by the access-state classifier.
            continue
    return False


def classify_probe_state(
    status_code: int | None = None,
    visible_prompt: bool = False,
    challenge_markers: bool = False,
    login_required: bool = False,
) -> dict[str, str | int | bool | None]:
    """Classify access with challenge, login, and HTTP failure taking priority.

    A missing HTTP status is preserved as unknown metadata even if a prompt
    control was observed. A visible control never overrides a known HTTP error.
    """
    if challenge_markers:
        result = "challenge_stop"
    elif login_required:
        result = "login_required"
    elif status_code is not None and status_code >= 400:
        result = "http_error"
    elif visible_prompt:
        result = "prompt_visible"
    elif status_code is None:
        result = "unknown"
    else:
        result = "page_unavailable"
    return {
        "result": result,
        "http_status": status_code,
        "prompt_box_visible": visible_prompt,
        "challenge": challenge_markers,
        "login_required": login_required,
    }


def select_probe_result(tab_summaries: Iterable[Mapping]) -> dict[str, str | int | bool | None]:
    """Select evidence from eligible tabs without assuming the first tab was used.

    Only an unambiguous, clean ChatGPT prompt takes priority over stop reasons
    elsewhere. Authentication pages cannot establish ChatGPT prompt access.
    Message counts are copied from the selected UI observation only; they do
    not establish submission, delivery, response generation, or streaming.
    With no selected tab, counts and HTTP status stay unknown rather than being
    combined across tabs. Same-priority stop reasons retain input order.
    """
    candidates = []
    for tab in tab_summaries:
        host = tab.get("host")
        if host not in {"chatgpt.com", "auth.openai.com"}:
            continue
        classified = classify_probe_state(
            tab.get("http_status"),
            tab.get("prompt_box_visible", False),
            tab.get("challenge", False),
            host == "auth.openai.com" or tab.get("login_required", False),
        )
        candidates.append({
            **classified,
            "selected_index": tab["index"],
            "assistant_messages": tab.get("assistant_messages"),
            "user_messages": tab.get("user_messages"),
        })

    clean = [candidate for candidate in candidates if candidate["result"] == "prompt_visible"]
    if len(clean) == 1:
        return clean[0]
    if len(clean) > 1 or not candidates:
        return {
            **classify_probe_state(visible_prompt=bool(clean)),
            "result": "ambiguous_tabs" if clean else "page_unavailable",
            "selected_index": None,
            "assistant_messages": None,
            "user_messages": None,
        }
    priority = {"challenge_stop": 0, "login_required": 1, "http_error": 2,
                "page_unavailable": 3, "unknown": 4}
    return min(candidates, key=lambda candidate: priority[candidate["result"]])
