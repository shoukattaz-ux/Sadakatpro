"""Human-like mouse, keyboard and scroll behaviour.

The maths (Bezier paths, timing) are pure functions so they can be unit tested; the
:class:`Humanizer` applies them to a Playwright page. Every wait goes through the
``sleep`` callable so pause/stop requests are honoured even in the middle of typing.
"""

from __future__ import annotations

import asyncio
import math
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from flowpilot.config import HumanizeSettings

Point = tuple[float, float]
SleepFn = Callable[[float], Awaitable[None]]

# Neighbouring keys on a QWERTY keyboard, used for the optional typo simulation.
_NEIGHBOURS = {
    "a": "qwsz", "b": "vghn", "c": "xdfv", "d": "serfcx", "e": "wsdr", "f": "drtgvc", "g": "ftyhbv",
    "h": "gyujnb", "i": "ujko", "j": "huikmn", "k": "jiolm", "l": "kop", "m": "njk", "n": "bhjm",
    "o": "iklp", "p": "ol", "q": "wa", "r": "edft", "s": "awedxz", "t": "rfgy", "u": "yhji",
    "v": "cfgb", "w": "qase", "x": "zsdc", "y": "tghu", "z": "asx",
}


# --------------------------------------------------------------------------- pure helpers

def ease_in_out(t: float) -> float:
    """Smooth-step easing: slow start, fast middle, slow end."""
    return t * t * (3 - 2 * t)


def cubic_bezier(p0: Point, p1: Point, p2: Point, p3: Point, t: float) -> Point:
    u = 1 - t
    x = u**3 * p0[0] + 3 * u**2 * t * p1[0] + 3 * u * t**2 * p2[0] + t**3 * p3[0]
    y = u**3 * p0[1] + 3 * u**2 * t * p1[1] + 3 * u * t**2 * p2[1] + t**3 * p3[1]
    return x, y


def bezier_path(start: Point, end: Point, rng: random.Random, steps: int | None = None) -> list[Point]:
    """Points along a curved path from ``start`` to ``end`` with random control points
    and ease-in/ease-out spacing (dense near the ends, sparse in the middle)."""
    dx, dy = end[0] - start[0], end[1] - start[1]
    dist = math.hypot(dx, dy)
    if steps is None:
        steps = max(8, min(60, int(dist / 12)))
    if dist < 1:
        return [end]
    # Control points sit on either side of the straight line, at 20-80% of the way.
    normal = (-dy / dist, dx / dist)
    spread = min(200.0, dist * rng.uniform(0.1, 0.35))
    c1t, c2t = sorted((rng.uniform(0.15, 0.45), rng.uniform(0.55, 0.85)))
    side = rng.choice((-1, 1))
    c1 = (start[0] + dx * c1t + normal[0] * spread * side * rng.uniform(0.4, 1.0),
          start[1] + dy * c1t + normal[1] * spread * side * rng.uniform(0.4, 1.0))
    c2 = (start[0] + dx * c2t + normal[0] * spread * side * rng.uniform(-0.3, 0.7),
          start[1] + dy * c2t + normal[1] * spread * side * rng.uniform(-0.3, 0.7))
    return [cubic_bezier(start, c1, c2, end, ease_in_out(i / steps)) for i in range(1, steps + 1)]


def overshoot_point(start: Point, end: Point, rng: random.Random) -> Point:
    """A point slightly past ``end`` in the direction of travel."""
    dx, dy = end[0] - start[0], end[1] - start[1]
    dist = math.hypot(dx, dy) or 1.0
    amount = rng.uniform(4, min(25, 4 + dist * 0.05))
    return end[0] + dx / dist * amount + rng.uniform(-3, 3), end[1] + dy / dist * amount + rng.uniform(-3, 3)


def random_point_in_box(box: dict[str, float], rng: random.Random) -> Point:
    """A random point inside an element's bounding box, biased towards the centre but never
    the exact centre and never on the outer 15% border."""
    w, h = box["width"], box["height"]
    cx, cy = box["x"] + w / 2, box["y"] + h / 2
    x = cx + max(-0.35, min(0.35, rng.gauss(0, 0.17))) * w
    y = cy + max(-0.35, min(0.35, rng.gauss(0, 0.17))) * h
    return x, y


@dataclass
class KeyAction:
    """One keyboard action produced by :func:`plan_typing`."""

    key: str  # a character to type, or "Backspace"
    delay: float  # seconds to wait *before* this key


def plan_typing(text: str, s: HumanizeSettings, rng: random.Random) -> list[KeyAction]:
    """Plan per-character delays, pauses between words and optional typos (+ correction)."""
    actions: list[KeyAction] = []
    lo, hi = sorted((s.typing_min_ms, s.typing_max_ms))
    for i, ch in enumerate(text):
        delay = rng.uniform(lo, hi) / 1000
        if i > 0 and text[i - 1] == " " and rng.random() < s.word_pause_chance:
            delay += rng.uniform(s.word_pause_min_ms, s.word_pause_max_ms) / 1000
        lower = ch.lower()
        if s.typo_chance > 0 and lower in _NEIGHBOURS and rng.random() < s.typo_chance:
            wrong = rng.choice(_NEIGHBOURS[lower])
            actions.append(KeyAction(wrong.upper() if ch.isupper() else wrong, delay))
            actions.append(KeyAction("Backspace", rng.uniform(0.15, 0.4)))
            delay = rng.uniform(lo, hi) / 1000
        actions.append(KeyAction(ch, delay))
    return actions


