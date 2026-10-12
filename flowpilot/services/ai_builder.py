"""AI step builder: turn a plain-English request into draft steps.

"log in, then like the first 3 posts" → Open URL, Type email, Type password, Click Log in,
Repeat 3 times … End loop. Claude (Anthropic API) returns JSON constrained by a schema; the
steps are then validated against FlowPilot's own step models, so nothing invalid reaches the
editor. When the test browser is open, a short outline of the page's buttons, links and
fields (with ready-made selectors) is sent too, so the draft can use real elements.

Without an API key, a small rule-based parser drafts the obvious parts offline.
"""

from __future__ import annotations

import json
import os
import re
import types
import typing
from dataclasses import dataclass, field
from typing import Any, Literal, get_args, get_origin

from loguru import logger
from pydantic import ValidationError

from flowpilot.core.selectors import candidates_from_descriptor, describe_element
from flowpilot.core.steps import COMMON_FIELDS, HIDDEN_FIELDS, StepBase, StepRegistry, new_step, parse_step

DEFAULT_MODEL = "claude-opus-5-5"
SKIP_TYPES = {"run_js"}  # never drafted automatically (advanced)
ALLOWED_COMMON = ("selector", "fallbacks", "label", "note", "on_error", "on_error_label")


class AiBuilderError(RuntimeError):
    """Plain-language error shown to the user."""


@dataclass
class Draft:
    steps: list[StepBase]
    warnings: list[str] = field(default_factory=list)
    summary: str = ""
    source: str = "ai"  # "ai" or "offline"


# --------------------------------------------------------------------------- step catalog

def _type_name(ann: Any) -> str:
    origin = get_origin(ann)
    if origin in (typing.Union, types.UnionType):
        args = [a for a in get_args(ann) if a is not type(None)]
        return _type_name(args[0]) + " (optional)" if args else "any"
    if origin is Literal:
        return "one of: " + ", ".join(str(a) for a in get_args(ann))
    if origin is list:
        return "list (one item per line)"
    return getattr(ann, "__name__", str(ann))


def step_catalog() -> str:
    """Compact description of every step type and its fields, for the model."""
    lines: list[str] = []
    for type_name, cls in sorted(StepRegistry.all().items()):
        if type_name in SKIP_TYPES:
            continue
        desc = cls.DESCRIPTION or cls.TITLE
        lines.append(f"- {type_name}: {cls.TITLE}. {desc}" + (f" [block: {cls.BLOCK}]" if cls.BLOCK else ""))
        for name, info in cls.model_fields.items():
            if name in HIDDEN_FIELDS or (name in COMMON_FIELDS and name not in ALLOWED_COMMON):
                continue
            if name in ("selector", "fallbacks") and not cls.USES_SELECTOR:
                continue
            if name in ("label", "note", "on_error", "on_error_label"):
                continue  # listed once below
            hint = f" - {info.description}" if info.description else ""
            lines.append(f"    {name}: {_type_name(info.annotation)}{hint}")
    lines.append("Every step may also have: label (name for 'goto'), note (comment for the user), "
                 "on_error (one of: stop, skip, goto, fail_close), on_error_label.")
    return "\n".join(lines)


SYSTEM_PROMPT = """You turn a user's plain-English description of a browser task into steps for FlowPilot, \
a no-code automation app that runs the steps on many anti-detect browser profiles ("accounts").

Rules:
- Use only the step types listed in the catalog, with their exact field names. Give every field value as a string \
(numbers like "3", booleans "true"/"false", list items separated by newlines).
- Selectors use Playwright syntax, best first: [data-testid="x"], role=button[name="Log in"], text="Sign in", \
internal:label="Email"i, [placeholder="Search"], #id, short CSS. When a page outline is given, copy selectors from \
it exactly. When you do not know a selector, leave "selector" empty and say in "note" what the user should pick \
(the user fills it with the Pick element tool).
- Use account variables for anything that differs per account: {{account.email}}, {{account.password}}, \
{{account.username}} or other names from the provided list. Mark password typing with sensitive=true.
- Blocks need their end markers: if_element/if_text/if_variable … (optional else) … end_if; repeat/loop_list … \
end_loop; try … catch … end_try. "the first N items" → repeat with times=N and use {{loop.index}} in selectors \
like ":nth-match(button:has-text(\\"Like\\"), {{loop.index}})" when possible.
- Add short human-like waits (wait_random 1-3 s) after actions that load content. Do not add close_browser unless \
the user asks to close/finish, or the request is a complete job.
- Keep it minimal and in order. Put anything you are unsure about into "warnings" in plain English."""

OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "summary": {"type": "string", "description": "One sentence describing what the steps do."},
        "steps": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "type": {"type": "string"},
                    "params": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {"name": {"type": "string"}, "value": {"type": "string"}},
                            "required": ["name", "value"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["type", "params"],
                "additionalProperties": False,
            },
        },
        "warnings": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["summary", "steps", "warnings"],
    "additionalProperties": False,
}


# --------------------------------------------------------------------------- page outline

OUTLINE_JS = """(limit) => {
  const sel = 'a,button,input,select,textarea,[role=button],[role=link],[role=tab],[role=menuitem],[role=checkbox],h1,h2,h3';
  const out = [];
  for (const el of document.querySelectorAll(sel)) {
    const r = el.getBoundingClientRect();
    if (r.width === 0 || r.height === 0) continue;
    const st = getComputedStyle(el);
    if (st.visibility === 'hidden' || st.display === 'none') continue;
    out.push(window.__fpDescribe(el));
    if (out.length >= limit) break;
  }
  return {url: location.href, title: document.title, items: out};
}"""


async def page_outline(page: Any, limit: int = 120) -> str:
    """Short text outline of the visible interactive elements with their best selector."""
    from flowpilot.core.recorder import RECORDER_JS

    await page.evaluate("() => { if (window.__fpPaused === undefined) window.__fpPaused = true; }")
    await page.evaluate(RECORDER_JS)
    data = await page.evaluate(OUTLINE_JS, limit)
    lines = [f"Page: {data.get('title', '')} ({data.get('url', '')})"]
    for desc in data.get("items", []):
        cands = candidates_from_descriptor(desc, max_candidates=2)
        if not cands:
            continue
        name = describe_element(desc) or desc.get("tag", "")
        lines.append(f"- {name} -> {cands[0]}")
    return "\n".join(lines)


# --------------------------------------------------------------------------- conversion

def _coerce(cls: type[StepBase], name: str, value: str) -> Any:
    info = cls.model_fields[name]
    ann = info.annotation
    origin = get_origin(ann)
    if origin in (typing.Union, types.UnionType):
        args = [a for a in get_args(ann) if a is not type(None)]
        if value.strip() == "":
            return None
        ann = args[0] if args else ann
        origin = get_origin(ann)
    if origin is list:
        return [ln.strip() for ln in value.splitlines() if ln.strip()]
    if ann is bool:
        return value.strip().lower() in ("true", "yes", "1", "on")
    return value  # pydantic converts numbers and checks literal values


