"""Regression checks for browser access evidence classification."""
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest


MODULE_PATH = Path(__file__).parents[1] / "browser_probe_state.py"
spec = spec_from_file_location("browser_probe_state", MODULE_PATH)
module = module_from_spec(spec)
spec.loader.exec_module(module)
classify_challenge = module.classify_challenge
classify_probe_state = module.classify_probe_state


def test_challenge_cannot_be_overridden_by_a_visible_prompt():
    result = classify_probe_state(403, True, True, True)
    assert result["result"] == "challenge_stop"


def test_login_cannot_be_overridden_by_a_visible_prompt_or_http_error():
    result = classify_probe_state(403, True, False, True)
    assert result["result"] == "login_required"


@pytest.mark.parametrize("status", [400, 403, 429, 500, 503])
def test_http_failure_is_not_prompt_success_or_automatically_a_challenge(status):
    result = classify_probe_state(status, True, False, False)
    assert result["result"] == "http_error"
    assert result["challenge"] is False


def test_visible_prompt_is_access_evidence_only():
    assert classify_probe_state(200, True)["result"] == "prompt_visible"
    assert classify_probe_state(None, True)["result"] == "prompt_visible"


def test_blank_page_is_not_success_and_missing_status_stays_unknown():
    assert classify_probe_state(200)["result"] == "page_unavailable"
    assert classify_probe_state(302)["result"] == "page_unavailable"
    assert classify_probe_state()["result"] == "unknown"


def test_result_is_fixed_metadata_without_input_text_or_urls():
    result = classify_probe_state(403, False, True, False)
    assert result == {
        "result": "challenge_stop",
        "http_status": 403,
        "prompt_box_visible": False,
        "challenge": True,
        "login_required": False,
    }


@pytest.mark.parametrize("text", [
    "사람인지 확인하십시오",
    "사람인지\n 확인하십시오",
    "Verify you are human",
    "Just a moment...",
    "Checking your browser",
    "Performing security verification",
])
def test_known_challenge_copy_in_title_or_body_is_detected(text):
    assert classify_challenge(title=text)
    assert classify_challenge(body=text)


def test_cross_origin_cloudflare_frame_is_detected_without_reading_or_clicking_it():
    assert classify_challenge(frames=[{
        "url": "https://challenges.cloudflare.com/cdn-cgi/challenge-platform/test?token=PRIVATE",
        "title": "",
    }])


def test_frame_title_alone_can_supply_challenge_evidence():
    assert classify_challenge(frames=[{
        "url": "about:blank",
        "title": "Widget containing a Cloudflare security challenge",
    }])


@pytest.mark.parametrize("url", [
    "https://challenges.cloudflare.com.evil.test/",
    "https://example.test/?next=https://challenges.cloudflare.com/",
    "https://example.test/challenges.cloudflare.com/",
    "https://challenges.cloudflare.com@example.test/",
    "not a URL",
    "http://[",
])
def test_similar_or_malformed_frame_url_does_not_invent_challenge_evidence(url):
    assert not classify_challenge(frames=[{"url": url, "title": ""}])


def test_generic_forbidden_or_cloudflare_error_is_not_a_human_challenge():
    assert not classify_challenge(title="403 Forbidden", body="Access denied")
    assert not classify_challenge(body="Cloudflare Ray ID: example; server unavailable")
    assert not classify_challenge()


def tab(index, host="chatgpt.com", **fields):
    return {
        "index": index,
        "host": host,
        "http_status": 200,
        "prompt_box_visible": False,
        "challenge": False,
        "login_required": False,
        "assistant_messages": 0,
        "user_messages": 0,
        **fields,
    }


def test_tab_selection_finds_prompt_after_wrong_initial_tab():
    result = module.select_probe_result([
        tab(0, challenge=True, http_status=403),
        tab(3, prompt_box_visible=True, assistant_messages=1, user_messages=1),
    ])
    assert result["selected_index"] == 3
    assert result["result"] == "prompt_visible"
    assert result["assistant_messages"] == result["user_messages"] == 1


def test_unrelated_prompt_or_challenge_cannot_influence_chatgpt_selection():
    result = module.select_probe_result([
        tab(0, "example.test", prompt_box_visible=True),
        tab(1, "chatgpt.com.evil.test", challenge=True),
        tab(2, http_status=503),
    ])
    assert result["selected_index"] == 2
    assert result["result"] == "http_error"


def test_multiple_clean_prompt_tabs_are_ambiguous_without_summing_messages():
    result = module.select_probe_result([
        tab(0, prompt_box_visible=True, user_messages=1),
        tab(1, prompt_box_visible=True, user_messages=7),
    ])
    assert result["result"] == "ambiguous_tabs"
    assert result["selected_index"] is None
    assert result["http_status"] is None
    assert result["assistant_messages"] is result["user_messages"] is None


def test_error_tab_with_prompt_is_not_a_clean_candidate():
    failed = tab(0, http_status=403, prompt_box_visible=True)
    result = module.select_probe_result([failed])
    assert result["result"] == "http_error"
    assert result["challenge"] is False
    result = module.select_probe_result([failed, tab(1, prompt_box_visible=True)])
    assert result["result"] == "prompt_visible"
    assert result["selected_index"] == 1


@pytest.mark.parametrize("tabs", [[], [tab(0, "example.test", prompt_box_visible=True)],
                                  [tab(0, "CHATGPT.COM", prompt_box_visible=True)]])
def test_no_eligible_tabs_means_unavailable_without_fabricated_observations(tabs):
    result = module.select_probe_result(tabs)
    assert result["result"] == "page_unavailable"
    assert result["selected_index"] is None
    assert result["http_status"] is None
    assert result["assistant_messages"] is result["user_messages"] is None


def test_auth_tab_cannot_be_reported_as_chatgpt_prompt_success():
    result = module.select_probe_result([tab(0, "auth.openai.com", prompt_box_visible=True)])
    assert result["result"] == "login_required"


@pytest.mark.parametrize("tabs,expected_index,expected_result", [
    ([tab(0, http_status=503), tab(1, "auth.openai.com"), tab(2, challenge=True)],
     2, "challenge_stop"),
    ([tab(0, http_status=503), tab(1, "auth.openai.com")], 1, "login_required"),
    ([tab(0), tab(1, http_status=503)], 1, "http_error"),
])
def test_nonclean_tab_selection_preserves_stop_reason_priority(tabs, expected_index, expected_result):
    result = module.select_probe_result(tabs)
    assert result["selected_index"] == expected_index
    assert result["result"] == expected_result


def test_message_counts_do_not_establish_submission_delivery_or_access_success():
    result = module.select_probe_result([tab(4, user_messages=5, assistant_messages=4)])
    assert result == {
        "selected_index": 4,
        "result": "page_unavailable",
        "http_status": 200,
        "prompt_box_visible": False,
        "challenge": False,
        "login_required": False,
        "assistant_messages": 4,
        "user_messages": 5,
    }
