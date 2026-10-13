from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from starlette.testclient import WebSocketDenialResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from alan import server
from tests.prep_chat_link_support import with_prep_chat_origin

_FIXED_BODY = {"detail": "host not allowed"}
_NEUTRAL_ORIGIN = "https://prep.example.test:8443"
_NEUTRAL_ORIGIN_HOST = "prep.example.test"
_FOREIGN_HOST = "rebind.example.test"
_LOOPBACK_BASE_URL = "http://127.0.0.1:8585"


class _RecordingApp:
    def __init__(self) -> None:
        self.scopes: list[Scope] = []

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        self.scopes.append(scope)
        if scope["type"] == "lifespan":
            return
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})


async def _drive(app: ASGIApp, scope: Scope) -> list[dict[str, Any]]:
    sent: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        return {"type": "http.disconnect"}

    async def send(message: Message) -> None:
        sent.append(dict(message))

    await app(scope, receive, send)
    return sent


def _http_scope(
    host: str | None, *, extra_headers: list[tuple[bytes, bytes]] | None = None
) -> Scope:
    headers: list[tuple[bytes, bytes]] = list(extra_headers or [])
    if host is not None:
        headers.append((b"host", host.encode()))
    return {"type": "http", "method": "GET", "headers": headers}


def _status_of(sent: list[dict[str, Any]]) -> int:
    return int(next(m["status"] for m in sent if m["type"].endswith("response.start")))


def _body_of(sent: list[dict[str, Any]]) -> bytes:
    return b"".join(m["body"] for m in sent if m["type"].endswith("response.body"))


_LOOPBACK_PASS_HOSTS = {
    "localhost": "localhost",
    "localhost_upper": "LOCALHOST",
    "localhost_with_port": "localhost:8585",
    "localhost_mixed_case_with_port": "LocalHost:9000",
    "ipv4": "127.0.0.1",
    "ipv4_with_port": "127.0.0.1:9000",
    "ipv6": "[::1]",
    "ipv6_with_port": "[::1]:8585",
}


@pytest.mark.parametrize(
    "host", _LOOPBACK_PASS_HOSTS.values(), ids=_LOOPBACK_PASS_HOSTS.keys()
)
async def test_loopback_hosts_pass_with_and_without_port_in_any_case(host: str) -> None:
    app = _RecordingApp()
    guard = server.HostAllowlistGuard(app)
    sent = await _drive(guard, _http_scope(host))
    assert _status_of(sent) == 204
    assert len(app.scopes) == 1


@pytest.mark.parametrize(
    "host", [f"{_NEUTRAL_ORIGIN_HOST}:8443", f"{_NEUTRAL_ORIGIN_HOST}:9000"]
)
async def test_configured_origin_hostname_passes_at_its_own_port_and_another_port(
    monkeypatch: pytest.MonkeyPatch, host: str
) -> None:
    with_prep_chat_origin(monkeypatch, _NEUTRAL_ORIGIN)
    app = _RecordingApp()
    guard = server.HostAllowlistGuard(app)
    sent = await _drive(guard, _http_scope(host))
    assert _status_of(sent) == 204


@pytest.mark.parametrize(
    "origin",
    [None, _NEUTRAL_ORIGIN_HOST, "https://[bad"],
    ids=["absent", "unparseable_no_scheme", "malformed_ipv6"],
)
async def test_without_a_usable_origin_only_loopback_hosts_pass(
    monkeypatch: pytest.MonkeyPatch, origin: str | None
) -> None:
    with_prep_chat_origin(monkeypatch, origin)
    app = _RecordingApp()
    guard = server.HostAllowlistGuard(app)
    loopback_sent = await _drive(guard, _http_scope("localhost"))
    assert _status_of(loopback_sent) == 204
    foreign_sent = await _drive(guard, _http_scope(_NEUTRAL_ORIGIN_HOST))
    assert _status_of(foreign_sent) == 400


_REFUSAL_CASE_HOSTS: dict[str, str | None] = {
    "foreign_name": _FOREIGN_HOST,
    "explicit_zero_address": "0.0.0.0",  # noqa: S104
    "trailing_dot": "localhost.",
    "userinfo": "user@localhost",
    "loopback_prefix": "localhost.evil.test",
    "loopback_suffix": "evil.localhost",
    "unbalanced_brackets": "[::1",
    "non_numeric_port": "localhost:abc",
    "empty_host": "",
    "missing_host": None,
}


