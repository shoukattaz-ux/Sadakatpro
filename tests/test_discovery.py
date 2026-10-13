"""Auto-detection of unknown browser APIs, against three simulated APIs of different styles."""

from __future__ import annotations

import json
import re

import httpx
import pytest

from flowpilot.adapters import AdapterError, create_adapter
from flowpilot.adapters.auto import DETECT_VERSION
from flowpilot.adapters.discovery import api_failure, discover, find_cdp, find_list

KEY = "hy3sobPeT6FVq4zSGk5H8MpkDnUmdOc"
PROFILES = [{"id": "p1", "name": "Shop 1", "group": "Main"}, {"id": "p2", "name": "Shop 2", "group": "Main"}]


def fastapi_like(calls: list) -> httpx.MockTransport:
    """Python/FastAPI style: /openapi.json, X-API-Key header, POST /profiles/{profile_id}/start."""
    spec = {
        "openapi": "3.1.0",
        "components": {"securitySchemes": {"key": {"type": "apiKey", "in": "header", "name": "X-API-Key"}}},
        "paths": {
            "/health": {"get": {}},
            "/profiles": {"get": {"operationId": "list_profiles"}, "post": {"operationId": "create_profile"}},
            "/profiles/{profile_id}": {"delete": {"operationId": "delete_profile"}},
            "/profiles/{profile_id}/start": {"post": {"operationId": "start_profile"}},
            "/profiles/{profile_id}/stop": {"post": {"operationId": "stop_profile"}},
        },
    }

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append((req.method, req.url.path))
        if req.url.path == "/openapi.json":
            return httpx.Response(200, json=spec)
        if req.headers.get("X-API-Key") != KEY:
            return httpx.Response(401, json={"detail": "Invalid API key"})
        if req.method == "GET" and req.url.path == "/profiles":
            return httpx.Response(200, json=PROFILES)
        m = re.fullmatch(r"/profiles/([\w-]+)/(start|stop)", req.url.path)
        if m and req.method == "POST":
            if m.group(1) not in ("p1", "p2"):
                return httpx.Response(404, json={"detail": "Profile not found"})
            if m.group(2) == "start":
                return httpx.Response(200, json={"profile_id": m.group(1), "cdp": {"port": 51234}})
            return httpx.Response(200, json={"ok": True})
        return httpx.Response(404, json={"detail": "Not Found"})

    return httpx.MockTransport(handler)


def express_like(calls: list) -> httpx.MockTransport:
    """Node/Express style: no OpenAPI, Bearer token, {data: {items: [...]}} and GET start/stop."""

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append((req.method, req.url.path))
        path = req.url.path
        known = path == "/api/v1/profiles" or re.fullmatch(r"/api/v1/profiles/[\w-]+/(start|stop)", path)
        if not known:
            return httpx.Response(404, text=f"<pre>Cannot {req.method} {path}</pre>",
                                  headers={"content-type": "text/html"})
        if req.headers.get("Authorization") != f"Bearer {KEY}":
            return httpx.Response(401, json={"success": False, "message": "unauthorized"})
        if path == "/api/v1/profiles" and req.method == "GET":
            return httpx.Response(200, json={"success": True, "data": {"items": [
                {"uuid": "a-1", "title": "Alpha"}, {"uuid": "b-2", "title": "Beta"}]}})
        if req.method != "GET":
            return httpx.Response(404, text=f"Cannot {req.method} {path}")
        pid, action = path.split("/")[-2:]
        if pid not in ("a-1", "b-2"):
            return httpx.Response(404, json={"success": False, "message": "Profile does not exist"})
        if action == "start":
            return httpx.Response(200, json={"success": True, "data": {"wsEndpoint": "ws://127.0.0.1:9333/devtools/browser/x"}})
        return httpx.Response(200, json={"success": True})

    return httpx.MockTransport(handler)


def body_style(calls: list) -> httpx.MockTransport:
    """Query-token auth, POST /browser/start with {"profile_id": ...}, 422 for a wrong body."""

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append((req.method, str(req.url)))
        if req.url.params.get("token") != KEY and req.url.path in ("/browser/list", "/browser/start", "/browser/stop"):
            return httpx.Response(403, json={"error": "bad token"})
        if req.url.path == "/browser/list" and req.method == "GET":
            return httpx.Response(200, json={"code": 0, "list": [{"profile_id": "z9", "profile_name": "Zed"}]})
        if req.url.path in ("/browser/start", "/browser/stop"):
            if req.method != "POST":
                return httpx.Response(405, json={"detail": "Method Not Allowed"})
            body = json.loads(req.content or b"{}")
            if "profile_id" not in body:
                return httpx.Response(422, json={"detail": [{"loc": ["body", "profile_id"], "msg": "required"}]})
            if body["profile_id"] != "z9":
                return httpx.Response(200, json={"code": 1, "msg": "profile not found"})
            return httpx.Response(200, json={"code": 0, "data": {"debug_port": "40123"}} if "start" in req.url.path
                                  else {"code": 0})
        return httpx.Response(404, json={"detail": "Not Found"})

    return httpx.MockTransport(handler)


