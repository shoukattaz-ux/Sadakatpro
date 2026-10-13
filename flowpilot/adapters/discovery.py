"""Automatic discovery of an unknown anti-detect browser's local API.

Given only a base URL and an API key, find out:

* how the key is sent (``Authorization: Bearer``, ``X-API-Key``, a query parameter, …),
* which endpoint lists profiles and how the list is shaped (path, id / name fields),
* which endpoints start and stop a profile (and with which method / parameter),

so the :class:`~flowpilot.adapters.custom.CustomAdapter` can drive it. Two strategies:

1. **OpenAPI** — many local APIs (FastAPI, NestJS, …) publish ``/openapi.json``; its paths
   and security schemes describe everything exactly.
2. **Probing** — otherwise common URL patterns are tried. Start/stop endpoints are probed
   with a fake profile id, so no real profile is ever opened during detection: a route
   that exists answers differently (e.g. "profile not found") from a route that doesn't.

The result is a dict of CustomAdapter ``extra`` settings plus a human-readable report.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

import httpx

PROBE_ID = "flowpilot-probe-0000"
#: An action word no API has; routes that also answer to it are catch-alls, not real actions.
CONTROL_WORD = "flowpilot-no-such-action"
OPENAPI_PATHS = ("/openapi.json", "/swagger.json", "/api/openapi.json", "/docs/openapi.json", "/v1/openapi.json",
                 "/api-docs", "/swagger/v1/swagger.json", "/api/docs/openapi.json")
# versioned public APIs first: some apps also serve internal routes (e.g. /profiles) for their own window
LIST_PATHS = ("/api/v1/profiles", "/api/v2/profiles", "/api/profiles", "/v1/profiles", "/v2/profiles", "/profiles",
              "/profile/list", "/api/profile/list", "/api/v1/profile/list", "/api/v1/profiles/list",
              "/api/profiles/list", "/profiles/list", "/api/v1/user/list", "/api/v1/browser/list", "/browser/list",
              "/api/browser/list", "/api/browsers", "/browsers", "/api/v1/browsers", "/api/profile", "/profile")
ID_KEYS = ("id", "uuid", "profile_id", "profileId", "user_id", "_id", "browser_id", "browserId", "key")
NAME_KEYS = ("name", "title", "profile_name", "profileName", "serial_number", "remark", "label")
GROUP_KEYS = ("group", "group_name", "groupName", "folder", "folder_name", "category", "tag")
START_WORDS = ("start", "open", "launch", "run")
STOP_WORDS = ("stop", "close", "kill", "quit")


@dataclass(frozen=True)
class Auth:
    location: str  # header / query / none
    name: str = ""
    prefix: str = ""

    def apply(self, key: str, headers: dict[str, str], params: dict[str, str]) -> None:
        if not key or self.location == "none":
            return
        if self.location == "header":
            headers[self.name] = f"{self.prefix}{key}"
        else:
            params[self.name] = key

    def describe(self) -> str:
        if self.location == "none":
            return "no API key"
        if self.location == "header":
            return f"header {self.name}: {self.prefix}<key>"
        return f"query parameter ?{self.name}=<key>"


AUTH_VARIANTS = (
    Auth("header", "Authorization", "Bearer "),
    Auth("header", "X-API-Key"),
    Auth("header", "x-api-token"),
    Auth("header", "api-key"),
    Auth("header", "Authorization"),
    Auth("header", "X-Auth-Token"),
    Auth("header", "token"),
    Auth("query", "api_key"),
    Auth("query", "token"),
    Auth("query", "key"),
    Auth("query", "apikey"),
    Auth("none"),
)


@dataclass
class DiscoveryResult:
    ok: bool
    config: dict[str, str] = field(default_factory=dict)
    report: list[str] = field(default_factory=list)
    profiles_found: int = 0

    def summary(self) -> str:
        return "\n".join(self.report)


# --------------------------------------------------------------------------- helpers

def find_list(data: Any, depth: int = 0) -> tuple[str, list[dict[str, Any]]] | None:
    """Locate a list of objects in a JSON answer; returns (json path, list)."""
    if isinstance(data, list) and (not data or isinstance(data[0], dict)):
        return "", data
    if isinstance(data, dict) and depth < 3:
        # prefer well-known keys, then anything that is a list of objects
        keys = sorted(data, key=lambda k: (k not in ("data", "list", "profiles", "items", "results", "rows",
                                                      "browsers", "records", "content"), k))
        for k in keys:
            sub = find_list(data[k], depth + 1)
            if sub is not None and (sub[1] or isinstance(data[k], list)):
                path = f"{k}.{sub[0]}".strip(".")
                return path, sub[1]
    return None


def pick_key(item: dict[str, Any], candidates: tuple[str, ...]) -> str:
    for k in candidates:
        if k in item and item[k] not in (None, ""):
            return k
    lower = {k.lower(): k for k in item}
    for k in candidates:
        if k.lower() in lower:
            return lower[k.lower()]
    return ""


CDP_KEYS = ("ws", "wsEndpoint", "ws_endpoint", "wsUrl", "ws_url", "webSocketDebuggerUrl", "websocket", "cdp",
            "cdp_url", "cdpUrl", "puppeteer", "debuggerAddress", "debugger_address", "browserWSEndpoint")
PORT_KEYS = ("port", "debug_port", "debugPort", "remote_debugging_port", "remoteDebuggingPort", "cdp_port",
             "cdpPort", "devtools_port")


def find_cdp(data: Any, host: str = "127.0.0.1", depth: int = 0) -> str:
    """Find the CDP address anywhere in a start answer (ws:// URL, host:port, or a port)."""
    if depth > 5:
        return ""
    if isinstance(data, str):
        s = data.strip()
        if s.startswith(("ws://", "wss://")):
            return s
        if re.fullmatch(r"https?://[\w.\-]+:\d+/?", s):
            return s.rstrip("/")
        if re.fullmatch(r"[\w.\-]+:\d{2,5}(/devtools/.*)?", s):
            return f"ws://{s}" if "/devtools/" in s else f"http://{s}"
        return ""
    if isinstance(data, dict):
        for k in CDP_KEYS:  # well-known keys first
            for key in data:
                if key.lower() == k.lower():
                    found = find_cdp(data[key], host, depth + 1)
                    if found:
                        return found
        for k in PORT_KEYS:
            for key, value in data.items():
                if key.lower() == k.lower() and str(value).isdigit() and 0 < int(value) < 65536:
                    return f"http://{host}:{int(value)}"
        for value in data.values():  # anything nested
            found = find_cdp(value, host, depth + 1)
            if found:
                return found
    if isinstance(data, list):
        for value in data:
            found = find_cdp(value, host, depth + 1)
            if found:
                return found
    return ""


def api_failure(data: Any) -> str:
    """A failure message when a 2xx JSON answer still reports an error, else ''."""
    if not isinstance(data, dict):
        return ""
    if data.get("success") is False or data.get("ok") is False or str(data.get("status", "")).lower() in (
            "error", "fail", "failed"):
        return str(data.get("message") or data.get("msg") or data.get("error") or data.get("detail") or "error")
    code = data.get("code")
    if isinstance(code, int) and code not in (0, 200) and ("msg" in data or "message" in data):
        return str(data.get("msg") or data.get("message"))
    if "error" in data and data["error"] and len(data) <= 3:
        return str(data["error"])
    return ""


def _is_missing_route(resp: httpx.Response) -> bool:
    """True if the answer means 'this URL does not exist' (not 'this profile does not exist')."""
    if resp.status_code not in (404, 405, 501):
        return False
    if resp.status_code in (405, 501):
        return True
    text = resp.text.strip().lower()
    if not text or text.startswith("<") or "cannot get" in text or "cannot post" in text or text == "not found":
        return True
    if re.fullmatch(r'\{\s*"(detail|message|error)"\s*:\s*"not found"\s*\}', text):
        return True
    return "route" in text and "not found" in text


# --------------------------------------------------------------------------- discovery

class Discoverer:
    def __init__(self, base_url: str, api_key: str, transport: httpx.AsyncBaseTransport | None = None,
                 timeout: float = 6.0) -> None:
        self.base = base_url.rstrip("/")
        self.key = api_key.strip()
        self.client = httpx.AsyncClient(timeout=timeout, transport=transport, follow_redirects=True)
        self.report: list[str] = []

    async def aclose(self) -> None:
        await self.client.aclose()

    async def _req(self, method: str, path: str, auth: Auth, json_body: Any = None) -> httpx.Response | None:
        headers: dict[str, str] = {"Accept": "application/json"}
        params: dict[str, str] = {}
        auth.apply(self.key, headers, params)
        try:
            return await self.client.request(method, f"{self.base}{path}", headers=headers, params=params or None,
                                             json=json_body)
        except httpx.HTTPError:
            return None

    # -- step 1: reachability --------------------------------------------------------
    async def reachable(self) -> bool:
        try:
            await self.client.get(self.base + "/")
            return True
        except httpx.HTTPError:
            return False

    # -- step 2: OpenAPI ----------------------------------------------------------------
    async def from_openapi(self) -> tuple[dict[str, Any], list[Auth]] | None:
        for path in OPENAPI_PATHS:
            for auth in (Auth("none"), AUTH_VARIANTS[0], AUTH_VARIANTS[1]):
                resp = await self._req("GET", path, auth)
                if resp is None or resp.status_code != 200:
                    continue
                try:
                    spec = resp.json()
                except ValueError:
                    continue
                if isinstance(spec, dict) and isinstance(spec.get("paths"), dict):
                    self.report.append(f"Found an API description at {path}.")
                    return spec, self._auth_from_spec(spec)
        return None

    @staticmethod
    def _auth_from_spec(spec: dict[str, Any]) -> list[Auth]:
        schemes = ((spec.get("components") or {}).get("securitySchemes") or
                   spec.get("securityDefinitions") or {})
        out: list[Auth] = []
        for scheme in schemes.values():
            t = str(scheme.get("type", "")).lower()
            if t == "http" and str(scheme.get("scheme", "")).lower() == "bearer":
                out.append(Auth("header", "Authorization", "Bearer "))
            elif t == "apikey":
                where = str(scheme.get("in", "header")).lower()
                out.append(Auth("query" if where == "query" else "header", str(scheme.get("name", "X-API-Key"))))
            elif t == "oauth2":
                out.append(Auth("header", "Authorization", "Bearer "))
        return out

    @staticmethod
    def endpoints_from_spec(spec: dict[str, Any]) -> dict[str, Any]:
        """Pick list / start / stop / status operations from OpenAPI paths."""
        found: dict[str, Any] = {}

        def template(path: str, op: dict[str, Any]) -> tuple[str, str]:
            """Return (url template with {id}, json body template)."""
            params = re.findall(r"\{([^}]+)\}", path)
            if params:
                return re.sub(r"\{[^}]+\}", "{id}", path, count=1), ""
            for p in op.get("parameters") or []:
                if p.get("in") == "query" and re.search(r"id|uuid|profile", str(p.get("name", "")), re.I):
                    return f"{path}?{p['name']}={{id}}", ""
            body = (((op.get("requestBody") or {}).get("content") or {}).get("application/json") or {}).get("schema")
            props = (body or {}).get("properties") or {}
            for name in props:
                if re.search(r"id|uuid|profile", name, re.I):
                    return path, '{"%s": "{id}"}' % name
            return path, ""

        list_candidates: list[tuple[int, str, str]] = []
        for path, ops in spec["paths"].items():
            low = path.lower()
            for method, op in ops.items():
                method = method.upper()
                if method not in ("GET", "POST", "PUT", "DELETE", "PATCH") or not isinstance(op, dict):
                    continue
                words = re.split(r"[^a-z]+", low + " " + str(op.get("operationId", "")).lower())
                about_profile = any(w.startswith(("profile", "browser", "user", "session")) for w in words)
                if not about_profile:
                    continue
                if any(w in words for w in START_WORDS) and "start" not in found:
                    url, body = template(path, op)
                    found["start"] = (method, url, body)
                elif any(w in words for w in STOP_WORDS) and "stop" not in found:
                    url, body = template(path, op)
                    found["stop"] = (method, url, body)
                elif any(w in words for w in ("status", "active", "running")) and "status" not in found and "{" in path:
                    url, body = template(path, op)
                    found["status"] = (method, url, body)
                elif "{" not in path and method in ("GET", "POST") and not any(
                        w in words for w in ("create", "delete", "update", "import", "export", "new")):
                    score = (0 if method == "GET" else 1, len(path))
                    if any(w in words for w in ("list", "search", "all")) or low.rstrip("/").endswith(
                            ("profiles", "browsers")):
                        score = (score[0] - 2, score[1])
                    list_candidates.append((score[0] * 1000 + score[1], method, path))
        if list_candidates:
            _, method, path = sorted(list_candidates)[0]
            found["list"] = (method, path, "{}" if method == "POST" else "")
        return found

    # -- step 3: profile list + auth -----------------------------------------------------
    async def probe_list(self, candidates: list[tuple[str, str, str]], auths: list[Auth]) -> dict[str, Any] | None:
        tried_auth_error = False
        for auth in auths:
            for method, path, body in candidates:
                resp = await self._req(method, path, auth, json_body={} if body else None)
                if resp is None:
                    continue
                if resp.status_code in (401, 403):
                    tried_auth_error = True
                    break  # wrong key style: try the next auth variant
                if resp.status_code != 200:
                    continue
                try:
                    data = resp.json()
                except ValueError:
                    continue
                if api_failure(data) and re.search(r"auth|token|key|unauthor|forbid", api_failure(data), re.I):
                    tried_auth_error = True
                    break
                located = find_list(data)
                if located is None:
                    continue
                items_path, items = located
                sample = items[0] if items else {}
                result = {"auth": auth, "method": method, "path": path, "body": body, "items_path": items_path,
                          "id": pick_key(sample, ID_KEYS) or "id", "name": pick_key(sample, NAME_KEYS) or "name",
                          "group": pick_key(sample, GROUP_KEYS), "count": len(items)}
                return result
        if tried_auth_error:
            self.report.append("The API rejected the key in every format FlowPilot tried — check the API key.")
        return None

    # -- step 4: start / stop ----------------------------------------------------------------
    async def probe_action(self, words: tuple[str, ...], list_path: str, auth: Auth) -> tuple[str, str, str] | None:
        base = list_path.rstrip("/")
        for suffix in ("/list", "/all", "/search"):
            if base.endswith(suffix):
                base = base[: -len(suffix)]
        prefixes = [base]
        if not base.endswith("s"):
            prefixes.append(base + "s")
        for root in ("/api/v1/profile", "/api/v1/profiles", "/api/profiles", "/profiles", "/api/v1/browser",
                     "/browser", "/api/browser"):
            if root not in prefixes:
                prefixes.append(root)
        templates: list[tuple[str, str, str, str]] = []  # method, url, body, control url
        for p in prefixes:
            for w in words:
                for method, tail, body in (
                        ("POST", "/{id}/W", ""), ("GET", "/{id}/W", ""),
                        ("POST", "/W/{id}", ""), ("GET", "/W/{id}", ""),
                        ("GET", "/W/p/{id}", ""), ("POST", "/W/p/{id}", ""),
                        ("GET", "/W?id={id}", ""), ("GET", "/W?user_id={id}", ""),
                        ("GET", "/W?profile_id={id}", ""),
                        ("POST", "/W", '{"id": "{id}"}'), ("POST", "/W", '{"profile_id": "{id}"}'),
                        ("POST", "/W", '{"uuid": "{id}"}'), ("POST", "/W", '{"profileId": "{id}"}')):
                    templates.append((method, p + tail.replace("W", w), body, p + tail.replace("W", CONTROL_WORD)))
        seen: set[tuple[str, str, str]] = set()
        fallback: tuple[str, str, str] | None = None
        for method, url, body, control in templates:
            if (method, url, body) in seen:
                continue
            seen.add((method, url, body))
            json_body = json.loads(body.replace("{id}", PROBE_ID)) if body else None
            resp = await self._req(method, url.replace("{id}", PROBE_ID), auth, json_body=json_body)
            if resp is None or resp.status_code in (401, 403) or _is_missing_route(resp):
                continue
            # A catch-all route (e.g. GET /profiles/{id}) also answers /profiles/start?id=… with
            # "profile not found". Ask the same thing with a made-up action word: if that gets the
            # same answer, the action word was never part of a real route.
            check = await self._req(method, control.replace("{id}", PROBE_ID), auth, json_body=json_body)
            if check is not None and not _is_missing_route(check) and check.status_code == resp.status_code:
                continue
            if resp.status_code in (400, 422):
                # the route exists but did not like the request (e.g. wrong body field): keep looking
                # for a template it accepts, and use this one only if nothing better answers
                fallback = fallback or (method, url, body)
                continue
            return method, url, body
        return fallback

    # -- all together ------------------------------------------------------------------------
    async def run(self) -> DiscoveryResult:
        if not await self.reachable():
            return DiscoveryResult(False, report=[f"Nothing answers at {self.base}. Is the browser app running and "
                                                  "is its local API turned on?"])
        auths: list[Auth] = list(AUTH_VARIANTS)
        endpoints: dict[str, Any] = {}
        spec = await self.from_openapi()
        list_candidates = [("GET", p, "") for p in LIST_PATHS] + [("POST", "/profile/search", "{}"),
                                                                   ("POST", "/api/v1/profile/search", "{}")]
        if spec is not None:
            endpoints = self.endpoints_from_spec(spec[0])
            spec_auths = spec[1]
            auths = spec_auths + [a for a in auths if a not in spec_auths]
            if "list" in endpoints:
                list_candidates.insert(0, endpoints["list"])
        listing = await self.probe_list(list_candidates, auths if self.key else [Auth("none")] + auths)
        if listing is None:
            self.report.append("Could not find the endpoint that lists profiles.")
            return DiscoveryResult(False, report=self.report)
        auth: Auth = listing["auth"]
        self.report.append(f"Profiles: {listing['method']} {listing['path']} ({listing['count']} found, "
                           f"id field “{listing['id']}”, name field “{listing['name']}”).")
        self.report.append(f"API key sent as {auth.describe()}.")
        start = endpoints.get("start") or await self.probe_action(START_WORDS, listing["path"], auth)
        stop = endpoints.get("stop") or await self.probe_action(STOP_WORDS, listing["path"], auth)
        if start is None:
            self.report.append("Could not find the endpoint that starts a profile.")
            return DiscoveryResult(False, report=self.report, profiles_found=listing["count"])
        self.report.append(f"Start: {start[0]} {start[1]}" + (f" body {start[2]}" if start[2] else "") + ".")
        self.report.append(f"Stop: {stop[0]} {stop[1]}." if stop else "Stop: not found (the browser will be closed "
                           "through its debugging connection instead).")
        config = {
            "list_url": listing["path"], "list_method": listing["method"], "list_body": listing["body"],
            "list_items_path": listing["items_path"], "list_id_path": listing["id"],
            "list_name_path": listing["name"], "list_group_path": listing["group"],
            "start_url": start[1], "start_method": start[0], "start_body": start[2],
            "stop_url": stop[1] if stop else "", "stop_method": stop[0] if stop else "GET",
            "stop_body": stop[2] if stop else "",
            "ws_path": "auto", "success_path": "auto",
            "key_location": auth.location, "key_name": auth.name, "key_prefix": auth.prefix,
        }
        status = endpoints.get("status")
        if status:
            config.update(status_url=status[1], status_path="", status_running_value="")
        return DiscoveryResult(True, config, self.report, listing["count"])


async def discover(base_url: str, api_key: str, transport: httpx.AsyncBaseTransport | None = None) -> DiscoveryResult:
    d = Discoverer(base_url, api_key, transport)
    try:
        return await d.run()
    finally:
        await d.aclose()
