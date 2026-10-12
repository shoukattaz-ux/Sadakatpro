"""End-to-end: real Chromium profiles via the Local Chrome adapter, driven by the RunEngine.

Covers the acceptance criteria: staggered launches with concurrency, sequential groups with
a group delay, account variables, Close browser stopping the profile, friendly errors with
screenshots on a missing element, and Stop closing every browser.
"""

from __future__ import annotations

import asyncio
import json
import time

import pytest

from flowpilot.core.engine import RunOptions, TargetSpec
from flowpilot.core.steps import dump_steps, new_step
from flowpilot.storage.models import Account, Connection
from tests.conftest import needs_browser

pytestmark = needs_browser


def fast(services) -> None:
    s = services.settings.settings
    s.humanize.step_delay_min = s.humanize.step_delay_max = 0.0
    s.humanize.mouse_speed = 4.0
    s.humanize.typing_min_ms, s.humanize.typing_max_ms = 5, 15
    s.step_timeout_s = 5
    s.step_retries = 0
    s.resource_guard = False


def setup_accounts(services, groups: dict[str, int]) -> list[Account]:
    conn = services.repos.connections.save(
        Connection(type="local", name="Local", base_url="",
                   extra_json=json.dumps({"headless": "1", "profiles_dir": str(services.paths.root / "profiles")})))
    accounts = []
    n = 0
    for gname, count in groups.items():
        g = services.repos.groups.create(gname)
        for _ in range(count):
            n += 1
            acc = services.repos.accounts.save(
                Account(name=f"acc{n}", connection_id=conn.id, remote_profile_id=f"p{n}", group_id=g.id),
                variables={"email": f"user{n}@test.dev", "password": f"pw{n}"})
            accounts.append(acc)
    return accounts


def login_flow(site_url: str, extra_end: list | None = None):
    steps = [
        new_step("open_url", url=f"{site_url}/login.html"),
        new_step("type_text", selector="internal:label=\"Email\"i", fallbacks=["#email"], text="{{account.email}}"),
        new_step("type_text", selector="#pw", text="{{account.password}}", sensitive=True),
        new_step("select_option", selector="#country", by="label", value="Germany"),
        new_step("check", selector="#remember"),
        new_step("click", selector="[data-testid=\"login-btn\"]"),
        new_step("wait_for_url", contains="welcome"),
        new_step("extract", selector="#who", variable="who"),
        new_step("save_value", values=["who = {{who}}", "url = {{account.email}}"]),
        *(extra_end or [new_step("close_browser")]),
    ]
    return json.dumps(dump_steps(steps))


async def wait_finished(services, run_id: int, timeout: float = 120) -> None:
    deadline = time.monotonic() + timeout
    while run_id in services.engine.runs:
        assert time.monotonic() < deadline, "run did not finish"
        await asyncio.sleep(0.2)


async def test_concurrency_stagger_groups_and_close_browser(services, site_url):
    fast(services)
    accounts = setup_accounts(services, {"A": 3, "B": 2})
    flow = services.repos.flows.create("Login", login_flow(site_url))
    events = []
    services.engine.subscribe(lambda e: events.append((time.monotonic(), e)))
    opts = RunOptions(concurrency=3, launch_delay_min=1.0, launch_delay_max=1.5, group_delay_min=1.0, group_delay_max=1.0)
    run_id = await services.engine.start(flow, TargetSpec(mode="all"), opts)
    await wait_finished(services, run_id)

    run = services.repos.runs.get(run_id)
    items = services.repos.runs.items(run_id)
    assert run.status == "done", [(i.account_name, i.status, i.error) for i in items]
    assert run.succeeded == 5 and run.failed == 0
    # account variables reached the page
    for it in items:
        results = json.loads(it.results_json)
        n = it.account_name.removeprefix("acc")
        assert results["who"] == f"user{n}@test.dev"

    starts = {}
    finishes = {}
    for t, e in events:
        if e.kind == "item_status" and e.data["status"] == "starting":
            starts[e.item_id] = t
        if e.kind == "item_status" and e.data["status"] == "done":
            finishes[e.item_id] = t
    by_group = {"A": [], "B": []}
    for it in items:
        by_group[it.group_name].append(it.id)
    a_starts = sorted(starts[i] for i in by_group["A"])
    # staggered: consecutive launches at least ~1s apart
    assert all(b - a >= 0.95 for a, b in zip(a_starts, a_starts[1:])), a_starts
    # all of group A ran in parallel: the 3rd started before the 1st finished
    assert a_starts[2] < min(finishes[i] for i in by_group["A"])
    # group B only after all of A finished + group delay
    assert min(starts[i] for i in by_group["B"]) >= max(finishes[i] for i in by_group["A"]) + 0.95
    # Close browser stopped every profile
    adapter = services.adapters.get(services.repos.connections.list()[0])
    for acc in accounts:
        assert not await adapter.is_running(acc.remote_profile_id)


