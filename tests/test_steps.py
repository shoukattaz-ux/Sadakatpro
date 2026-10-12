"""Every step type: model contract (all types) and behaviour on a real page."""

from __future__ import annotations

import csv
import random

import pytest

from flowpilot.config import AppSettings
from flowpilot.core.context import RunControl, StepContext
from flowpilot.core.errors import ElementNotFound, FlowStopped, StepFailed
from flowpilot.core.humanize import Humanizer
from flowpilot.core.steps import CATEGORY_ORDER, StepRegistry, dump_steps, new_step, parse_step, parse_steps
from flowpilot.core.steps.logic import compare
from flowpilot.core.variables import Variables
from tests.conftest import needs_browser

ALL_TYPES = sorted(StepRegistry.all())


@pytest.mark.parametrize("type_name", ALL_TYPES)
def test_step_contract(type_name):
    cls = StepRegistry.get(type_name)
    step = cls()
    assert step.type == type_name
    assert cls.TITLE and cls.CATEGORY in CATEGORY_ORDER and cls.ICON
    assert isinstance(step.summary(), str) and step.summary()
    assert isinstance(step.problems(), list)
    again = parse_step(step.model_dump(mode="json"))
    assert again == step


def test_parse_errors_are_friendly():
    with pytest.raises(ValueError, match="Step 2 could not be read"):
        parse_steps([{"type": "wait"}, {"type": "teleport"}])
    steps = [new_step("click", selector="#a"), new_step("wait", seconds=1)]
    assert parse_steps(dump_steps(steps)) == steps


def test_compare():
    assert compare("10", "greater", "9")
    assert compare("abc", "contains", "B")
    assert compare("", "is_empty", "")
    assert compare("1.0", "equals", "1")
    assert not compare("x", "equals", "y")


# --------------------------------------------------------------------------- real page

@pytest.fixture()
async def page_ctx(tmp_path, site_url):
    from playwright.async_api import async_playwright

    from flowpilot.adapters.local import find_browser

    pw = await async_playwright().start()
    browser = await pw.chromium.launch(executable_path=find_browser(), headless=True, args=["--no-sandbox"])
    context = await browser.new_context()
    page = await context.new_page()
    settings = AppSettings(step_timeout_s=3, step_retries=0)
    settings.humanize.step_delay_min = settings.humanize.step_delay_max = 0
    settings.humanize.typing_min_ms, settings.humanize.typing_max_ms = 1, 5
    settings.humanize.mouse_speed = 5
    control = RunControl()
    ctx = StepContext(settings=settings, variables=Variables(account={"email": "x@y.z"}), control=control,
                      humanizer=Humanizer(settings.humanize, sleep=control.sleep, rng=random.Random(0)),
                      page=page, browser_context=context, browser=browser, artifacts_dir=tmp_path / "art",
                      exports_dir=tmp_path / "exp", account_name="acc")
    await page.goto(f"{site_url}/login.html")
    yield ctx
    await browser.close()
    await pw.stop()


async def run(ctx, type_name, **kw):
    return await new_step(type_name, **kw).run(ctx)


@needs_browser
async def test_navigation_steps(page_ctx, site_url):
    ctx = page_ctx
    await run(ctx, "open_url", url=f"{site_url}/welcome.html?email=a")
    assert "welcome" in ctx.page.url
    await run(ctx, "go_back")
    assert "login" in ctx.page.url
    await run(ctx, "go_forward")
    await run(ctx, "reload")
    await run(ctx, "wait_for_load")
    await run(ctx, "new_tab", url=f"{site_url}/login.html")
    assert len(ctx.browser_context.pages) == 2 and "login" in ctx.page.url
    await run(ctx, "switch_tab", by="url", value="welcome")
    assert "welcome" in ctx.page.url
    await run(ctx, "switch_tab", by="index", value="2")
    assert "login" in ctx.page.url
    await run(ctx, "switch_tab", by="title", value="Welcome")
    await run(ctx, "close_tab")
    assert len(ctx.browser_context.pages) == 1
    await run(ctx, "open_url", url=f"{site_url}/login.html", new_tab=True)
    await run(ctx, "switch_tab", by="newest")
    assert ctx.page == ctx.browser_context.pages[-1]


