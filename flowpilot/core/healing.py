"""Self-healing selectors.

When a step was recorded (or its element picked), FlowPilot keeps a *fingerprint* of the
element: tag, role, accessible name, text, label, stable attributes and position. If every
selector of the step later fails, the page is scanned for the element that best matches the
fingerprint. A confident, unambiguous match is used for this run and reported as a
*heal suggestion* the user can accept in the editor (which saves the new selector and keeps
the old one as a backup).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from flowpilot.core.selectors import candidates_from_descriptor, describe_element

#: Minimum score of the best match (tag 2 + role 2 + position 2 = 6 is never enough on its own).
MIN_SCORE = 7.0
#: The best match must beat the runner-up by this much, otherwise it is ambiguous.
MIN_MARGIN = 1.5

FP_ATTRS = ("data-testid", "data-test-id", "data-test", "data-qa", "data-cy", "data-automation-id", "name", "type",
            "placeholder", "aria-label", "title", "alt", "href")


def fingerprint(desc: dict[str, Any]) -> dict[str, Any]:
    """Compact, JSON-safe fingerprint of a recorded element descriptor."""
    if not desc:
        return {}
    attrs = {k: str(v)[:200] for k, v in (desc.get("attrs") or {}).items() if k in FP_ATTRS and v}
    fp: dict[str, Any] = {
        "tag": str(desc.get("tag") or "").lower(),
        "role": str(desc.get("role") or ""),
        "name": str(desc.get("name") or "")[:120],
        "text": str(desc.get("text") or "")[:120],
        "label": str(desc.get("label") or "")[:120],
        "id": str(desc.get("id") or ""),
        "attrs": attrs,
    }
    rect = desc.get("rect")
    if isinstance(rect, dict) and {"x", "y"} <= set(rect):
        fp["rect"] = {k: int(rect.get(k, 0)) for k in ("x", "y", "w", "h")}
    return {k: v for k, v in fp.items() if v}


@dataclass
class HealResult:
    selector: str
    fallbacks: list[str]
    score: float
    element_desc: str = ""
    descriptor: dict[str, Any] = field(default_factory=dict)


def is_confident(score: float, second: float) -> bool:
    return score >= MIN_SCORE and score - second >= MIN_MARGIN


async def heal_locator(page: Any, fp: dict[str, Any]) -> tuple[Any, HealResult] | None:
    """Find the element matching ``fp`` on ``page``. Returns ``(locator, result)`` or None."""
    if not fp or not fp.get("tag"):
        return None
    from flowpilot.core.recorder import RECORDER_JS

    try:
        await page.evaluate("() => { if (window.__fpPaused === undefined) window.__fpPaused = true; }")
        await page.evaluate(RECORDER_JS)
        found = await page.evaluate("(fp) => window.__fpHeal ? window.__fpHeal(fp) : null", fp)
    except Exception:
        return None
    if not found or not is_confident(float(found.get("score", 0)), float(found.get("second", 0))):
        return None
    desc = found["desc"]
    for candidate in candidates_from_descriptor(desc, max_candidates=4):
        try:
            loc = page.locator(candidate).first
            if await loc.count() > 0:
                cands = candidates_from_descriptor(desc, max_candidates=4)
                rest = [c for c in cands if c != candidate]
                return loc, HealResult(candidate, rest, float(found["score"]), describe_element(desc), desc)
        except Exception:
            continue
    return None


def apply_heal(step: Any, selector: str, fallbacks: list[str], element_fp: dict[str, Any] | None = None) -> Any:
    """Return a copy of ``step`` using the healed selector; the old selectors become backups."""
    backups: list[str] = []
    for s in [*fallbacks, step.selector, *step.fallbacks]:
        if s and s != selector and s not in backups:
            backups.append(s)
    update: dict[str, Any] = {"selector": selector, "fallbacks": backups[:5]}
    if element_fp:
        update["element_fp"] = element_fp
    return step.model_copy(update=update)


def heal_payload(result: HealResult) -> str:
    return json.dumps({"fallbacks": result.fallbacks, "fp": fingerprint(result.descriptor)})
