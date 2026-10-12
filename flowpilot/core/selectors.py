"""Robust selector generation and resolution with fallbacks.

Generation priority (most stable first):

1. ``data-testid`` / ``data-test`` / ``data-qa`` / other ``data-*`` attributes
2. ARIA role + accessible name (``role=button[name="Log in"]``)
3. Visible text (``text="Log in"``)
4. Label / placeholder / aria-label
5. A stable ``id`` (random looking ids are skipped)
6. A short CSS path
7. XPath (last resort)

The in-page recorder script gathers an *element descriptor* (tag, attributes, text, label,
match counts ...); :func:`candidates_from_descriptor` turns it into an ordered list, so the
logic lives in Python and is unit tested.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any

from flowpilot.core.errors import ElementNotFound

PREFERRED_DATA_ATTRS = ("data-testid", "data-test-id", "data-test", "data-qa", "data-cy", "data-automation-id")
IGNORED_DATA_ATTRS = ("data-reactid", "data-v-", "data-styled", "data-emotion", "data-radix", "data-state",
                      "data-headlessui", "data-darkreader", "data-gtm", "data-ved", "data-hveid")
INTERACTIVE_ROLES = {"button", "link", "checkbox", "radio", "textbox", "combobox", "menuitem", "tab", "option",
                     "switch", "searchbox", "listbox", "slider", "spinbutton"}

_DYNAMIC_PATTERNS = [
    re.compile(r"\d{4,}"),  # long numbers
    re.compile(r"^[a-z]{1,3}-?[0-9a-z]{5,}$", re.I),  # e.g. css-1x9fz2, sc-abc123
    re.compile(r"^(css|sc|jsx|emotion|svelte|makeStyles|jss)-", re.I),
    re.compile(r"_[a-zA-Z0-9]{5,}$"),  # CSS-modules hash suffix
    re.compile(r"^[0-9a-f]{8,}$", re.I),  # hex hash
    re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-", re.I),  # uuid
    re.compile(r"^(ember|react|radix|headlessui|mui|rc|ext-gen|yui|gwt|j_id|ng-)[-_:]?\w*\d", re.I),
    re.compile(r":r[0-9a-z]+:", re.I),  # React useId
]


def looks_dynamic(token: str) -> bool:
    """True if an id/class looks auto-generated (likely to change between page loads)."""
    if not token or len(token) > 60:
        return True
    digits = sum(c.isdigit() for c in token)
    if digits >= 3 and digits / len(token) > 0.3:
        return True
    return any(p.search(token) for p in _DYNAMIC_PATTERNS)


def quote(value: str) -> str:
    """Quote a string for use inside a Playwright/CSS attribute selector."""
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def css_ident(value: str) -> str:
    """Escape a CSS identifier (for ``#id`` selectors)."""
    out = []
    for i, ch in enumerate(value):
        if ch.isalnum() or ch in "-_" or ord(ch) > 127:
            if i == 0 and ch.isdigit():
                out.append(f"\\3{ch} ")
            else:
                out.append(ch)
        else:
            out.append("\\" + ch)
    return "".join(out)


def _clean_text(text: str | None, limit: int = 60) -> str:
    text = re.sub(r"\s+", " ", (text or "")).strip()
    return text if 0 < len(text) <= limit else ""


def candidates_from_descriptor(desc: dict[str, Any], max_candidates: int = 4) -> list[str]:
    """Build an ordered list of selectors (primary first) from an element descriptor.

    ``desc`` keys: ``tag``, ``id``, ``attrs`` (dict), ``role``, ``name``, ``text``, ``label``,
    ``css`` (short CSS path), ``xpath`` and ``counts``: how many elements on the page share
    that property, keyed by kind (an attribute name, ``id``, ``text``, ``role_name``, ``label``,
    ``css``). Candidates known to match more than one element are moved to the end.
    """
    tag = str(desc.get("tag") or "").lower()
    attrs: dict[str, str] = {k: str(v) for k, v in (desc.get("attrs") or {}).items() if v is not None}
    counts: dict[str, int] = {k: int(v) for k, v in (desc.get("counts") or {}).items()}
    found: list[tuple[str, str]] = []  # (selector, kind)

    def add(sel: str, kind: str) -> None:
        if sel and sel not in (s for s, _ in found):
            found.append((sel, kind))

    def attr_sel(name: str, value: str, with_tag: bool = True) -> str:
        return f"{tag}[{name}={quote(value)}]" if tag and with_tag else f"[{name}={quote(value)}]"

    # 1. data-* attributes
    for name in PREFERRED_DATA_ATTRS:
        if attrs.get(name):
            add(attr_sel(name, attrs[name], with_tag=False), name)
    for name, value in attrs.items():
        if (name.startswith("data-") and name not in PREFERRED_DATA_ATTRS and value and len(value) <= 50
                and not name.startswith(IGNORED_DATA_ATTRS) and not looks_dynamic(value)):
            add(attr_sel(name, value), name)
    # 2. role + accessible name
    role = str(desc.get("role") or "").lower()
    name = _clean_text(desc.get("name"))
    if role in INTERACTIVE_ROLES and name:
        add(f"role={role}[name={quote(name)}]", "role_name")
    # 3. visible text (only for clickable things with short text)
    text = _clean_text(desc.get("text"), 50)
    if text and (role in {"button", "link", "menuitem", "tab", "option"} or tag in {"button", "a", "label", "span"}):
        add(f"text={quote(text)}", "text")
    # 4. label / placeholder / aria-label / name / title / alt
    label = _clean_text(desc.get("label"))
    if label:
        add(f"internal:label={quote(label)}i", "label")
    for attr in ("placeholder", "aria-label"):
        if attrs.get(attr):
            add(attr_sel(attr, attrs[attr]), attr)
    if attrs.get("name") and not looks_dynamic(attrs["name"]):
        add(attr_sel("name", attrs["name"]), "name")
    for attr in ("title", "alt"):
        if attrs.get(attr) and len(attrs[attr]) <= 60:
            add(attr_sel(attr, attrs[attr]), attr)
    # 5. stable id
    elem_id = str(desc.get("id") or "")
    if elem_id and not looks_dynamic(elem_id):
        add(f"#{css_ident(elem_id)}", "id")
    # 6. short css path
    if desc.get("css"):
        add(str(desc["css"]), "css")
    # 7. xpath
    xpath = ""
    if desc.get("xpath"):
        xp = str(desc["xpath"])
        xpath = xp if xp.startswith("xpath=") else f"xpath={xp}"

    unique = [s for s, kind in found if counts.get(kind, 1) == 1]
    ambiguous = [s for s, kind in found if counts.get(kind, 1) > 1]
    ordered = unique + ambiguous
    if xpath:  # XPath is always the last resort
        ordered = ordered[: max_candidates - 1] + [xpath]
    return ordered[:max_candidates]


def describe_selector(selector: str) -> str:
    """Plain-English description of a selector for step summaries."""
    m = re.match(r'role=(\w+)\[name="(.+)"\]', selector)
    if m:
        return f"the “{m.group(2)}” {m.group(1)}"
    m = re.match(r'text="(.+)"', selector)
    if m:
        return f"“{m.group(1)}”"
    m = re.match(r'internal:label="(.+)"i?', selector)
    if m:
        return f"the “{m.group(1)}” field"
    m = re.search(r'\[(placeholder|aria-label|title|alt)="(.+?)"\]', selector)
    if m:
        return f"the “{m.group(2)}” element"
    m = re.search(r'\[(data-[\w-]+)="(.+?)"\]', selector)
    if m:
        return f"“{m.group(2)}”"
    if selector.startswith("#"):
        return f"element #{selector[1:]}"
    return selector if len(selector) <= 40 else selector[:37] + "..."


def convert_devtools_selector(parts: list[str] | str) -> str | None:
    """Convert one Chrome DevTools Recorder selector to Playwright syntax."""
    if isinstance(parts, list):
        if len(parts) != 1:  # frames / shadow chains: Playwright's css pierces open shadow roots
            parts = [p for p in parts if p][-1:] or [""]
        sel = parts[0]
    else:
        sel = parts
    if sel.startswith("aria/"):
        body = sel[5:]
        m = re.match(r'(.*)\[role="(\w+)"\]$', body)
        if m:
            return f"role={m.group(2)}[name={quote(m.group(1))}]"
        return f"internal:label={quote(body)}i" if body else None
    if sel.startswith("xpath/"):
        return "xpath=" + sel[6:]
    if sel.startswith("pierce/"):
        return sel[7:]
    if sel.startswith("text/"):
        return f"text={quote(sel[5:])}"
    return sel or None


async def resolve_locator(page: Any, selectors: list[str], timeout_s: float, state: str = "visible",
                          check: Any = None) -> tuple[Any, str]:
    """Return ``(locator, selector)`` for the first selector that matches an element.

    All candidates are polled in order until ``timeout_s`` passes, so a slow page still
    gets the full timeout and a fallback can win if the primary selector never appears.
    ``state`` is ``visible`` or ``attached``. ``check`` is an optional async callable used
    between polls (e.g. pause/stop handling).
    """
    candidates = [s for s in selectors if s and s.strip()]
    if not candidates:
        raise ElementNotFound("This step has no selector. Use 'Pick element' to choose one.")
    loop = asyncio.get_running_loop()
    deadline = loop.time() + max(0.1, timeout_s)
    last_error = ""
    while True:
        for sel in candidates:
            try:
                loc = page.locator(sel).first
                if await loc.count() > 0 and (state != "visible" or await loc.is_visible()):
                    return loc, sel
            except Exception as exc:  # invalid selector syntax, navigation in progress...
                last_error = str(exc)
        if loop.time() >= deadline:
            break
        if check is not None:
            await check()
        await asyncio.sleep(0.25)
    shown = describe_selector(candidates[0])
    raise ElementNotFound(
        f"Could not find {shown} on the page after {timeout_s:g} seconds.",
        detail=f"Tried: {candidates}. {last_error}".strip(),
    )