@pytest.mark.parametrize("human", [False, True])
@needs_browser
async def test_interaction_steps(page_ctx, human):
    ctx = page_ctx
    hm = "on" if human else "off"
    await run(ctx, "type_text", selector="#email", text="{{account.email}}", humanize=hm)
    assert await ctx.page.input_value("#email") == "x@y.z"
    await run(ctx, "clear_field", selector="#email")
    assert await ctx.page.input_value("#email") == ""
    await run(ctx, "focus", selector="#pw")
    await run(ctx, "press_key", key="a", times=3)
    assert await ctx.page.input_value("#pw") == "aaa"
    await run(ctx, "select_option", selector="#country", by="label", value="Germany")
    assert await ctx.page.input_value("#country") == "de"
    await run(ctx, "select_option", selector="#country", by="index", value="0")
    await run(ctx, "check", selector="#remember", humanize=hm)
    assert await ctx.page.is_checked("#remember")
    await run(ctx, "uncheck", selector="#remember", humanize=hm)
    assert not await ctx.page.is_checked("#remember")
    await run(ctx, "scroll", mode="to_bottom", humanize=hm)
    assert await ctx.page.evaluate("window.scrollY") > 100
    await run(ctx, "scroll", mode="to_top", humanize=hm)
    await run(ctx, "scroll", mode="to_element", selector="#result", humanize=hm)
    await run(ctx, "hover", selector="[data-testid=\"login-btn\"]", humanize=hm)
    await ctx.page.wait_for_selector("#later", state="visible")
    await run(ctx, "click", selector="#later", humanize=hm)
    assert await ctx.page.inner_text("#later") == "clicked"
    await run(ctx, "double_click", selector="#later", humanize=hm)
    await run(ctx, "right_click", selector="#later", humanize=hm)


@needs_browser
async def test_upload_and_drag(page_ctx, tmp_path):
    ctx = page_ctx
    await ctx.page.set_content("""
      <input type=file id=f>
      <div id=src draggable=true style='width:50px;height:50px;background:red'>drag</div>
      <div id=dst style='width:80px;height:80px;background:blue;margin-top:40px'>drop</div>
      <script>
        dst.addEventListener('dragover', e => e.preventDefault());
        dst.addEventListener('drop', e => { e.preventDefault(); dst.textContent = 'dropped'; });
      </script>""")
    f = tmp_path / "a.txt"
    f.write_text("hi")
    await run(ctx, "upload_file", selector="#f", files=[str(f)])
    assert await ctx.page.evaluate("document.getElementById('f').files.length") == 1
    with pytest.raises(StepFailed, match="File not found"):
        await run(ctx, "upload_file", selector="#f", files=[str(tmp_path / "nope.txt")])
    await run(ctx, "drag_drop", selector="#src", target_selector="#dst", humanize="off")
    assert await ctx.page.inner_text("#dst") == "dropped"


@needs_browser
async def test_waiting_steps(page_ctx):
    ctx = page_ctx
    await run(ctx, "wait", seconds=0.1)
    await run(ctx, "wait_random", min_seconds=0.05, max_seconds=0.1)
    await run(ctx, "wait_for_element", selector="#later")
    await ctx.page.evaluate("document.getElementById('later').style.display='none'")
    await run(ctx, "wait_for_element", selector="#later", state="hidden")
    await run(ctx, "wait_for_url", contains="login")
    await run(ctx, "wait_for_text", text="sign in")
    await run(ctx, "wait_for_network_idle")
    with pytest.raises(ElementNotFound, match="Could not find"):
        await run(ctx, "wait_for_element", selector="#ghost", timeout=0.5)
    with pytest.raises(StepFailed, match="never contained"):
        await run(ctx, "wait_for_url", contains="zzz", timeout=0.5)