@pytest.mark.parametrize(
    "host", _REFUSAL_CASE_HOSTS.values(), ids=_REFUSAL_CASE_HOSTS.keys()
)
async def test_each_refusal_case_returns_the_fixed_400(host: str | None) -> None:
    app = _RecordingApp()
    guard = server.HostAllowlistGuard(app)
    sent = await _drive(guard, _http_scope(host))
    assert _status_of(sent) == 400
    assert json.loads(_body_of(sent)) == _FIXED_BODY
    assert app.scopes == []


async def test_duplicate_host_header_is_refused() -> None:
    app = _RecordingApp()
    guard = server.HostAllowlistGuard(app)
    scope: Scope = {
        "type": "http",
        "method": "GET",
        "headers": [(b"host", b"localhost"), (b"host", b"127.0.0.1")],
    }
    sent = await _drive(guard, scope)
    assert _status_of(sent) == 400
    assert app.scopes == []


async def test_a_foreign_host_on_a_post_with_a_foreign_origin_returns_the_host_400() -> None:
    app = _RecordingApp()
    guard = server.HostAllowlistGuard(app)
    scope: Scope = {
        "type": "http",
        "method": "POST",
        "headers": [
            (b"host", _FOREIGN_HOST.encode()),
            (b"origin", b"https://rebind.example.test"),
        ],
    }
    sent = await _drive(guard, scope)
    assert _status_of(sent) == 400
    assert json.loads(_body_of(sent)) == _FIXED_BODY
    assert app.scopes == []


async def test_lifespan_scope_passes_through_to_the_wrapped_app() -> None:
    app = _RecordingApp()
    guard = server.HostAllowlistGuard(app)
    scope: Scope = {"type": "lifespan", "headers": []}
    await _drive(guard, scope)
    assert app.scopes == [scope]


async def test_websocket_scope_with_a_foreign_host_is_refused() -> None:
    app = _RecordingApp()
    guard = server.HostAllowlistGuard(app)
    scope: Scope = {"type": "websocket", "headers": [(b"host", _FOREIGN_HOST.encode())]}
    sent = await _drive(guard, scope)
    assert app.scopes == []
    assert _status_of(sent) == 400
    assert json.loads(_body_of(sent)) == _FIXED_BODY


@pytest.mark.parametrize("host", [_FOREIGN_HOST, f"{_FOREIGN_HOST}:8585"])
def test_served_app_refuses_a_foreign_host_with_and_without_a_port(host: str) -> None:
    with TestClient(server.served_app, base_url=_LOOPBACK_BASE_URL) as client:
        res = client.get("/api/health", headers={"Host": host})
    assert res.status_code == 400
    assert res.json() == _FIXED_BODY
    assert "rebind" not in res.text


def test_served_app_denies_a_websocket_with_a_foreign_host_via_the_asgi_extension() -> None:
    with TestClient(server.served_app, base_url=_LOOPBACK_BASE_URL) as client:
        with pytest.raises(WebSocketDenialResponse) as excinfo:
            with client.websocket_connect("/api/events", headers={"Host": _FOREIGN_HOST}):
                pass
    assert excinfo.value.status_code == 400
    assert excinfo.value.json() == _FIXED_BODY


def test_served_app_refuses_a_foreign_host_even_with_an_allowed_forwarded_host() -> None:
    with TestClient(server.served_app, base_url=_LOOPBACK_BASE_URL) as client:
        res = client.get(
            "/api/health",
            headers={
                "Host": _FOREIGN_HOST,
                "X-Forwarded-Host": "127.0.0.1",
                "Forwarded": "host=127.0.0.1",
            },
        )
    assert res.status_code == 400


@pytest.mark.parametrize("path", ["/api/events", "/", "/assets/app.js"])
def test_served_app_refuses_every_kind_of_request_with_a_foreign_host(path: str) -> None:
    with TestClient(server.served_app, base_url=_LOOPBACK_BASE_URL) as client:
        res = client.get(path, headers={"Host": _FOREIGN_HOST})
    assert res.status_code == 400


def test_served_app_completes_startup_serves_health_and_preserves_cors(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_PROJECTS_ROOT", str(tmp_path / "projects"))
    with TestClient(server.served_app, base_url=_LOOPBACK_BASE_URL) as client:
        health = client.get("/api/health")
        assert health.status_code == 200
        root = client.get("/")
        assert root.status_code != 400
        cors = client.get("/api/health", headers={"Origin": "http://localhost:5173"})
        assert "access-control-allow-origin" in cors.headers


def test_bare_app_is_not_host_guarded() -> None:
    with TestClient(server.app, base_url=_LOOPBACK_BASE_URL) as client:
        res = client.get("/api/health", headers={"Host": _FOREIGN_HOST})
    assert res.status_code != 400