async def test_missing_element_friendly_error_and_screenshot(services, site_url):
    fast(services)
    setup_accounts(services, {"A": 1})
    steps = [
        new_step("open_url", url=f"{site_url}/login.html"),
        new_step("click", selector="#does-not-exist", timeout=1.5),
        new_step("close_browser"),
    ]
    flow = services.repos.flows.create("Broken", json.dumps(dump_steps(steps)))
    run_id = await services.engine.start(flow, TargetSpec(), RunOptions(launch_delay_min=0, launch_delay_max=0))
    await wait_finished(services, run_id)
    item = services.repos.runs.items(run_id)[0]
    assert item.status == "failed"
    assert "Could not find" in item.error and "1.5 seconds" in item.error
    assert item.screenshot_path and item.html_path
    adapter = services.adapters.get(services.repos.connections.list()[0])
    assert not await adapter.is_running("p1")


async def test_fallback_selector_waits_and_on_error_policy(services, site_url):
    fast(services)
    setup_accounts(services, {"A": 1})
    steps = [
        new_step("open_url", url=f"{site_url}/login.html"),
        new_step("click", selector="#nope", fallbacks=["#later"], timeout=5),  # appears after 1.2s
        new_step("extract", selector="#later", variable="txt"),
        new_step("click", selector="#also-missing", timeout=0.5, on_error="skip"),
        new_step("save_value", values=["txt = {{txt}}"]),
        new_step("close_browser"),
    ]
    flow = services.repos.flows.create("Fallback", json.dumps(dump_steps(steps)))
    run_id = await services.engine.start(flow, TargetSpec(), RunOptions(launch_delay_min=0, launch_delay_max=0))
    await wait_finished(services, run_id)
    item = services.repos.runs.items(run_id)[0]
    assert item.status == "done", item.error
    assert json.loads(item.results_json)["txt"] == "clicked"


async def test_stop_mid_run_closes_browsers(services, site_url):
    fast(services)
    accounts = setup_accounts(services, {"A": 2})
    steps = [new_step("open_url", url=f"{site_url}/login.html"), new_step("wait", seconds=60), new_step("close_browser")]
    flow = services.repos.flows.create("Long", json.dumps(dump_steps(steps)))
    run_id = await services.engine.start(flow, TargetSpec(), RunOptions(concurrency=2, launch_delay_min=0.2,
                                                                        launch_delay_max=0.3))
    adapter = services.adapters.get(services.repos.connections.list()[0])
    for _ in range(100):
        if all([await adapter.is_running(a.remote_profile_id) for a in accounts]):
            break
        await asyncio.sleep(0.2)
    await asyncio.sleep(1.0)
    services.engine.stop_all()
    await wait_finished(services, run_id, timeout=30)
    items = services.repos.runs.items(run_id)
    assert {i.status for i in items} == {"stopped"}
    assert services.repos.runs.get(run_id).status == "stopped"
    for acc in accounts:
        assert not await adapter.is_running(acc.remote_profile_id)


async def test_new_tab_auto_switch_and_humanized_actions(services, site_url):
    fast(services)
    s = services.settings.settings
    s.humanize.mouse_enabled = True
    setup_accounts(services, {"A": 1})
    steps = [
        new_step("open_url", url=f"{site_url}/login.html"),
        new_step("scroll", mode="to_bottom"),
        new_step("scroll", mode="to_top"),
        new_step("type_text", selector="#email", text="human@test.dev"),
        new_step("hover", selector="[data-testid=\"login-btn\"]"),
        new_step("click", selector="#popup"),
        new_step("wait_for_url", contains="popup=1"),
        new_step("get_url", variable="u"),
        new_step("switch_tab", by="index", value="1"),
        new_step("get_url", variable="first"),
        new_step("save_value", values=["u = {{u}}", "first = {{first}}"]),
        new_step("close_browser"),
    ]
    flow = services.repos.flows.create("Tabs", json.dumps(dump_steps(steps)))
    run_id = await services.engine.start(flow, TargetSpec(), RunOptions(launch_delay_min=0, launch_delay_max=0))
    await wait_finished(services, run_id)
    item = services.repos.runs.items(run_id)[0]
    assert item.status == "done", item.error
    res = json.loads(item.results_json)
    assert "popup=1" in res["u"]


@pytest.mark.parametrize("dummy", [0])
async def test_test_mode_runs_first_account_only(services, site_url, dummy):
    fast(services)
    setup_accounts(services, {"A": 3})
    flow = services.repos.flows.create("Login", login_flow(site_url))
    run_id = await services.engine.start(flow, TargetSpec(), RunOptions(test_mode=True, launch_delay_min=0,
                                                                        launch_delay_max=0))
    await wait_finished(services, run_id)
    items = services.repos.runs.items(run_id)
    assert len(items) == 1 and items[0].status == "done"
