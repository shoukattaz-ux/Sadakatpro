"""Step model base class, shared option types and the :class:`StepRegistry`.

Every step type is a pydantic model with a literal ``type`` field. Steps are stored as
JSON (``model_dump``) and parsed back with :func:`parse_step`, which dispatches on
``type`` — a discriminated union whose members are registered at import time, so new step
types only need the ``@register`` decorator.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

if TYPE_CHECKING:
    from flowpilot.core.context import StepContext

OnError = Literal["stop", "skip", "goto", "fail_close"]
HumanizeMode = Literal["default", "on", "off"]


def new_step_id() -> str:
    return uuid.uuid4().hex[:10]


class Delay(BaseModel):
    """A random delay between ``min`` and ``max`` seconds."""

    model_config = ConfigDict(extra="ignore")
    min: float = Field(0.0, ge=0, title="Min seconds")
    max: float = Field(0.0, ge=0, title="Max seconds")

    @property
    def is_zero(self) -> bool:
        return self.min <= 0 and self.max <= 0


#: Fields every step has; the editor shows them in the "Options" section.
COMMON_FIELDS = (
    "selector", "fallbacks", "timeout", "retries", "retry_delay", "on_error", "on_error_label",
    "delay_before", "delay_after", "humanize", "label", "note",
)
#: Internal fields never shown in the form.
HIDDEN_FIELDS = ("id", "type", "enabled", "collapsed", "element_desc", "element_fp")


class StepBase(BaseModel):
    """Common fields of all steps."""

    model_config = ConfigDict(extra="ignore", validate_assignment=False)

    id: str = Field(default_factory=new_step_id)
    type: str
    enabled: bool = True
    collapsed: bool = False
    label: str = Field("", title="Label", description="A name for this step. 'Go to label' steps can jump here.")
    note: str = Field("", title="Note", description="Your own comment about this step.")
    #: plain-English name of the element, filled by the recorder / Pick element ("the “Log in” button")
    element_desc: str = ""
    #: fingerprint of the recorded element, used by self-healing (see core/healing.py)
    element_fp: dict[str, Any] = Field(default_factory=dict)
    selector: str = Field("", title="Element", description="Which element to use. Click 'Pick element' to choose it on the page.")
    fallbacks: list[str] = Field(default_factory=list, title="Backup selectors",
                                 description="Tried in order if the main selector finds nothing (one per line).")
    timeout: float | None = Field(None, title="Timeout (seconds)",
                                  description="How long to wait for the element. Empty = default from Settings.")
    retries: int | None = Field(None, title="Retries", description="How many times to try again if the step fails. Empty = default.")
    retry_delay: float | None = Field(None, title="Seconds between retries", description="Empty = default.")
    on_error: OnError = Field("stop", title="If it still fails",
                              description="stop = end this account as failed; skip = continue with the next step; "
                                          "goto = jump to a label; fail_close = mark failed and close the browser.")
    on_error_label: str = Field("", title="Go to label (on error)", description="Used when 'If it still fails' is goto.")
    delay_before: Delay = Field(default_factory=Delay, title="Wait before (random)")
    delay_after: Delay = Field(default_factory=Delay, title="Wait after (random)")
    humanize: HumanizeMode = Field("default", title="Human-like behaviour",
                                   description="default = use Settings; on/off = override for this step.")

    # -- class metadata (override in subclasses) ---------------------------------------
    TITLE: ClassVar[str] = "Step"
    CATEGORY: ClassVar[str] = "Other"
    ICON: ClassVar[str] = "circle"
    DESCRIPTION: ClassVar[str] = ""
    USES_SELECTOR: ClassVar[bool] = False
    SELECTOR_REQUIRED: ClassVar[bool] = True
    ADVANCED: ClassVar[bool] = False
    #: Block role for logic steps: if / else / end_if / loop / end_loop / try / catch / end_try
    BLOCK: ClassVar[str | None] = None

    # -- behaviour -------------------------------------------------------------------------
    def summary(self) -> str:
        """Plain-English one-liner shown on the step card."""
        return self.TITLE

    def problems(self) -> list[str]:
        """Configuration problems (shown in red in the editor; block running)."""
        issues: list[str] = []
        if self.USES_SELECTOR and self.SELECTOR_REQUIRED and not self.selector.strip():
            issues.append("Choose an element (use 'Pick element').")
        if self.on_error == "goto" and not self.on_error_label.strip():
            issues.append("'If it still fails' is set to go to a label, but no label is given.")
        return issues

    async def run(self, ctx: StepContext) -> Any:
        """Execute the step. Logic steps are handled by the runner instead."""
        return None

    def target(self) -> str:
        """Short description of the element for summaries."""
        from flowpilot.core.selectors import describe_selector

        if self.element_desc and self.selector:
            return self.element_desc
        return describe_selector(self.selector) if self.selector else "an element"


class StepRegistry:
    """Maps ``type`` strings to step classes."""

    _types: dict[str, type[StepBase]] = {}

    @classmethod
    def register(cls, step_cls: type[StepBase]) -> type[StepBase]:
        type_name = step_cls.model_fields["type"].default
        if not isinstance(type_name, str) or not type_name:
            raise TypeError(f"{step_cls.__name__} needs a literal default for 'type'")
        cls._types[type_name] = step_cls
        return step_cls

    @classmethod
    def get(cls, type_name: str) -> type[StepBase] | None:
        return cls._types.get(type_name)

    @classmethod
    def all(cls) -> dict[str, type[StepBase]]:
        return dict(cls._types)

    @classmethod
    def by_category(cls) -> dict[str, list[type[StepBase]]]:
        out: dict[str, list[type[StepBase]]] = {}
        for step_cls in cls._types.values():
            out.setdefault(step_cls.CATEGORY, []).append(step_cls)
        return out


register = StepRegistry.register


class UnknownStepType(ValueError):
    pass


def parse_step(data: dict[str, Any]) -> StepBase:
    """Parse one step dict (discriminated by ``type``)."""
    type_name = data.get("type")
    step_cls = StepRegistry.get(str(type_name))
    if step_cls is None:
        raise UnknownStepType(f"Unknown step type '{type_name}'")
    return step_cls.model_validate(data)


def parse_steps(items: list[dict[str, Any]]) -> list[StepBase]:
    steps: list[StepBase] = []
    for i, item in enumerate(items, start=1):
        try:
            steps.append(parse_step(item))
        except (ValidationError, UnknownStepType) as exc:
            raise ValueError(f"Step {i} could not be read: {exc}") from exc
    return steps


def dump_steps(steps: list[StepBase]) -> list[dict[str, Any]]:
    return [s.model_dump(mode="json") for s in steps]


def new_step(type_name: str, **values: Any) -> StepBase:
    step_cls = StepRegistry.get(type_name)
    if step_cls is None:
        raise UnknownStepType(type_name)
    return step_cls(**values)
