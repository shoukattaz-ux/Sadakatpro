"""Step types. Importing this package registers every step in the :class:`StepRegistry`.

Steps are grouped by category (one module per category) to keep related code together.
"""

from flowpilot.core.steps import data, interaction, logic, navigation, utilities, waiting  # noqa: F401
from flowpilot.core.steps.base import (
    COMMON_FIELDS,
    HIDDEN_FIELDS,
    Delay,
    StepBase,
    StepRegistry,
    dump_steps,
    new_step,
    parse_step,
    parse_steps,
)

CATEGORY_ORDER = ["Navigation", "Interaction", "Waiting", "Data & variables", "Logic", "Utilities", "Ending / Browser"]

__all__ = [
    "CATEGORY_ORDER",
    "COMMON_FIELDS",
    "HIDDEN_FIELDS",
    "Delay",
    "StepBase",
    "StepRegistry",
    "dump_steps",
    "new_step",
    "parse_step",
    "parse_steps",
]
