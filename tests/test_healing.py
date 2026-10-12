"""Self-healing selectors on a real page."""

from __future__ import annotations

import random

import pytest

from flowpilot.config import AppSettings
from flowpilot.core.context import RunControl, StepContext
from flowpilot.core.errors import ElementNotFound
from flowpilot.core.healing import apply_heal, fingerprint, is_confident
from flowpilot.core.humanize import Humanizer
from flowpilot.core.steps import new_step
from flowpilot.core.variables import Variables
from tests.conftest import needs_browser

ORIGINAL = """
<h1>Shop</h1>
<button id="buy-btn" data-testid="buy">Buy now</button>
<button id="other">Cancel</button>
<p id="out"></p>
"""
CHANGED = """
<h1>Shop (new design)</h1>
<div class="wrap"><button class="x9f" data-testid="purchase-v2">Buy now</button></div>
<button>Cancel</button>
<p id="out"></p>
<script>document.querySelector('[data-testid="purchase-v2"]').onclick = () => out.textContent = 'bought';</script>
"""


def test_confidence_rules_and_apply():
    assert is_confident(9, 3) and not is_confident(6.5, 0) and not is_confident(9, 8)
    step = new_step("click", selector="#old", fallbacks=["text=\"Buy\""])
    healed = apply_heal(step, "role=button[name=\"Buy now\"]", ["xpath=//button"], {"tag": "button"})
    assert healed.selector == "role=button[name=\"Buy now\"]"
    assert healed.fallbacks == ["xpath=//button", "#old", "text=\"Buy\""]
    assert healed.element_fp == {"tag": "button"}
    assert fingerprint({"tag": "BUTTON", "name": "Buy", "attrs": {"class": "x", "data-testid": "b"},
                        "rect": {"x": 1, "y": 2, "w": 3, "h": 4}}) == {
        "tag": "button", "name": "Buy", "attrs": {"data-testid": "b"}, "rect": {"x": 1, "y": 2, "w": 3, "h": 4}}


@pytest.fixture()
async def page():
    from playwright.async_api import async_playwright

    from flowpilot.adapters.local import find_browser

    pw = await async_playwright().start()
    browser = await pw.chromium.launch(executable_path=find_browser(), headless=True, args=["--no-sandbox"])
    pg = await browser.new_page()
    yield pg
    await browser.close()
    await pw.stop()


def ctx_for(pg, **settings) -> StepContext:
    s = AppSettings(step_timeout_s=1, step_retries=0, **settings)
    s.humanize.mouse_enabled = False
    control = RunControl()
    return StepContext(settings=s, variables=Variables(), control=control,
                       humanizer=Humanizer(s.humanize, sleep=control.sleep, rng=random.Random(0)), page=pg,
                       browser_context=pg.context)


async def record_fp(pg) -> dict:
    from flowpilot.core.recorder import RECORDER_JS

    await pg.evaluate("() => { window.__fpPaused = true; }")
    await pg.evaluate(RECORDER_JS)
    desc = await pg.evaluate("() => window.__fpDescribe(document.getElementById('buy-btn'))")
    return fingerprint(desc)


@needs_browser
async def test_heals_after_page_redesign(page):
    await page.set_content(ORIGINAL)
    fp = await record_fp(page)
    assert fp["name"] == "Buy now" and fp["attrs"]["data-testid"] == "buy"
    step = new_step("click", selector="#buy-btn", fallbacks=['[data-testid="buy"]'], element_fp=fp)
    await page.set_content(CHANGED)
    ctx = ctx_for(page)
    seen = []
    ctx.heal_cb = lambda s, r: seen.append(r)
    await step.run(ctx)
    assert await page.inner_text("#out") == "bought"
    assert len(seen) == 1 and seen[0].selector == '[data-testid="purchase-v2"]'
    assert seen[0].score >= 7
    healed = apply_heal(step, seen[0].selector, seen[0].fallbacks)
    assert "#buy-btn" in healed.fallbacks


@needs_browser
async def test_no_heal_when_element_is_gone_or_disabled(page):
    await page.set_content(ORIGINAL)
    fp = await record_fp(page)
    step = new_step("click", selector="#buy-btn", element_fp=fp)
    await page.set_content("<h1>Sold out</h1><button>Cancel</button>")
    with pytest.raises(ElementNotFound):
        await step.run(ctx_for(page))
    await page.set_content(CHANGED)
    with pytest.raises(ElementNotFound):
        await step.run(ctx_for(page, self_heal=False))
