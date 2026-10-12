"""AI step builder with a fake Claude client (no network) + offline parser + page outline."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from flowpilot.core.runner import flow_problems
from flowpilot.services.ai_builder import (
    OUTPUT_SCHEMA,
    AiBuilderError,
    draft_offline,
    draft_with_claude,
    step_catalog,
    to_steps,
)
from tests.conftest import needs_browser


class FakeMessages:
    def __init__(self, payload: dict | None, stop_reason: str = "end_turn") -> None:
        self.payload = payload
        self.stop_reason = stop_reason
        self.calls: list[dict] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        text = json.dumps(self.payload) if self.payload is not None else "not json"
        return SimpleNamespace(stop_reason=self.stop_reason, content=[SimpleNamespace(type="text", text=text)])


def fake_client(payload, stop_reason="end_turn"):
    msgs = FakeMessages(payload, stop_reason)
    return SimpleNamespace(beta=SimpleNamespace(messages=msgs)), msgs


def P(**kw):  # noqa: N802
    return [{"name": k, "value": v} for k, v in kw.items()]


LIKE_POSTS = {
    "summary": "Log in and like the first 3 posts.",
    "steps": [
        {"type": "open_url", "params": P(url="https://social.example/login")},
        {"type": "type_text", "params": P(selector='internal:label="Email"i', text="{{account.email}}")},
        {"type": "type_text", "params": P(selector="#pw", text="{{account.password}}", sensitive="true")},
        {"type": "click", "params": P(selector='role=button[name="Log in"]')},
        {"type": "wait_random", "params": P(min_seconds="1", max_seconds="3")},
        {"type": "repeat", "params": P(times="3")},
        {"type": "click", "params": P(selector=':nth-match(button:has-text("Like"), {{loop.index}})')},
        {"type": "end_loop", "params": []},
        {"type": "teleport", "params": []},
        {"type": "scroll", "params": P(mode="sideways", wobble="1")},
    ],
    "warnings": ["Check the Like button selector."],
}


def test_catalog_and_schema_cover_steps():
    cat = step_catalog()
    for t in ("open_url", "click", "type_text", "repeat", "end_loop", "if_element", "close_browser"):
        assert f"- {t}:" in cat
    assert "- run_js:" not in cat
    assert "element_fp" not in cat and "delay_before" not in cat
    assert OUTPUT_SCHEMA["additionalProperties"] is False


async def test_draft_with_claude_validates_and_warns():
    client, msgs = fake_client(LIKE_POSTS)
    draft = await draft_with_claude("log in, then like the first 3 posts", "", outline="Page: x",
                                    account_variables=["account.email", "account.password"],
                                    existing=["1. Open https://x"], client=client)
    types = [s.type for s in draft.steps]
    assert types == ["open_url", "type_text", "type_text", "click", "wait_random", "repeat", "click", "end_loop",
                     "scroll"]
    assert draft.steps[2].sensitive is True and draft.steps[5].times == 3
    assert draft.steps[8].mode == "by_amount"  # invalid literal dropped, default kept
    assert any("teleport" in w for w in draft.warnings)
    assert any("wobble" in w for w in draft.warnings)
    assert "Check the Like button selector." in draft.warnings
    assert flow_problems(draft.steps) == {}
    call = msgs.calls[0]
    assert call["model"] == "claude-opus-5-5"
    assert call["output_config"]["format"]["type"] == "json_schema"
    assert call["fallbacks"] == "default" and "server-side-fallback-2026-07-01" in call["betas"]
    content = call["messages"][0]["content"]
    assert "{{account.email}}" in content and "Page: x" in content and "log in" in content


async def test_draft_errors_are_friendly():
    with pytest.raises(AiBuilderError, match="API key"):
        await draft_with_claude("x", "")
    client, _ = fake_client({"summary": "", "steps": [], "warnings": []}, stop_reason="refusal")
    with pytest.raises(AiBuilderError, match="declined"):
        await draft_with_claude("x", "", client=client)
    client, _ = fake_client(None)
    with pytest.raises(AiBuilderError, match="could not be read"):
        await draft_with_claude("x", "", client=client)
    client, _ = fake_client({"summary": "", "steps": [], "warnings": []})
    with pytest.raises(AiBuilderError, match="could not turn"):
        await draft_with_claude("x", "", client=client)


def test_to_steps_list_and_optional_fields():
    steps, warnings = to_steps({"steps": [
        {"type": "random_item", "params": P(items="red\ngreen\n", variable="color")},
        {"type": "click", "params": P(selector="#a", timeout="", retries="2", fallbacks="#b\n#c")},
    ]})
    assert steps[0].items == ["red", "green"]
    assert steps[1].timeout is None and steps[1].retries == 2 and steps[1].fallbacks == ["#b", "#c"]
    assert not warnings


def test_offline_parser():
    d = draft_offline('open example.com, then log in, then like the first 3 posts, wait 2-4, '
                      'search "cats", scroll down, take a screenshot and then close the browser')
    types = [s.type for s in d.steps]
    assert types[0] == "open_url" and d.steps[0].url == "example.com"
    assert types[1:5] == ["type_text", "type_text", "click", "wait_for_load"]
    assert d.steps[1].text == "{{account.email}}" and d.steps[2].sensitive
    assert ["repeat", "click", "wait_random", "end_loop"] == types[5:9] and d.steps[5].times == 3
    assert d.steps[9].type == "wait_random" and d.steps[9].max_seconds == 4
    assert d.steps[10].type == "type_text" and d.steps[10].text == "cats" and d.steps[10].press_enter
    assert types[-3:] == ["scroll", "screenshot", "close_browser"]
    assert draft_offline("do a backflip").warnings


@needs_browser
async def test_page_outline(site_url):
    from playwright.async_api import async_playwright

    from flowpilot.adapters.local import find_browser
    from flowpilot.services.ai_builder import page_outline

    pw = await async_playwright().start()
    browser = await pw.chromium.launch(executable_path=find_browser(), headless=True, args=["--no-sandbox"])
    page = await browser.new_page()
    try:
        await page.goto(f"{site_url}/login.html")
        outline = await page_outline(page)
        assert outline.startswith("Page: Test Login")
        assert '[data-testid="login-btn"]' in outline
        assert "Email" in outline
    finally:
        await browser.close()
        await pw.stop()
