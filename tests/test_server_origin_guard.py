from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient
from starlette.testclient import WebSocketDenialResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send
from starlette.websockets import WebSocketDisconnect

from alan import server
from tests.prep_chat_link_support import with_prep_chat_origin

_LOOPBACK_BASE_URL = "http://127.0.0.1:8585"
_LOOPBACK_HOST = "127.0.0.1:8585"
_TERMINAL_PATH = "/api/operator/terminal"
_FIXED_ORIGIN_BODY = {"detail": "origin not allowed"}
_FIXED_HOST_BODY = {"detail": "host not allowed"}
_NEUTRAL_ORIGIN = "https://prep.example.test:8443"
_FOREIGN_ORIGIN = "https://rebind.example.test"


class _RecordingApp:
    def __init__(self) -> None:
        self.scopes: list[Scope] = []

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        self.scopes.append(scope)
        if scope["type"] == "lifespan":
            return
        prefix = "websocket." if scope["type"] == "websocket" else ""
        await send({"type": prefix + "http.response.start", "status": 204, "headers": []})
        await send({"type": prefix + "http.response.body", "body": b""})


async def _drive(app: ASGIApp, scope: Scope) -> list[dict[str, Any]]:
    sent: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        return {"type": "http.disconnect"}

    async def send(message: Message) -> None:
        sent.append(dict(message))

    await app(scope, receive, send)
    return sent


def _scope(
    kind: str, host: str | None, origins: list[str], *, method: str | None = None
) -> Scope:
    headers: list[tuple[bytes, bytes]] = []
    if host is not None:
        headers.append((b"host", host.encode()))
    headers.extend((b"origin", origin.encode()) for origin in origins)
    scope: Scope = {"type": kind, "headers": headers}
    if method is not None:
        scope["method"] = method
    return scope


def _status(sent: list[dict[str, Any]]) -> int:
    return int(next(m["status"] for m in sent if m["type"].endswith("response.start")))


def _body(sent: list[dict[str, Any]]) -> Any:
    return json.loads(b"".join(m["body"] for m in sent if m["type"].endswith("response.body")))


@pytest.mark.parametrize(
    "origin",
    [
        "http://localhost:5173",
        "http://127.0.0.1:8585",
        "http://[::1]:8585",
        "HTTP://LOCALHOST:5173",
        "http://LocalHost",
        "http://localhost:65535",
        "http://localhost:1",
    ],
)
async def test_a_websocket_with_a_loopback_origin_passes(origin: str) -> None:
    app = _RecordingApp()
    guard = server.HostAllowlistGuard(app)
    sent = await _drive(guard, _scope("websocket", "localhost:8585", [origin]))
    assert len(app.scopes) == 1
    assert _status(sent) == 204


async def test_a_websocket_with_the_configured_origin_hostname_passes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with_prep_chat_origin(monkeypatch, _NEUTRAL_ORIGIN)
    app = _RecordingApp()
    guard = server.HostAllowlistGuard(app)
    scope = _scope("websocket", "prep.example.test:9000", ["https://prep.example.test:9000"])
    await _drive(guard, scope)
    assert len(app.scopes) == 1


_REFUSED_ORIGINS: dict[str, list[str]] = {
    "missing": [],
    "null": ["null"],
    "malformed_ipv6": ["http://[bad"],
    "no_scheme": ["localhost"],
    "empty": [""],
    "foreign": [_FOREIGN_ORIGIN],
    "loopback_lookalike": ["http://localhost.evil.test"],
    "loopback_suffix": ["http://evil.localhost"],
    "duplicate_same": ["http://localhost", "http://localhost"],
    "duplicate_mixed": ["http://localhost", _FOREIGN_ORIGIN],
    "unreachable_zero": ["http://0.0.0.0"],  # noqa: S104
    "port_not_numeric": ["http://localhost:bad"],
    "port_out_of_range": ["http://localhost:99999"],
    "port_zero": ["http://localhost:0"],
    "port_empty": ["http://localhost:"],
    "scheme_relative": ["//localhost"],
    "non_http_scheme": ["ftp://localhost"],
    "with_path": ["http://localhost/some/path"],
    "trailing_slash": ["http://localhost/"],
    "with_query": ["http://localhost?x=1"],
    "with_fragment": ["http://localhost#frag"],
    "with_userinfo": ["http://user@localhost"],
    "trailing_newline": ["http://localhost\n"],
    "surrounding_space": [" http://localhost"],
}