def to_steps(data: dict[str, Any]) -> tuple[list[StepBase], list[str]]:
    """Validate the model's JSON into step objects; problems become warnings."""
    steps: list[StepBase] = []
    warnings: list[str] = [str(w) for w in data.get("warnings", []) if str(w).strip()]
    for i, item in enumerate(data.get("steps", []), start=1):
        type_name = str(item.get("type", "")).strip()
        cls = StepRegistry.get(type_name)
        if cls is None or type_name in SKIP_TYPES:
            warnings.append(f"Skipped step {i}: unknown step type “{type_name}”.")
            continue
        values: dict[str, Any] = {}
        for p in item.get("params", []):
            name = str(p.get("name", "")).strip()
            if name not in cls.model_fields or name in HIDDEN_FIELDS or name in ("delay_before", "delay_after"):
                if name:
                    warnings.append(f"Step {i} ({cls.TITLE}): ignored unknown setting “{name}”.")
                continue
            values[name] = _coerce(cls, name, str(p.get("value", "")))
        try:
            steps.append(parse_step({"type": type_name, **values}))
        except ValidationError as exc:
            first = exc.errors()[0]
            field_name = ".".join(str(x) for x in first.get("loc", ()))
            warnings.append(f"Step {i} ({cls.TITLE}): “{field_name}” {first.get('msg', 'is invalid')} — left at default.")
            safe = {k: v for k, v in values.items() if k != field_name}
            try:
                steps.append(parse_step({"type": type_name, **safe}))
            except ValidationError:
                warnings.append(f"Step {i} ({cls.TITLE}) could not be created and was skipped.")
    return steps, warnings


# --------------------------------------------------------------------------- Claude call

def resolve_api_key(stored: str) -> str:
    return stored.strip() or os.environ.get("ANTHROPIC_API_KEY", "").strip()