def plan_scroll(total: float, s: HumanizeSettings, rng: random.Random) -> list[float]:
    """Split a scroll distance into wheel-like steps, sometimes scrolling back a little."""
    steps: list[float] = []
    direction = 1 if total >= 0 else -1
    remaining = abs(total)
    lo, hi = sorted((max(10, s.scroll_step_min), max(10, s.scroll_step_max)))
    while remaining > 0:
        step = min(remaining, rng.uniform(lo, hi))
        steps.append(direction * step)
        remaining -= step
        if remaining > 0 and rng.random() < s.scroll_back_chance:
            back = rng.uniform(20, 80)
            steps.append(-direction * back)
            remaining += back
    return steps


# --------------------------------------------------------------------------- page actions

class Humanizer:
    """Applies human-like behaviour to a Playwright page."""

    def __init__(self, settings: HumanizeSettings, sleep: SleepFn | None = None, rng: random.Random | None = None,
                 slow_factor: float = 1.0) -> None:
        self.s = settings
        self.rng = rng or random.Random()
        self._sleep = sleep or asyncio.sleep
        self.slow_factor = slow_factor
        self._positions: dict[int, Point] = {}

    async def sleep(self, seconds: float) -> None:
        if seconds > 0:
            await self._sleep(seconds * self.slow_factor)

    def _pos(self, page: Any) -> Point:
        if id(page) not in self._positions:
            vp = page.viewport_size or {"width": 1280, "height": 800}
            self._positions[id(page)] = (self.rng.uniform(0, vp["width"]), self.rng.uniform(0, vp["height"]))
        return self._positions[id(page)]

    async def move_to(self, page: Any, x: float, y: float) -> None:
        """Move the mouse along a curve, optionally overshooting and correcting."""
        start = self._pos(page)
        if not self.s.mouse_enabled:
            await page.mouse.move(x, y)
            self._positions[id(page)] = (x, y)
            return
        speed = max(0.1, self.s.mouse_speed)
        targets: list[Point] = []
        dist = math.hypot(x - start[0], y - start[1])
        if dist > 80 and self.rng.random() < self.s.overshoot_chance:
            over = overshoot_point(start, (x, y), self.rng)
            targets += bezier_path(start, over, self.rng)
            targets += bezier_path(over, (x, y), self.rng, steps=max(4, int(8 / speed)))
        else:
            targets += bezier_path(start, (x, y), self.rng)
        per_step = self.rng.uniform(0.006, 0.014) / speed
        for px, py in targets:
            await page.mouse.move(px, py)
            await self.sleep(per_step)
        self._positions[id(page)] = (x, y)

    async def scroll_into_view(self, page: Any, locator: Any) -> None:
        """Wheel-scroll until the element is inside the viewport (falls back to Playwright)."""
        try:
            box = await locator.bounding_box()
            vp = page.viewport_size or {"width": 1280, "height": 800}
            if box is None:
                await locator.scroll_into_view_if_needed()
                return
            for _ in range(30):
                top, bottom = box["y"], box["y"] + box["height"]
                if top >= 0 and bottom <= vp["height"]:
                    return
                target = top - vp["height"] * self.rng.uniform(0.3, 0.45)
                for delta in plan_scroll(target, self.s.model_copy(update={"scroll_back_chance": 0}), self.rng)[:4]:
                    await page.mouse.wheel(0, delta)
                    await self.sleep(self.rng.uniform(0.03, 0.12))
                await self.sleep(0.1)
                box = await locator.bounding_box()
                if box is None:
                    break
        except Exception:
            pass
        await locator.scroll_into_view_if_needed()

    async def click(self, page: Any, locator: Any, button: str = "left", click_count: int = 1,
                    timeout_ms: float = 15000) -> None:
        """Scroll into view, move along a curve to a random point inside the element, pause, click."""
        await self.scroll_into_view(page, locator)
        box = await locator.bounding_box()
        if box is None or box["width"] == 0:
            await locator.click(button=button, click_count=click_count, timeout=timeout_ms)
            return
        x, y = random_point_in_box(box, self.rng)
        await self.move_to(page, x, y)
        await self.sleep(self.rng.uniform(0.05, 0.25))
        await page.mouse.click(x, y, button=button, click_count=click_count, delay=self.rng.randint(40, 120))

    async def hover(self, page: Any, locator: Any) -> None:
        await self.scroll_into_view(page, locator)
        box = await locator.bounding_box()
        if box is None:
            await locator.hover()
            return
        await self.move_to(page, *random_point_in_box(box, self.rng))

    async def type_text(self, page: Any, text: str) -> None:
        """Type into the focused element with human rhythm."""
        for action in plan_typing(text, self.s, self.rng):
            await self.sleep(action.delay)
            if action.key == "Backspace":
                await page.keyboard.press("Backspace")
            else:
                await page.keyboard.type(action.key)

    async def scroll_by(self, page: Any, amount: float) -> None:
        for delta in plan_scroll(amount, self.s, self.rng):
            await page.mouse.wheel(0, delta)
            await self.sleep(self.rng.uniform(0.05, 0.25))

    async def idle(self, page: Any, seconds: float, sleep: SleepFn) -> None:
        """Wait ``seconds``; with idle jitter on, wiggle the mouse a little meanwhile."""
        if not self.s.idle_jitter or seconds < 1.5:
            await sleep(seconds)
            return
        loop = asyncio.get_running_loop()
        end = loop.time() + seconds
        while (left := end - loop.time()) > 0:
            chunk = min(left, self.rng.uniform(0.8, 2.0))
            await sleep(chunk)
            if end - loop.time() > 0.3:
                x, y = self._pos(page)
                try:
                    await self.move_to(page, x + self.rng.uniform(-15, 15), y + self.rng.uniform(-10, 10))
                except Exception:
                    pass

    def step_delay(self) -> float:
        lo, hi = sorted((self.s.step_delay_min, self.s.step_delay_max))
        return self.rng.uniform(lo, hi)
