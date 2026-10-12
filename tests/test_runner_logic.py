"""Runner control flow (If/Else, loops, Go to, Try, error policies) without a browser."""

from __future__ import annotations

import random

import pytest

from flowpilot.config import AppSettings
from flowpilot.core.context import RunControl, StepContext
from flowpilot.core.errors import StepFailed, ValidationProblem
from flowpilot.core.humanize import Humanizer
from flowpilot.core.runner import FlowRunner, compile_flow, flow_problems
from flowpilot.core.steps import new_step
from flowpilot.core.variables import Variables


def make_ctx(**account) -> StepContext:
    settings = AppSettings(step_retries=0, step_retry_delay_s=0)
    settings.humanize.mouse_enabled = False
    logs: list[tuple[str, str]] = []
    ctx = StepContext(settings=settings, variables=Variables(account=account), control=RunControl(),
                      humanizer=Humanizer(settings.humanize, rng=random.Random(1)))
    ctx.log = lambda level, msg: logs.append((level, msg))
    ctx.logs = logs  # type: ignore[attr-defined]
    return ctx


def S(type_name, **kw):  # noqa: N802
    return new_step(type_name, **kw)


def FAIL(**kw):  # noqa: N802
    """A step that always fails with a normal (non-browser) error."""
    return new_step("read_csv_row", file="/definitely/missing.csv", **kw)


async def run(steps, ctx=None):
    ctx = ctx or make_ctx()
    outcome = await FlowRunner(steps, ctx).run()
    return outcome, ctx


async def test_if_else():
    steps = [
        S("set_variable", variable="x", value="5"),
        S("if_variable", variable="x", operator="greater", value="3"),
        S("set_variable", variable="r", value="big"),
        S("else"),
        S("set_variable", variable="r", value="small"),
        S("end_if"),
    ]
    outcome, ctx = await run(steps)
    assert outcome.success and ctx.variables.get("r") == "big"
    steps[0] = S("set_variable", variable="x", value="1")
    _, ctx = await run(steps)
    assert ctx.variables.get("r") == "small"


async def test_nested_loops_and_break():
    steps = [
        S("set_variable", variable="out", value=""),
        S("repeat", times=3),
        S("loop_list", items_list=["a", "b", "c"]),
        S("if_variable", variable="loop.item", operator="equals", value="c"),
        S("break_loop"),
        S("end_if"),
        S("set_variable", variable="out", value="{{out}}{{loop.item}}"),
        S("end_loop"),
        S("end_loop"),
    ]
    outcome, ctx = await run(steps)
    assert outcome.success
    assert ctx.variables.get("out") == "ababab"


async def test_goto_and_label_with_counter():
    steps = [
        S("set_variable", variable="n", value="0"),
        S("label_marker", name="top"),
        S("set_variable", variable="n", value="{{n}}1"),
        S("if_variable", variable="n", operator="not_equals", value="0111"),
        S("goto", goto_label="top"),
        S("end_if"),
    ]
    outcome, ctx = await run(steps)
    assert outcome.success and ctx.variables.get("n") == "0111"


async def test_goto_max_jumps_protects_endless_loop():
    steps = [S("label_marker", name="a"), S("goto", goto_label="a", max_jumps=5)]
    with pytest.raises(StepFailed, match="more than 5 times"):
        await run(steps)


async def test_try_catch_receives_error():
    steps = [
        S("try"),
        FAIL(),
        S("set_variable", variable="after", value="should not run"),
        S("catch"),
        S("set_variable", variable="caught", value="{{error.message}}"),
        S("end_try"),
        S("set_variable", variable="end", value="yes"),
    ]
    outcome, ctx = await run(steps)
    assert outcome.success
    assert ctx.variables.get("after") is None
    assert ctx.variables.get("caught")
    assert ctx.variables.get("end") == "yes"


async def test_on_error_skip_and_goto():
    steps = [
        FAIL(on_error="skip"),
        S("set_variable", variable="a", value="1"),
        FAIL(on_error="goto", on_error_label="recover"),
        S("set_variable", variable="b", value="never"),
        S("label_marker", name="recover"),
        S("set_variable", variable="c", value="{{error.message}}"),
    ]
    outcome, ctx = await run(steps)
    assert outcome.success
    assert ctx.variables.get("a") == "1" and ctx.variables.get("b") is None
    assert "not found" in ctx.variables.get("c")


async def test_stop_policy_fails_flow_with_friendly_message():
    with pytest.raises(StepFailed) as info:
        await run([FAIL()])
    assert str(info.value).startswith("Step 1 ") and "CSV file not found" in str(info.value)


async def test_missing_browser_is_disconnect():
    from flowpilot.core.errors import BrowserDisconnected

    with pytest.raises(BrowserDisconnected):
        await run([S("go_back")])


async def test_stop_flow_and_mark_failed():
    outcome, _ = await run([S("stop_flow", result="fail", message="nope"), S("set_variable", variable="x", value="1")])
    assert not outcome.success and outcome.message == "nope"
    outcome, _ = await run([S("mark_failed", message="bad account")])
    assert not outcome.success and outcome.message == "bad account"
    outcome, _ = await run([S("mark_done", stop=False), S("set_variable", variable="x", value="1")])
    assert outcome.success


async def test_disabled_block_is_skipped():
    steps = [
        S("repeat", times=3, enabled=False),
        S("set_variable", variable="x", value="ran"),
        S("end_loop"),
    ]
    _, ctx = await run(steps)
    assert ctx.variables.get("x") is None


def test_structure_problems():
    steps = [S("if_variable", variable="x"), S("else"), S("end_loop")]
    problems = flow_problems(steps)
    assert 2 in problems and 0 in problems
    with pytest.raises(ValidationProblem):
        compile_flow(steps)
    assert flow_problems([S("click")])[0] == ["Choose an element (use 'Pick element')."]
    assert flow_problems([S("goto", goto_label="nowhere")])[0][0].startswith("There is no label")


async def test_account_variables_and_retry():
    ctx = make_ctx(email="a@b.c")
    ctx.settings.step_retries = 2
    steps = [S("set_variable", variable="mail", value="{{account.email}}"), FAIL(on_error="skip")]
    outcome, ctx = await run(steps, ctx)
    assert outcome.success and ctx.variables.get("mail") == "a@b.c"
    assert any("retrying" in m for _, m in ctx.logs)  # type: ignore[attr-defined]


async def test_pause_and_stop_control():
    import asyncio

    from flowpilot.core.errors import StopRequested

    control = RunControl()
    control.pause()

    async def stopper():
        await asyncio.sleep(0.3)
        control.stop()

    asyncio.get_running_loop().create_task(stopper())
    with pytest.raises(StopRequested):
        await control.sleep(5)