def test_helpers():
    assert find_list({"data": {"list": [{"id": 1}]}}) == ("data.list", [{"id": 1}])
    assert find_list([{"id": 1}]) == ("", [{"id": 1}])
    assert find_list({"status": "ok"}) is None
    assert find_cdp({"data": {"ws": {"puppeteer": "ws://127.0.0.1:1/devtools/browser/a"}}}).startswith("ws://")
    assert find_cdp({"result": {"debug_port": 9222}}) == "http://127.0.0.1:9222"
    assert find_cdp({"debuggerAddress": "127.0.0.1:9555"}) == "http://127.0.0.1:9555"
    assert find_cdp({"nothing": True}) == ""
    assert api_failure({"success": False, "message": "nope"}) == "nope"
    assert api_failure({"code": 1, "msg": "bad"}) == "bad" and api_failure({"code": 0, "msg": "ok"}) == ""


async def test_openapi_style_api():
    calls: list = []
    result = await discover("http://127.0.0.1:9378", KEY, fastapi_like(calls))
    assert result.ok, result.summary()
    c = result.config
    assert (c["list_url"], c["list_method"], c["list_id_path"], c["list_name_path"]) == ("/profiles", "GET", "id", "name")
    assert (c["start_method"], c["start_url"]) == ("POST", "/profiles/{id}/start")
    assert (c["stop_method"], c["stop_url"]) == ("POST", "/profiles/{id}/stop")
    assert (c["key_location"], c["key_name"], c["key_prefix"]) == ("header", "X-API-Key", "")
    assert "API description" in result.summary()
    assert not any(m == "POST" and p == "/profiles" for m, p in calls)  # never creates profiles


async def test_express_style_api_by_probing():
    calls: list = []
    result = await discover("http://127.0.0.1:9378", KEY, express_like(calls))
    assert result.ok, result.summary()
    c = result.config
    assert (c["list_url"], c["list_items_path"], c["list_id_path"], c["list_name_path"]) == (
        "/api/v1/profiles", "data.items", "uuid", "title")
    assert (c["start_method"], c["start_url"]) == ("GET", "/api/v1/profiles/{id}/start")
    assert (c["key_location"], c["key_name"], c["key_prefix"]) == ("header", "Authorization", "Bearer ")
    # detection never touched a real profile
    assert not any(p.startswith(("/api/v1/profiles/a-1", "/api/v1/profiles/b-2")) for _, p in calls)


async def test_body_style_api_and_wrong_key():
    calls: list = []
    result = await discover("http://127.0.0.1:9378", KEY, body_style(calls))
    assert result.ok, result.summary()
    c = result.config
    assert (c["start_method"], c["start_url"], c["start_body"]) == ("POST", "/browser/start", '{"profile_id": "{id}"}')
    assert (c["key_location"], c["key_name"]) == ("query", "token")
    bad = await discover("http://127.0.0.1:9378", "wrong-key", body_style([]))
    assert not bad.ok and "rejected the key" in bad.summary()


@pytest.mark.parametrize("make", [fastapi_like, express_like, body_style])
async def test_auto_adapter_end_to_end(make):
    adapter = create_adapter("auto", "http://127.0.0.1:9378", KEY, {})
    adapter._transport = make([])  # noqa: SLF001 - inject the fake API
    adapter.http = adapter._make_client()  # noqa: SLF001
    result = await adapter.test_connection()
    assert result.ok, result.message
    profiles = await adapter.list_profiles()
    assert profiles and profiles[0].name in ("Shop 1", "Alpha", "Zed")
    endpoint = await adapter.start_profile(profiles[0].id)
    assert endpoint in ("http://127.0.0.1:51234", "ws://127.0.0.1:9333/devtools/browser/x", "http://127.0.0.1:40123")
    await adapter.stop_profile(profiles[0].id)
    saved = adapter.detected_settings()
    assert saved["detected"] == DETECT_VERSION and saved["ws_path"] == "auto"
    # a new adapter built from the saved settings works without detecting again
    again = create_adapter("auto", "http://127.0.0.1:9378", KEY, saved)
    assert again.is_detected


