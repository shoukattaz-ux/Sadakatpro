"""Selector generation, recorder event conversion, cleanup, DevTools import, live recording."""

from __future__ import annotations

import asyncio
import json

from flowpilot.core.recorder import Recorder, cleanup_steps, event_to_step, import_devtools_recording
from flowpilot.core.selectors import (
    candidates_from_descriptor,
    convert_devtools_selector,
    describe_selector,
    looks_dynamic,
)
from flowpilot.core.steps import new_step
from tests.conftest import needs_browser


def test_looks_dynamic():
    assert looks_dynamic("css-1x9fz2q")
    assert looks_dynamic("a8f3c2d19e")
    assert looks_dynamic("input_12345")
    assert looks_dynamic(":r1:")
    assert not looks_dynamic("login-button")
    assert not looks_dynamic("email")


def test_candidate_priority():
    desc = {
        "tag": "button", "id": "submit-9f8e7d6c5b", "role": "button", "name": "Log in", "text": "Log in",
        "attrs": {"data-testid": "login-btn", "type": "submit"}, "css": "form > button", "xpath": "/html/body/form/button[1]",
        "counts": {"data-testid": 1, "role_name": 1, "text": 1, "css": 1},
    }
    cands = candidates_from_descriptor(desc)
    assert cands[0] == '[data-testid="login-btn"]'
    assert cands[1] == 'role=button[name="Log in"]'
    assert cands[-1].startswith("xpath=")
    assert all("9f8e7d" not in c for c in cands)  # dynamic id skipped
    assert len(cands) == 4


def test_ambiguous_candidates_move_to_end():
    desc = {"tag": "input", "attrs": {"placeholder": "Search", "name": "q"}, "label": "Find",
            "counts": {"placeholder": 3, "name": 1, "label": 1}}
    cands = candidates_from_descriptor(desc)
    assert cands[0] == 'internal:label="Find"i'
    assert cands.index('input[name="q"]') < cands.index('input[placeholder="Search"]')


def test_describe_selector():
    assert describe_selector('role=button[name="Log in"]') == "the “Log in” button"
    assert describe_selector('internal:label="Email"i') == "the “Email” field"
    assert describe_selector("#email") == "element #email"


def test_convert_devtools_selector():
    assert convert_devtools_selector(['aria/Log in[role="button"]']) == 'role=button[name="Log in"]'
    assert convert_devtools_selector(["xpath///*[@id=\"x\"]"]) == 'xpath=//*[@id="x"]'
    assert convert_devtools_selector(["pierce/#email"]) == "#email"
    assert convert_devtools_selector(["text/Sign in"]) == 'text="Sign in"'
    assert convert_devtools_selector(["#id"]) == "#id"


def test_event_to_step_password_and_enter_merge():
    desc = {"tag": "input", "id": "pw", "attrs": {"type": "password", "name": "password"}, "inputType": "password"}
    step, replace = event_to_step({"kind": "type", "desc": desc, "value": "secret"})
    assert step.type == "type_text" and step.text == "{{account.password}}" and step.sensitive and not replace
    merged, replace = event_to_step({"kind": "press", "key": "Enter", "desc": desc}, step)
    assert replace and merged.press_enter
    again, replace = event_to_step({"kind": "type", "desc": desc, "value": "x"}, step)
    assert replace


def test_event_to_step_dblclick_replaces_click():
    desc = {"tag": "button", "id": "go", "attrs": {}}
    click, _ = event_to_step({"kind": "click", "desc": desc})
    dbl, replace = event_to_step({"kind": "dblclick", "desc": desc}, click)
    assert dbl.type == "double_click" and replace


def test_cleanup_steps():
    steps = [
        new_step("open_url", url="https://a"), new_step("open_url", url="https://a"),
        new_step("click", selector="#b"), new_step("click", selector="#b"),
        new_step("click", selector="#e"), new_step("type_text", selector="#e", text="x"),
        new_step("type_text", selector="#e", text="xy"),
    ]
    out = cleanup_steps(steps)
    assert [s.type for s in out] == ["open_url", "click", "type_text"]
    assert out[-1].text == "xy"


def test_import_devtools_recording():
    data = {
        "title": "Login test",
        "steps": [
            {"type": "setViewport", "width": 1280, "height": 800},
            {"type": "navigate", "url": "https://example.com/login"},
            {"type": "click", "selectors": [["aria/Email"], ["#email"]], "offsetX": 3, "offsetY": 3},
            {"type": "change", "value": "me@x.com", "selectors": [["aria/Email"], ["#email"]]},
            {"type": "keyDown", "key": "Enter"}, {"type": "keyUp", "key": "Enter"},
            {"type": "waitForExpression", "expression": "true"},
        ],
    }
    title, steps = import_devtools_recording(json.dumps(data))
    assert title == "Login test"
    assert [s.type for s in steps] == ["open_url", "type_text", "press_key", "log"]
    assert steps[1].selector == 'internal:label="Email"i' and steps[1].fallbacks == ["#email"]


@needs_browser
async def test_live_recording_and_pick(services, site_url):
    import json as _json

    from flowpilot.adapters import create_adapter
    from flowpilot.core.session import BrowserSession

    adapter = create_adapter("local", "", "", {"headless": "1", "profiles_dir": str(services.paths.root / "rec")})
    session = BrowserSession(adapter, "rec1", services.playwright)
    page = await session.open()
    await page.goto(f"{site_url}/login.html")
    recorded: list = []

    def on_step(step, replace):
        if replace and recorded:
            recorded[-1] = step
        else:
            recorded.append(step)

    rec = Recorder(session, on_step)
    try:
        await rec.start()
        await page.click("#email")
        await page.keyboard.type("me@test.dev")
        await page.click("#pw")
        await page.keyboard.type("hunter2")
        await page.select_option("#country", "de")
        await page.check("#remember")
        await page.click("[data-testid=login-btn]")
        await page.wait_for_url("**/welcome.html**")
        await asyncio.sleep(0.5)
        types = [s.type for s in recorded]
        assert types[0] == "open_url"
        assert "type_text" in types and "select_option" in types and "check" in types
        email = next(s for s in recorded if s.type == "type_text" and "me@test.dev" in s.text)
        assert "Email" in email.selector or "email" in email.selector
        pw = next(s for s in recorded if s.type == "type_text" and s.sensitive)
        assert pw.text == "{{account.password}}"
        click = [s for s in recorded if s.type == "click"][-1]
        assert click.selector == '[data-testid="login-btn"]'
        assert click.summary() == "Click the “Log in” button"
        assert types.count("open_url") == 1  # the navigation after the click is not recorded

        # Pick element: simulate the user clicking the heading while pick mode is on
        await page.goto(f"{site_url}/login.html")
        await rec.stop()
        pick_task = asyncio.ensure_future(rec.pick_element(timeout_s=10))
        await asyncio.sleep(0.5)
        await page.click("[data-testid=login-btn]")
        picked = await pick_task
        assert picked is not None and picked.selector == '[data-testid="login-btn"]'
        assert picked.element_desc == "the “Log in” button"
        assert picked.element_fp["tag"] == "button" and picked.element_fp["name"] == "Log in"
        assert _json.dumps(picked.element_fp)
    finally:
        await session.close()
        await adapter.aclose()