async def draft_with_claude(request: str, api_key: str, model: str = DEFAULT_MODEL, outline: str = "",
                            account_variables: list[str] | None = None, existing: list[str] | None = None,
                            client: Any = None) -> Draft:
    """Ask Claude for draft steps. ``client`` can be injected (tests)."""
    import anthropic

    if client is None:
        if not api_key:
            raise AiBuilderError("Add your Anthropic API key in Settings → AI to use the AI step builder.")
        client = anthropic.AsyncAnthropic(api_key=api_key, timeout=180.0)
    parts = [f"Step catalog:\n{step_catalog()}"]
    if account_variables:
        parts.append("Account variables available: " + ", ".join("{{" + v + "}}" for v in account_variables))
    if existing:
        parts.append("The flow already has these steps (continue after them):\n" + "\n".join(existing[-30:]))
    if outline:
        parts.append(f"Elements on the page that is open now:\n{outline}")
    parts.append(f"User request:\n{request.strip()}")
    try:
        response = await client.beta.messages.create(
            model=model,
            max_tokens=16000,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": "\n\n".join(parts)}],
            output_config={"effort": "medium", "format": {"type": "json_schema", "schema": OUTPUT_SCHEMA}},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
    except anthropic.AuthenticationError as exc:
        raise AiBuilderError("The Anthropic API key was not accepted. Check it in Settings → AI.") from exc
    except anthropic.PermissionDeniedError as exc:
        raise AiBuilderError("This API key may not use that model. Choose another model in Settings → AI.") from exc
    except anthropic.RateLimitError as exc:
        raise AiBuilderError("The AI service is busy (rate limit). Try again in a minute.") from exc
    except anthropic.APIConnectionError as exc:
        raise AiBuilderError("Could not reach the AI service. Check your internet connection.") from exc
    except anthropic.APIStatusError as exc:
        raise AiBuilderError(f"The AI service returned an error ({exc.status_code}). Try again.") from exc
    if response.stop_reason == "refusal":
        raise AiBuilderError("The AI declined this request. Describe the browser steps more neutrally.")
    if response.stop_reason == "max_tokens":
        raise AiBuilderError("The request is too long for one draft. Split it into smaller parts.")
    text = next((b.text for b in response.content if getattr(b, "type", "") == "text"), "")
    try:
        data = json.loads(text)
    except ValueError as exc:
        logger.warning("AI builder returned non-JSON: {}", text[:300])
        raise AiBuilderError("The AI answer could not be read. Please try again.") from exc
    steps, warnings = to_steps(data)
    if not steps:
        raise AiBuilderError("The AI could not turn this into steps. Try describing it step by step.")
    return Draft(steps, warnings, str(data.get("summary", "")), "ai")


# --------------------------------------------------------------------------- offline fallback

_URL = re.compile(r"(https?://\S+|\b[\w-]+(?:\.[\w-]+)*\.[a-z]{2,}\b(?:/\S*)?)", re.I)
_QUOTED = re.compile(r"[\"“']([^\"”']+)[\"”']")


def draft_offline(request: str) -> Draft:
    """Rule-based drafting for common phrases (no network, no key)."""
    steps: list[StepBase] = []
    warnings: list[str] = []
    parts = [p.strip() for p in re.split(r",|;|\band then\b|\bthen\b|\.\s|\n", request, flags=re.I)
             if p.strip() and p.strip().lower() not in ("and", "then")]
    for part in parts:
        low = part.lower()
        quoted = _QUOTED.findall(part)
        url = _URL.search(part)
        if url and any(w in low for w in ("open", "go to", "visit", "navigate", "load")) or (url and len(parts) == 1):
            steps.append(new_step("open_url", url=url.group(1).rstrip(".,")))
        elif re.search(r"\blog ?in\b|\bsign ?in\b", low):
            steps += [
                new_step("type_text", text="{{account.email}}", note="Pick the email / username field."),
                new_step("type_text", text="{{account.password}}", sensitive=True, note="Pick the password field."),
                new_step("click", note="Pick the Log in button."),
                new_step("wait_for_load"),
            ]
        elif m := re.search(r"(?:first|top)\s+(\d+)\s+(\w+)", low):
            n, what = int(m.group(1)), m.group(2)
            verb = re.match(r"\s*(\w+)", low)
            steps += [new_step("repeat", times=n),
                      new_step("click", note=f"Pick the element to {verb.group(1) if verb else 'use'} on each {what}."),
                      new_step("wait_random", min_seconds=1, max_seconds=3),
                      new_step("end_loop")]
            warnings.append(f"Pick the element for “{part}” and make its selector match each item "
                            "(use {{loop.index}}).")
        elif m := re.search(r"wait\s+(\d+(?:\.\d+)?)\s*(?:-|to)\s*(\d+(?:\.\d+)?)", low):
            steps.append(new_step("wait_random", min_seconds=float(m.group(1)), max_seconds=float(m.group(2))))
        elif m := re.search(r"wait\s+(\d+(?:\.\d+)?)\s*(s|sec|second|m|min|minute)?", low):
            secs = float(m.group(1)) * (60 if (m.group(2) or "").startswith("m") else 1)
            steps.append(new_step("wait", seconds=secs))
        elif low.startswith(("type", "enter", "write", "search for", "search")):
            text = quoted[0] if quoted else re.sub(r"^(type|enter|write|search for|search)\s+", "", part, flags=re.I)
            text = re.split(r"\s+(?:in|into)\s+", text)[0].strip()
            steps.append(new_step("type_text", text=text, press_enter=low.startswith("search"),
                                  note="Pick the field to type into."))
        elif "scroll" in low:
            mode = "to_bottom" if "bottom" in low or "end" in low else "to_top" if "top" in low else "by_amount"
            steps.append(new_step("scroll", mode=mode, amount=-700 if " up" in f" {low}" else 700))
        elif "screenshot" in low:
            steps.append(new_step("screenshot"))
        elif "close" in low and ("browser" in low or "profile" in low):
            steps.append(new_step("close_browser"))
        elif low.startswith(("click", "press", "tap", "like", "follow", "open", "select", "choose")):
            target = quoted[0] if quoted else re.sub(r"^(click|press|tap)\s+(on\s+)?(the\s+)?", "", part, flags=re.I)
            selector = f'text="{target.strip()}"' if quoted else ""
            steps.append(new_step("click", selector=selector, note="" if selector else f"Pick: {target.strip()}"))
        else:
            warnings.append(f"Not understood offline: “{part}”. Add an Anthropic API key in Settings → AI for "
                            "full AI drafting.")
    return Draft(steps, warnings, "Drafted offline with simple rules.", "offline")
