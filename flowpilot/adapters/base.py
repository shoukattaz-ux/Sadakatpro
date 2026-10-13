"""Common adapter interface, data types and the HTTP helper with retry/backoff."""

from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

import httpx
from loguru import logger


class AdapterError(RuntimeError):
    """A problem talking to an anti-detect browser; ``str(exc)`` is user friendly."""


@dataclass
class ConnectionResult:
    ok: bool
    message: str
    details: str = ""


@dataclass
class RemoteProfile:
    """A profile as reported by the anti-detect browser."""

    id: str
    name: str
    group: str = ""
    tags: list[str] = field(default_factory=list)
    notes: str = ""


@dataclass
class ExtraField:
    """Describes an additional per-connection setting shown in the connection editor."""

    key: str
    label: str
    help: str = ""
    default: str = ""
    secret: bool = False
    choices: list[str] | None = None


@runtime_checkable
class BrowserAdapter(Protocol):
    """What every anti-detect browser integration must provide."""

    async def test_connection(self) -> ConnectionResult: ...

    async def list_profiles(self) -> list[RemoteProfile]: ...

    async def start_profile(self, profile_id: str) -> str:
        """Start a profile and return its CDP endpoint (``ws://`` or ``http://``)."""
        ...

    async def stop_profile(self, profile_id: str) -> None: ...

    async def is_running(self, profile_id: str) -> bool: ...

    async def aclose(self) -> None: ...


def json_path(data: Any, path: str) -> Any:
    """Read a value by a dotted path, e.g. ``data.ws.puppeteer`` or ``data.list.0.id``.

    Returns ``None`` when any part of the path is missing.
    """
    current = data
    for part in [p for p in path.strip().split(".") if p]:
        if isinstance(current, dict):
            current = current.get(part)
        elif isinstance(current, list) and part.lstrip("-").isdigit():
            idx = int(part)
            current = current[idx] if -len(current) <= idx < len(current) else None
        else:
            return None
        if current is None:
            return None
    return current


class HttpClient:
    """Small wrapper around :class:`httpx.AsyncClient` with retries and friendly errors."""

    def __init__(
        self,
        base_url: str,
        headers: dict[str, str] | None = None,
        params: dict[str, str] | None = None,
        timeout: float = 30.0,
        retries: int = 3,
        backoff: float = 1.0,
        verify: bool = True,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.retries = retries
        self.backoff = backoff
        self._default_params = params or {}
        self._client = httpx.AsyncClient(
            headers=headers or {}, timeout=timeout, verify=verify, transport=transport, follow_redirects=True
        )

    def url(self, path: str) -> str:
        if path.startswith(("http://", "https://")):
            return path
        return f"{self.base_url}/{path.lstrip('/')}"

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,
        headers: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> Any:
        """Send a request and return parsed JSON (or text). Retries network errors,
        HTTP 429 and 5xx with exponential backoff + jitter."""
        url = self.url(path)
        merged = {**self._default_params, **(params or {})}
        last_exc: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                kwargs: dict[str, Any] = {"params": merged or None, "headers": headers}
                if json is not None:
                    kwargs["json"] = json
                if timeout is not None:
                    kwargs["timeout"] = timeout
                resp = await self._client.request(method.upper(), url, **kwargs)
                if resp.status_code == 429 or resp.status_code >= 500:
                    raise httpx.HTTPStatusError(f"HTTP {resp.status_code}", request=resp.request, response=resp)
                if resp.status_code in (401, 403):
                    raise AdapterError(
                        f"The browser API refused the request (HTTP {resp.status_code}). Check the API key."
                    )
                if resp.status_code == 404:
                    raise AdapterError(f"The browser API does not have this address (HTTP 404): {url}\n"
                                       "If this is not the browser type you chose, use the type "
                                       "“Any browser (auto-detect)” — it finds the right addresses itself.")
                if resp.status_code >= 400:
                    raise AdapterError(f"The browser API returned an error (HTTP {resp.status_code}): {resp.text[:300]}")
                try:
                    return resp.json()
                except ValueError:
                    return resp.text
            except AdapterError:
                raise
            except (httpx.TransportError, httpx.HTTPStatusError) as exc:
                last_exc = exc
                if attempt >= self.retries:
                    break
                delay = self.backoff * (2**attempt) + random.uniform(0, 0.3)
                logger.debug("HTTP {} {} failed ({}), retry in {:.1f}s", method, url, exc, delay)
                await asyncio.sleep(delay)
        if isinstance(last_exc, httpx.ConnectError):
            raise AdapterError(
                f"Could not reach {self.base_url}. Is the anti-detect browser app running and is the API enabled?"
            ) from last_exc
        if isinstance(last_exc, httpx.TimeoutException):
            raise AdapterError(f"The browser API at {self.base_url} did not answer in time.") from last_exc
        raise AdapterError(f"Request to the browser API failed: {last_exc}") from last_exc

    async def aclose(self) -> None:
        await self._client.aclose()


class BaseHttpAdapter:
    """Shared plumbing for HTTP based adapters."""

    #: Human readable vendor name.
    display_name = "Browser"
    #: Default local API base URL.
    default_base_url = ""
    #: Extra settings shown in the connection editor.
    extra_fields: list[ExtraField] = []
    #: Help text shown under the API key box.
    api_key_help = ""

    def __init__(
        self,
        base_url: str,
        api_key: str = "",
        extra: dict[str, Any] | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = (base_url or self.default_base_url).rstrip("/")
        self.api_key = api_key
        self.extra = {f.key: f.default for f in self.extra_fields} | (extra or {})
        self._transport = transport
        self.http = self._make_client()

    def _make_client(self) -> HttpClient:
        return HttpClient(self.base_url, headers=self._auth_headers(), transport=self._transport)

    def _auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}

    async def aclose(self) -> None:
        await self.http.aclose()

    async def test_connection(self) -> ConnectionResult:
        try:
            profiles = await self.list_profiles()
        except AdapterError as exc:
            return ConnectionResult(False, str(exc))
        return ConnectionResult(True, f"Connected to {self.display_name}. Found {len(profiles)} profile(s).")

    async def list_profiles(self) -> list[RemoteProfile]:  # pragma: no cover - abstract
        raise NotImplementedError

    async def is_running(self, profile_id: str) -> bool:  # pragma: no cover - default
        return False

    @staticmethod
    def _expect(condition: bool, message: str, payload: Any = None) -> None:
        if not condition:
            detail = f" Response: {str(payload)[:300]}" if payload is not None else ""
            raise AdapterError(message + detail)
