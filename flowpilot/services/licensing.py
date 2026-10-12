"""Licensing hook (placeholder).

Replace :func:`check_license` with a real check (online activation, signed key file, ...).
The rest of the app only reads :class:`LicenseInfo`, so nothing else needs to change.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class LicenseInfo:
    valid: bool = True
    plan: str = "Free"
    holder: str = ""
    expires: str = ""
    max_concurrent_browsers: int | None = None  # None = unlimited
    message: str = ""


def check_license(license_key: str = "") -> LicenseInfo:
    """Return the current license. Placeholder: every installation is valid."""
    return LicenseInfo(valid=True, plan="Free", message="No license check is configured.")
