"""Ready-made flow templates offered when creating a flow."""

from __future__ import annotations

from collections.abc import Callable

from flowpilot.core.steps import StepBase, new_step
from flowpilot.core.steps.base import Delay


def _login() -> list[StepBase]:
    return [
        new_step("open_url", url="https://example.com/login", note="Change to your site's login page."),
        new_step("type_text", text="{{account.email}}", note="Pick the email/username field."),
        new_step("type_text", text="{{account.password}}", sensitive=True, note="Pick the password field."),
        new_step("click", note="Pick the Log in button."),
        new_step("wait_for_load"),
        new_step("screenshot", name="after-login"),
        new_step("close_browser"),
    ]


def _visit_scroll() -> list[StepBase]:
    return [
        new_step("open_url", url="https://example.com"),
        new_step("wait_random", min_seconds=2, max_seconds=5),
        new_step("repeat", times=5),
        new_step("scroll", mode="by_amount", amount=700),
        new_step("wait_random", min_seconds=1.5, max_seconds=4),
        new_step("end_loop"),
        new_step("scroll", mode="to_top"),
        new_step("close_browser"),
    ]


def _search_click() -> list[StepBase]:
    return [
        new_step("open_url", url="https://duckduckgo.com"),
        new_step("type_text", selector='[name="q"]', text="{{account.search_keyword}}", press_enter=True,
                 delay_after=Delay(min=1, max=2)),
        new_step("wait_for_load"),
        new_step("click", note="Pick the result you want to open."),
        new_step("wait_random", min_seconds=5, max_seconds=15),
        new_step("scroll", mode="by_amount", amount=900),
        new_step("close_browser"),
    ]


TEMPLATES: dict[str, tuple[str, Callable[[], list[StepBase]]]] = {
    "Empty flow": ("Start from scratch.", lambda: []),
    "Login": ("Open a page, type email and password from the account, click Log in.", _login),
    "Visit and scroll": ("Open a page and scroll slowly like a reader.", _visit_scroll),
    "Search and click": ("Search a keyword ({{account.search_keyword}}) and open a result.", _search_click),
}
