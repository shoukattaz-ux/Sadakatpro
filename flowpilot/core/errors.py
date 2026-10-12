"""Exceptions used by the runner and translation of technical errors to plain language."""

from __future__ import annotations

import re


class FlowError(Exception):
    """Base class for errors raised while running a flow. ``str()`` is user friendly."""

    def __init__(self, message: str, detail: str = "") -> None:
        super().__init__(message)
        self.detail = detail


class StepFailed(FlowError):
    """A step could not be completed."""


class ElementNotFound(StepFailed):
    """No candidate selector matched an element in time."""


class FlowStopped(Exception):
    """Raised by the 'Stop flow' step or a stop policy to end the flow early."""

    def __init__(self, success: bool, message: str = "", close_browser: bool = False) -> None:
        super().__init__(message)
        self.success = success
        self.message = message
        self.close_browser = close_browser


class StopRequested(Exception):
    """The user pressed Stop; the run must end and clean up."""


class BrowserDisconnected(FlowError):
    """The browser was closed or crashed."""


class ValidationProblem(FlowError):
    """A flow cannot run because of a configuration problem (e.g. missing selector)."""


_RULES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"Target (page, context or browser|closed)|Browser has been closed|browser has disconnected|"
                r"Connection closed|has been closed", re.I),
     "The browser was closed or disconnected."),
    (re.compile(r"net::ERR_NAME_NOT_RESOLVED", re.I), "The website address could not be found (check the URL)."),
    (re.compile(r"net::ERR_INTERNET_DISCONNECTED|net::ERR_PROXY|net::ERR_TUNNEL", re.I),
     "No internet connection or the profile's proxy is not working."),
    (re.compile(r"net::ERR_CONNECTION_(REFUSED|RESET|TIMED_OUT|CLOSED)", re.I), "The website did not respond."),
    (re.compile(r"net::ERR_CERT", re.I), "The website's security certificate is not valid."),
    (re.compile(r"Cannot navigate to invalid URL|invalid URL", re.I), "The URL is not valid. It should start with https://"),
    (re.compile(r"strict mode violation", re.I), "More than one element matches; make the selector more specific."),
    (re.compile(r"not an? <select>|Element is not a <select>", re.I), "The element is not a dropdown list."),
    (re.compile(r"Element is not attached|detached", re.I), "The element disappeared from the page while working on it."),
    (re.compile(r"element is not visible|not visible", re.I), "The element is on the page but hidden."),
    (re.compile(r"intercepts pointer events", re.I), "Something is covering the element (a popup or banner?)."),
    (re.compile(r"Timeout \d+ms exceeded|TimeoutError", re.I), "The page took too long to respond."),
    (re.compile(r"ECONNREFUSED|connect_over_cdp|WebSocket error", re.I),
     "Could not connect to the browser profile. Is it still open?"),
    (re.compile(r"SyntaxError|ReferenceError", re.I), "The JavaScript code has an error."),
]


def friendly_error(exc: BaseException | str) -> str:
    """Return a short plain-language description of ``exc`` (no stack traces)."""
    if isinstance(exc, FlowError):
        return str(exc)
    text = str(exc)
    for pattern, message in _RULES:
        if pattern.search(text):
            return message
    first_line = text.strip().splitlines()[0] if text.strip() else type(exc).__name__ if not isinstance(exc, str) else ""
    return f"Something went wrong: {first_line[:200]}" if first_line else "Something went wrong."


def is_disconnect(exc: BaseException) -> bool:
    return isinstance(exc, BrowserDisconnected) or bool(_RULES[0][0].search(str(exc)))