async def test_auto_adapter_reports_unreachable_and_api_errors():
    def refuse(req):
        raise httpx.ConnectError("refused", request=req)

    adapter = create_adapter("auto", "http://127.0.0.1:1", KEY, {})
    adapter._transport = httpx.MockTransport(refuse)  # noqa: SLF001
    result = await adapter.test_connection()
    assert not result.ok and "Nothing answers" in result.message
    calls: list = []
    adapter = create_adapter("auto", "http://127.0.0.1:9378", KEY, {})
    adapter._transport = body_style(calls)  # noqa: SLF001
    adapter.http = adapter._make_client()  # noqa: SLF001
    with pytest.raises(AdapterError, match="profile not found"):
        await adapter.start_profile("does-not-exist")


def catch_all_api(calls: list) -> httpx.MockTransport:
    """GET /profiles/{id} answers *any* word with "Profile not found" — e.g. /profiles/start?id=…
    looks like a start route but is not. The real start is POST /profiles/{id}/open."""

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append((req.method, str(req.url)))
        if req.headers.get("X-API-Key") != KEY:
            return httpx.Response(401, json={"detail": "Invalid API key"})
        parts = [p for p in req.url.path.split("/") if p]
        if req.method == "GET" and parts == ["profiles"]:
            return httpx.Response(200, json={"profiles": PROFILES})
        if req.method == "GET" and len(parts) == 2 and parts[0] == "profiles":
            if parts[1] in ("p1", "p2"):
                return httpx.Response(200, json=PROFILES[0])
            return httpx.Response(404, json={"detail": "Profile not found"})
        if req.method == "POST" and len(parts) == 3 and parts[0] == "profiles" and parts[2] in ("open", "close"):
            if parts[1] not in ("p1", "p2"):
                return httpx.Response(404, json={"detail": "Profile not found"})
            if parts[2] == "open":
                return httpx.Response(200, json={"status": "running", "debug_port": 45111})
            return httpx.Response(200, json={"status": "stopped"})
        return httpx.Response(404, json={"detail": "Not Found"})

    return httpx.MockTransport(handler)


async def test_catch_all_route_is_not_taken_for_start():
    result = await discover("http://127.0.0.1:9378", KEY, catch_all_api([]))
    assert result.ok, result.summary()
    c = result.config
    assert (c["start_method"], c["start_url"]) == ("POST", "/profiles/{id}/open")
    assert (c["stop_method"], c["stop_url"]) == ("POST", "/profiles/{id}/close")


async def test_old_or_wrong_saved_endpoints_are_found_again(tmp_path):
    from flowpilot.core.adapter_pool import AdapterPool
    from flowpilot.security import SecretBox
    from flowpilot.storage.db import Database
    from flowpilot.storage.models import Connection
    from flowpilot.storage.repositories import Repositories

    wrong = {"list_url": "/profiles", "list_items_path": "profiles", "list_id_path": "id", "list_name_path": "name",
             "start_url": "/profiles/start?id={id}", "start_method": "GET", "ws_path": "auto",
             "success_path": "auto", "key_location": "header", "key_name": "X-API-Key", "key_prefix": ""}
    # saved by v0.2.1 (detection version 1): detected again before the first start
    old = create_adapter("auto", "http://127.0.0.1:9378", KEY, {**wrong, "detected": "1"})
    assert not old.is_detected
    old._transport = catch_all_api([])  # noqa: SLF001
    assert await old.start_profile("p1") == "http://127.0.0.1:45111"

    # saved by this version but wrong anyway: a 404 on start triggers one new detection, which is saved
    db = Database(tmp_path / "db.sqlite")
    repos = Repositories(db, SecretBox(tmp_path, use_dpapi=False))
    conn = repos.connections.save(Connection(type="auto", name="Stealth", base_url="http://127.0.0.1:9378",
                                             extra_json=json.dumps({**wrong, "detected": DETECT_VERSION})), api_key=KEY)
    pool = AdapterPool(repos.connections)
    adapter = pool.get(conn)
    assert adapter.is_detected
    adapter._transport = catch_all_api([])  # noqa: SLF001
    adapter.http = adapter._make_client()  # noqa: SLF001
    assert await adapter.start_profile("p1") == "http://127.0.0.1:45111"
    saved = repos.connections.extra(repos.connections.get(conn.id))
    assert saved["start_url"] == "/profiles/{id}/open"
    assert pool.get(repos.connections.get(conn.id)) is adapter  # still the same live adapter
    with pytest.raises(AdapterError, match="Profile not found"):
        await adapter.start_profile("missing")  # a real "no such profile" is reported, not retried forever
    await pool.aclose()
    db.dispose()
