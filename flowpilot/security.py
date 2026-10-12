"""Encryption of secrets (API keys, account passwords) at rest.

* On Windows the data is protected with **DPAPI** (``CryptProtectData``), bound to the
  current Windows user — no key file needed.
* Elsewhere a Fernet key is kept in the OS keyring (``keyring``) and, if no keyring
  backend is available, in a ``secret.key`` file inside the data folder.

Encrypted values are stored as text with a scheme prefix (``dpapi:`` / ``fernet:``) so the
storage can be read back even if the platform changes how it encrypts new values.
"""

from __future__ import annotations

import base64
import ctypes
import os
import sys
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from loguru import logger

_KEYRING_SERVICE = "FlowPilot"
_KEYRING_USER = "master-key"


class SecretError(RuntimeError):
    """Raised when a secret cannot be decrypted."""


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", ctypes.c_uint32), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _dpapi(data: bytes, protect: bool) -> bytes:  # pragma: no cover - Windows only
    crypt32 = ctypes.windll.crypt32  # type: ignore[attr-defined]
    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    buf = ctypes.create_string_buffer(data, len(data))
    blob_in = _DataBlob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    blob_out = _DataBlob()
    fn = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    ok = fn(ctypes.byref(blob_in), None, None, None, None, 0x01, ctypes.byref(blob_out))
    if not ok:
        raise SecretError("Windows could not decrypt this value (was it created by another user?)")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        kernel32.LocalFree(blob_out.pbData)


class SecretBox:
    """Encrypts and decrypts short text secrets."""

    def __init__(self, data_dir: Path, use_dpapi: bool | None = None) -> None:
        self._data_dir = data_dir
        self._use_dpapi = (sys.platform == "win32") if use_dpapi is None else use_dpapi
        self._fernet: Fernet | None = None

    # -- key management -----------------------------------------------------------
    def _get_fernet(self) -> Fernet:
        if self._fernet is None:
            self._fernet = Fernet(self._load_or_create_key())
        return self._fernet

    def _load_or_create_key(self) -> bytes:
        key_file = self._data_dir / "secret.key"
        if key_file.exists():
            return key_file.read_bytes().strip()
        if not os.environ.get("FLOWPILOT_NO_KEYRING"):
            try:
                import keyring

                stored = keyring.get_password(_KEYRING_SERVICE, _KEYRING_USER)
                if stored:
                    return stored.encode()
                key = Fernet.generate_key()
                keyring.set_password(_KEYRING_SERVICE, _KEYRING_USER, key.decode())
                return key
            except Exception as exc:  # no keyring backend (headless Linux, CI ...)
                logger.debug("Keyring unavailable ({}), using key file", exc)
        key = Fernet.generate_key()
        self._data_dir.mkdir(parents=True, exist_ok=True)
        key_file.write_bytes(key)
        try:
            key_file.chmod(0o600)
        except OSError:
            pass
        return key

    # -- public API -----------------------------------------------------------------
    def encrypt(self, plain: str) -> str:
        """Encrypt ``plain``; an empty string stays empty."""
        if not plain:
            return ""
        if self._use_dpapi:
            return "dpapi:" + base64.b64encode(_dpapi(plain.encode("utf-8"), True)).decode()
        return "fernet:" + self._get_fernet().encrypt(plain.encode("utf-8")).decode()

    def decrypt(self, stored: str) -> str:
        """Decrypt a value produced by :meth:`encrypt`."""
        if not stored:
            return ""
        scheme, _, payload = stored.partition(":")
        try:
            if scheme == "dpapi":
                return _dpapi(base64.b64decode(payload), False).decode("utf-8")
            if scheme == "fernet":
                return self._get_fernet().decrypt(payload.encode()).decode("utf-8")
        except (InvalidToken, ValueError) as exc:
            raise SecretError("Stored secret could not be decrypted") from exc
        if scheme == "plain":
            return payload
        raise SecretError(f"Unknown secret format '{scheme}'")


def mask(value: str, visible: int = 3) -> str:
    """Return a masked version of a secret for display (``abc•••••``)."""
    if not value:
        return ""
    return value[:visible] + "•" * max(4, len(value) - visible)