@needs_browser
async def test_data_steps(page_ctx, tmp_path):
    ctx = page_ctx
    await run(ctx, "extract", selector="h1", variable="title")
    assert ctx.variables.get("title") == "Sign in"
    await run(ctx, "extract", selector="#email", attribute="placeholder", variable="ph")
    assert ctx.variables.get("ph") == "Email address"
    await ctx.page.fill("#email", "order 12345 ok")
    await run(ctx, "extract", selector="#email", attribute="value", variable="num", regex=r"(\d+)")
    assert ctx.variables.get("num") == "12345"
    await run(ctx, "set_variable", variable="greet", value="hi {{account.email}}")
    assert ctx.variables.get("greet") == "hi x@y.z"
    await run(ctx, "random_item", items=["a", "b"], variable="pick")
    assert ctx.variables.get("pick") in ("a", "b")
    await run(ctx, "random_number", minimum=5, maximum=6, variable="n")
    assert ctx.variables.get("n") in ("5", "6")
    await run(ctx, "get_url", variable="u")
    assert "login" in ctx.variables.get("u")
    data = tmp_path / "rows.csv"
    data.write_text("city;zip\nBerlin;10115\nParis;75001\n", "utf-8")
    for expected in ("Berlin", "Paris", "Berlin"):
        await run(ctx, "read_csv_row", file=str(data))
        assert ctx.variables.get("row.city") == expected
    with pytest.raises(StepFailed, match="not found"):
        await run(ctx, "read_csv_row", file=str(tmp_path / "missing.csv"))
    ctx.account_position = 5
    with pytest.raises(StepFailed, match="already used"):
        await run(ctx, "read_csv_row", file=str(data), mode="same_as_account", when_finished="fail")
    ctx.account_position = 1
    await run(ctx, "read_csv_row", file=str(data), mode="same_as_account")
    assert ctx.variables.get("row.city") == "Paris"
    await run(ctx, "save_value", values=["city = {{row.city}}"], file="out.csv")
    await run(ctx, "save_value", values=["city = second"], file="out.csv")
    rows = list(csv.DictReader((ctx.exports_dir / "out.csv").open(encoding="utf-8")))
    assert [r["city"] for r in rows] == ["Paris", "second"] and rows[0]["account"] == "acc"
    assert ctx.results["city"] == "second"


@needs_browser
async def test_logic_conditions(page_ctx):
    ctx = page_ctx
    assert await new_step("if_element", selector="h1").evaluate(ctx)
    assert not await new_step("if_element", selector="#ghost", wait_seconds=0.2).evaluate(ctx)
    assert await new_step("if_element", selector="#ghost", wait_seconds=0.2, negate=True).evaluate(ctx)
    assert await new_step("if_text", text="SIGN IN").evaluate(ctx)
    assert await new_step("if_text", selector="h1", text="sign").evaluate(ctx)
    assert not await new_step("if_text", selector="#ghost", text="x").evaluate(ctx)
    ctx.variables.set("v", "7")
    assert await new_step("if_variable", variable="v", operator="less", value="10").evaluate(ctx)
    assert await new_step("repeat", times=2).items(ctx) == [1, 2]
    assert await new_step("loop_list", items_list=["{{v}}", "b"]).items(ctx) == ["7", "b"]
    ctx.variables.set("lst", "a, b ,c")
    assert await new_step("loop_list", source="variable", variable="lst").items(ctx) == ["a", "b", "c"]


@needs_browser
async def test_utility_and_ending_steps(page_ctx):
    ctx = page_ctx
    shots = []
    ctx.screenshot_cb = shots.append
    await run(ctx, "screenshot", name="hello world", full_page=True)
    assert shots and shots[0].endswith("_hello_world.png")
    logs = []
    ctx.log = lambda level, msg: logs.append((level, msg))
    await run(ctx, "log", message="mail {{account.email}}", level="warning")
    assert logs == [("warning", "mail x@y.z")]
    await run(ctx, "run_js", code="return 1 + 2;", variable="js")
    assert ctx.variables.get("js") == "3"
    await run(ctx, "run_js", code="return document.title;", variable="t")
    assert ctx.variables.get("t") == "Test Login"
    notes = []
    ctx.notify_cb = notes.append
    await run(ctx, "notify", message="hey", sound=False)
    assert notes == ["acc: hey"]

    import asyncio

    task = asyncio.ensure_future(run(ctx, "pause_manual", message="solve captcha"))
    await asyncio.sleep(0.3)
    assert ctx.control.manual_message == "solve captcha" and not task.done()
    ctx.control.resume()
    await asyncio.wait_for(task, 2)

    with pytest.raises(FlowStopped) as stop:
        await run(ctx, "mark_done", message="ok")
    assert stop.value.success and ctx.forced_status == "done"
    with pytest.raises(FlowStopped) as stop:
        await run(ctx, "mark_failed", message="bad")
    assert not stop.value.success

    closed = []

    async def close_cb():
        closed.append(True)

    ctx.close_browser_cb = close_cb
    await run(ctx, "close_browser")
    await run(ctx, "close_browser")  # second time is a no-op
    assert closed == [True] and ctx.browser_closed
    from flowpilot.core.errors import BrowserDisconnected

    with pytest.raises(BrowserDisconnected, match="already closed"):
        await run(ctx, "reload")
