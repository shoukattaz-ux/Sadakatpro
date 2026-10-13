"""Adapters against mocked vendor APIs (httpx.MockTransport)."""

from __future__ import annotations

import json

import httpx
import pytest

from flowpilot.adapters import ADAPTERS, AdapterError, create_adapter
from flowpilot.adapters.adspower import AdsPowerAdapter
from flowpilot.adapters.base import HttpClient, json_path
from flowpilot.adapters.custom import CustomAdapter
from flowpilot.adapters.dolphin import DolphinAdapter
from flowpilot.adapters.gologin import GoLoginAdapter
from flowpilot.adapters.multilogin import MultiloginAdapter
from flowpilot.adapters.octo import OctoAdapter


def transport(handler):
    calls: list[httpx.Request] = []

    def wrapped(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return handler(request)

    t = httpx.MockTransport(wrapped)
    t.calls = calls  # type: ignore[attr-defined]
    return t


def test_json_path():
    data = {"data": {"ws": {"puppeteer": "ws://x"}, "list": [{"id": 1}, {"id": 2}]}}
    assert json_path(data, "data.ws.puppeteer") == "ws://x"
    assert json_path(data, "data.list.1.id") == 2
    assert json_path(data, "data.list.5.id") is None
    assert json_path(data, "nope.x") is None


def test_registry_complete():
    assert set(ADAPTERS) == {"adspower", "gologin", "dolphin", "multilogin", "octo", "stealth", "auto", "custom", "local"}
    with pytest.raises(AdapterError):
        create_adapter("unknown", "", "")


async def test_http_client_retries_then_friendly_error():
    attempts = {"n": 0}

    def handler(req):
        attempts["n"] += 1
        return httpx.Response(503)

    client = HttpClient("http://api", retries=2, backoff=0.01, transport=transport(handler))
    with pytest.raises(AdapterError, match="failed"):
        await client.request("GET", "/x")
    assert attempts["n"] == 3

    client = HttpClient("http://api", retries=0, transport=transport(lambda r: httpx.Response(401)))
    with pytest.raises(AdapterError, match="API key"):
        await client.request("GET", "/x")

    def refuse(req):
        raise httpx.ConnectError("refused", request=req)

    client = HttpClient("http://api", retries=1, backoff=0.01, transport=transport(refuse))
    with pytest.raises(AdapterError, match="Is the anti-detect browser app running"):
        await client.request("GET", "/x")


async def test_adspower():
    def handler(req: httpx.Request):
        path = req.url.path
        assert req.headers.get("Authorization") == "Bearer KEY"
        if path == "/status":
            return httpx.Response(200, json={"code": 0, "msg": "success"})
        if path == "/api/v1/user/list":
            return httpx.Response(200, json={"code": 0, "data": {"list": [
                {"user_id": "u1", "name": "One", "group_name": "G"}, {"user_id": "u2", "name": "", "serial_number": "7"}]}})
        if path == "/api/v1/browser/start":
            assert req.url.params["user_id"] == "u1"
            return httpx.Response(200, json={"code": 0, "data": {"ws": {"puppeteer": "ws://127.0.0.1:9/devtools/browser/x"}}})
        if path == "/api/v1/browser/stop":
            return httpx.Response(200, json={"code": 0})
        if path == "/api/v1/browser/active":
            return httpx.Response(200, json={"code": 0, "data": {"status": "Active"}})
        return httpx.Response(404)

    a = AdsPowerAdapter("http://local.adspower.net:50325", "KEY", transport=transport(handler))
    result = await a.test_connection()
    assert result.ok and "2 profile" in result.message
    profiles = await a.list_profiles()
    assert [(p.id, p.name, p.group) for p in profiles] == [("u1", "One", "G"), ("u2", "7", "")]
    assert (await a.start_profile("u1")).startswith("ws://")
    await a.stop_profile("u1")
    assert await a.is_running("u1")


async def test_adspower_error_and_rate_limit(monkeypatch):
    import flowpilot.adapters.adspower as mod

    async def no_sleep(_):
        return None

    monkeypatch.setattr(mod.asyncio, "sleep", no_sleep)
    state = {"n": 0}

    def handler(req):
        state["n"] += 1
        if state["n"] < 3:
            return httpx.Response(200, json={"code": -1, "msg": "Too many request per second"})
        return httpx.Response(200, json={"code": -1, "msg": "Profile does not exist"})

    a = AdsPowerAdapter("", "", transport=transport(handler))
    with pytest.raises(AdapterError, match="Profile does not exist"):
        await a.start_profile("zzz")
    assert state["n"] == 3


async def test_gologin():
    def handler(req: httpx.Request):
        if req.url.host == "api.gologin.com":
            assert req.headers["Authorization"] == "Bearer T"
            return httpx.Response(200, json={"profiles": [{"id": "g1", "name": "GL", "tags": [], "folders": ["F"]}],
                                             "allProfilesCount": 1})
        if req.url.path == "/browser/start-profile":
            assert json.loads(req.content) == {"profileId": "g1", "sync": True}
            return httpx.Response(200, json={"status": "success", "wsUrl": "ws://127.0.0.1:1/x"})
        if req.url.path == "/browser/stop-profile":
            return httpx.Response(200, json={})
        return httpx.Response(404)

    a = GoLoginAdapter("http://localhost:36912", "T", transport=transport(handler))
    assert [(p.id, p.group) for p in await a.list_profiles()] == [("g1", "F")]
    assert await a.start_profile("g1") == "ws://127.0.0.1:1/x"
    await a.stop_profile("g1")
    await a.aclose()


async def test_dolphin():
    t = transport(lambda req: (
        httpx.Response(200, json={"success": True}) if req.url.path.endswith("/auth/login-with-token") else
        httpx.Response(200, json={"success": True, "automation": {"port": 5555, "wsEndpoint": "/devtools/browser/abc"}})
        if req.url.path.endswith("/start") else
        httpx.Response(200, json={"data": [{"id": 7, "name": "D", "tags": ["t"]}], "last_page": 1})
        if req.url.host == "dolphin-anty-api.com" else httpx.Response(200, json={"success": True})))
    a = DolphinAdapter("http://localhost:3001/v1.0", "TOKEN", transport=t)
    assert await a.start_profile("7") == "ws://127.0.0.1:5555/devtools/browser/abc"
    assert any(r.url.path.endswith("/auth/login-with-token") for r in t.calls)
    assert [(p.id, p.tags) for p in await a.list_profiles()] == [("7", ["t"])]
    await a.stop_profile("7")


async def test_multilogin():
    def handler(req: httpx.Request):
        if req.url.host == "api.multilogin.com":
            body = json.loads(req.content)
            assert body["folder_id"] == "F1"
            return httpx.Response(200, json={"data": {"profiles": [{"id": "m1", "name": "ML"}]}})
        if "/start" in req.url.path:
            assert req.url.params["automation_type"] == "playwright"
            assert "/f/F1/p/m1/" in req.url.path
            return httpx.Response(200, json={"data": {"port": "40001"}})
        return httpx.Response(200, json={"data": {"states": {}}})

    a = MultiloginAdapter("https://launcher.mlx.yt:45001", "TOK", {"folder_id": "F1"}, transport=transport(handler))
    assert [p.id for p in await a.list_profiles()] == ["m1"]
    assert await a.start_profile("m1") == "http://127.0.0.1:40001"
    await a.stop_profile("m1")
    assert not await a.is_running("m1")

    no_folder = MultiloginAdapter("", "TOK", transport=transport(handler))
    with pytest.raises(AdapterError, match="Folder ID"):
        await no_folder.start_profile("m1")


async def test_octo():
    def handler(req: httpx.Request):
        if req.url.host == "app.octobrowser.net":
            assert req.headers["X-Octo-Api-Token"] == "OT"
            return httpx.Response(200, json={"data": [{"uuid": "o1", "title": "Octo", "tags": []}]})
        if req.url.path == "/api/profiles/start":
            assert json.loads(req.content)["debug_port"] is True
            return httpx.Response(200, json={"ws_endpoint": "ws://127.0.0.1:2/devtools/browser/o"})
        if req.url.path == "/api/profiles/active":
            return httpx.Response(200, json=[{"uuid": "o1"}])
        return httpx.Response(200, json={})

    a = OctoAdapter("http://localhost:58888", "OT", transport=transport(handler))
    assert [p.name for p in await a.list_profiles()] == ["Octo"]
    assert (await a.start_profile("o1")).startswith("ws://")
    assert await a.is_running("o1")
    await a.stop_profile("o1")


async def test_custom_adapter_header_and_paths():
    extra = {
        "start_url": "/start/{id}", "start_method": "POST", "start_body": '{"profile": "{id}"}',
        "ws_path": "result.cdp", "stop_url": "/stop?id={id}", "list_url": "/profiles",
        "list_items_path": "items", "list_id_path": "pid", "list_name_path": "meta.title",
        "key_location": "header", "key_name": "X-Key", "key_prefix": "", "success_path": "ok", "success_value": "True",
        "status_url": "/status/{id}", "status_path": "state", "status_running_value": "on",
    }

    def handler(req: httpx.Request):
        assert req.headers["X-Key"] == "SECRET"
        if req.url.path == "/start/abc":
            assert json.loads(req.content) == {"profile": "abc"}
            return httpx.Response(200, json={"ok": True, "result": {"cdp": "127.0.0.1:9222/devtools/browser/z"}})
        if req.url.path == "/profiles":
            return httpx.Response(200, json={"ok": True, "items": [{"pid": "abc", "meta": {"title": "Mine"}}]})
        if req.url.path == "/status/abc":
            return httpx.Response(200, json={"ok": True, "state": "ON"})
        return httpx.Response(200, json={"ok": True})

    a = CustomAdapter("http://my-browser:1234", "SECRET", extra, transport=transport(handler))
    assert await a.start_profile("abc") == "ws://127.0.0.1:9222/devtools/browser/z"
    assert [(p.id, p.name) for p in await a.list_profiles()] == [("abc", "Mine")]
    assert await a.is_running("abc")
    await a.stop_profile("abc")

    port_only = CustomAdapter("http://h", "", {**extra, "key_location": "none", "success_path": ""},
                              transport=transport(lambda r: httpx.Response(200, json={"result": {"cdp": 9333}})))
    assert await port_only.start_profile("x") == "http://127.0.0.1:9333"


async def test_custom_adapter_reports_failures():
    def handler(req):
        return httpx.Response(200, json={"code": 1, "msg": "license expired"})

    a = CustomAdapter("http://h", "k", {"success_path": "code", "success_value": "0", "key_location": "query",
                                        "key_name": "token"}, transport=transport(handler))
    with pytest.raises(AdapterError, match="license expired"):
        await a.start_profile("1")
    a2 = CustomAdapter("http://h", "", {"start_url": "/s", "ws_path": "data.ws", "success_path": ""},
                       transport=transport(lambda r: httpx.Response(200, json={"data": {}})))
    with pytest.raises(AdapterError, match="JSON path"):
        await a2.start_profile("1")


async def test_local_adapter_lists_and_starts(tmp_path):
    from tests.conftest import chromium_available

    a = create_adapter("local", "", "", {"profiles_dir": str(tmp_path / "p"), "headless": "1"})
    profiles = await a.list_profiles()
    assert [p.id for p in profiles] == ["Profile 1", "Profile 2", "Profile 3"]
    if not chromium_available():
        pytest.skip("no browser")
    ws = await a.start_profile("Profile 1")
    assert ws.startswith("ws://")
    assert await a.is_running("Profile 1")
    await a.stop_profile("Profile 1")
    assert not await a.is_running("Profile 1")