@pytest.mark.parametrize(
    "origins", _REFUSED_ORIGINS.values(), ids=_REFUSED_ORIGINS.keys()
)
async def test_a_websocket_with_a_bad_origin_is_refused_before_the_app(
    origins: list[str],
) -> None:
    app = _RecordingApp()
    guard = server.HostAllowlistGuard(app)
    sent = await _drive(guard, _scope("websocket", "localhost", origins))
    assert app.scopes == []
    assert _status(sent) == 403
    assert _body(sent) == _FIXED_ORIGIN_BODY


async def test_a_foreign_host_is_refused_as_a_host_even_with_a_good_origin() -> None:
    app = _RecordingApp()
    guard = server.HostAllowlistGuard(app)
    sent = await _drive(
        guard, _scope("websocket", "rebind.example.test", ["http://localhost:5173"])
    )
    assert app.scopes == []
    assert _status(sent) == 400
    assert _body(sent) == _FIXED_HOST_BODY


@pytest.mark.parametrize(
    "origins",
    [[], ["null"], [_FOREIGN_ORIGIN], ["http://localhost", "http://localhost"]],
    ids=["missing", "null", "foreign", "duplicate"],
)
@pytest.mark.parametrize("method", ["GET", "HEAD", "OPTIONS"])
async def test_safe_method_http_scopes_are_not_subject_to_the_origin_rule(
    origins: list[str], method: str
) -> None:
    app = _RecordingApp()
    guard = server.HostAllowlistGuard(app)
    sent = await _drive(guard, _scope("http", "localhost", origins, method=method))
    assert len(app.scopes) == 1
    assert _status(sent) == 204


def test_served_app_lets_a_websocket_with_a_loopback_origin_reach_the_route() -> None:
    with TestClient(server.served_app, base_url=_LOOPBACK_BASE_URL) as client:
        with pytest.raises(WebSocketDisconnect) as excinfo:
            with client.websocket_connect(
                f"{_TERMINAL_PATH}?ticket=none",
                headers={"Host": _LOOPBACK_HOST, "Origin": "http://127.0.0.1:8585"},
            ):
                pass
    assert excinfo.value.code == 4401


@pytest.mark.parametrize(
    "headers",
    [{}, {"Origin": "null"}, {"Origin": _FOREIGN_ORIGIN}, {"Origin": "http://[bad"}],
    ids=["missing", "null", "foreign", "malformed"],
)
def test_served_app_denies_a_websocket_handshake_with_a_bad_origin(
    headers: dict[str, str],
) -> None:
    with TestClient(server.served_app, base_url=_LOOPBACK_BASE_URL) as client:
        with pytest.raises(WebSocketDenialResponse) as excinfo:
            with client.websocket_connect(
                f"{_TERMINAL_PATH}?ticket=none", headers={"Host": _LOOPBACK_HOST, **headers}
            ):
                pass
    assert excinfo.value.status_code == 403
    assert excinfo.value.json() == _FIXED_ORIGIN_BODY


def test_served_app_denies_a_websocket_handshake_with_a_foreign_host() -> None:
    with TestClient(server.served_app, base_url=_LOOPBACK_BASE_URL) as client:
        with pytest.raises(WebSocketDenialResponse) as excinfo:
            with client.websocket_connect(
                f"{_TERMINAL_PATH}?ticket=none",
                headers={"Host": "rebind.example.test", "Origin": "http://localhost:5173"},
            ):
                pass
    assert excinfo.value.status_code == 400
    assert excinfo.value.json() == _FIXED_HOST_BODY


def test_served_app_leaves_safe_method_http_requests_with_a_foreign_origin_unchanged() -> None:
    with TestClient(server.served_app, base_url=_LOOPBACK_BASE_URL) as client:
        with_origin = client.get("/api/health", headers={"Origin": _FOREIGN_ORIGIN})
        without_origin = client.get("/api/health")
    assert with_origin.status_code == 200
    assert without_origin.status_code == 200
