"""``{{variable}}`` templating.

Names are dotted: ``{{account.email}}``, ``{{vars.price}}``, ``{{loop.item}}``,
``{{row.city}}``. A bare name such as ``{{price}}`` is looked up in the flow variables.
Unknown variables are left untouched by default so mistakes are visible in logs.
Built-ins: ``{{now}}``, ``{{today}}``, ``{{timestamp}}``, ``{{random_int}}``, ``{{uuid}}``.
"""

from __future__ import annotations

import random
import re
import uuid
from collections.abc import Mapping
from datetime import datetime
from typing import Any

VAR_PATTERN = re.compile(r"\{\{\s*([A-Za-z_][\w.\-]*)\s*\}\}")


class Variables:
    """Holds the variables of one account run, organised in namespaces."""

    def __init__(self, account: Mapping[str, Any] | None = None, flow: Mapping[str, Any] | None = None) -> None:
        self.namespaces: dict[str, dict[str, Any]] = {
            "account": dict(account or {}),
            "vars": dict(flow or {}),
            "loop": {},
            "row": {},
            "error": {},
        }

    # -- reading -----------------------------------------------------------------
    def get(self, name: str, default: Any = None) -> Any:
        name = name.strip()
        builtin = self._builtin(name)
        if builtin is not None:
            return builtin
        ns, _, key = name.partition(".")
        if key and ns in self.namespaces:
            return self.namespaces[ns].get(key, default)
        return self.namespaces["vars"].get(name, default)

    @staticmethod
    def _builtin(name: str) -> Any:
        if name == "now":
            return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if name == "today":
            return datetime.now().strftime("%Y-%m-%d")
        if name == "timestamp":
            return str(int(datetime.now().timestamp()))
        if name == "random_int":
            return str(random.randint(0, 999999))
        if name == "uuid":
            return str(uuid.uuid4())
        return None

    def has(self, name: str) -> bool:
        return self.get(name, _MISSING) is not _MISSING

    # -- writing -----------------------------------------------------------------
    def set(self, name: str, value: Any) -> None:
        """Set ``vars.x`` (default namespace) or a namespaced name like ``loop.item``."""
        name = name.strip()
        if name.startswith("{{") and name.endswith("}}"):
            name = name[2:-2].strip()
        ns, _, key = name.partition(".")
        if key and ns in self.namespaces:
            if ns == "account":
                raise ValueError("Account variables are read-only inside a flow")
            self.namespaces[ns][key] = value
        else:
            self.namespaces["vars"][name] = value

    # -- templating ----------------------------------------------------------------
    def render(self, text: Any, keep_unknown: bool = True) -> Any:
        """Replace every ``{{name}}`` in ``text``. Non-strings are returned unchanged."""
        if not isinstance(text, str) or "{{" not in text:
            return text

        def repl(match: re.Match[str]) -> str:
            value = self.get(match.group(1), _MISSING)
            if value is _MISSING:
                return match.group(0) if keep_unknown else ""
            return str(value)

        return VAR_PATTERN.sub(repl, text)

    def snapshot(self) -> dict[str, Any]:
        """Flow variables (not account secrets) for logs and results."""
        return dict(self.namespaces["vars"])


class _Missing:
    def __repr__(self) -> str:
        return "<missing>"


_MISSING: Any = _Missing()


def find_variables(text: str) -> list[str]:
    """Names of all variables referenced in ``text``."""
    return [m.group(1) for m in VAR_PATTERN.finditer(text or "")]
