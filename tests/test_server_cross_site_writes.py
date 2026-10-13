from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from alan import server
from tests.prep_chat_link_support import with_prep_chat_origin

_LOOPBACK_BASE_URL = "http://127.0.0.1:8585"
_FIXED_ORIGIN_BODY = {"detail": "origin not allowed"}
_NEUTRAL_ORIGIN = "https://prep.example.test:8443"
_FOREIGN_ORIGIN = "https://rebind.example.test"
_LOOPBACK_ORIGIN = "http://localhost:5173"
_WRITE_METHODS = ["POST", "PUT", "PATCH", "DELETE"]
_SAFE_METHODS = ["GET", "HEAD", "OPTIONS"]
_PROJECTS_PATH = "/api/projects"


class _RecordingApp:
    def __init__(self) -> None:
        self.call_count = 0

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        self.call_count += 1
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})


@dataclass(frozen=True)
class _Outcome:
    status: int
    body: bytes
    app_calls: int


async def _drive(app: ASGIApp, scope: Scope) -> list[dict[str, Any]]:
    sent: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        return {"type": "http.disconnect"}

    async def send(message: Message) -> None:
        sent.append(dict(message))

    await app(scope, receive, send)
    return sent


def _status_of(sent: list[dict[str, Any]]) -> int:
    return int(next(m["status"] for m in sent if m["type"].endswith("response.start")))


def _body_of(sent: list[dict[str, Any]]) -> bytes:
    return b"".join(m["body"] for m in sent if m["type"].endswith("response.body"))


def _scope(
    method: str,
    *,
    origins: tuple[str, ...] = (),
    fetch_sites: tuple[str, ...] = (),
    host: str = "localhost:8585",
) -> Scope:
    headers: list[tuple[bytes, bytes]] = [(b"host", host.encode())]
    headers.extend((b"origin", origin.encode()) for origin in origins)
    headers.extend((b"sec-fetch-site", site.encode()) for site in fetch_sites)
    return {"type": "http", "method": method, "headers": headers}


async def _run(scope: Scope) -> _Outcome:
    app = _RecordingApp()
    sent = await _drive(server.HostAllowlistGuard(app), scope)
    return _Outcome(_status_of(sent), _body_of(sent), app.call_count)


def _assert_refused(outcome: _Outcome) -> None:
    assert outcome.status == 403
    assert json.loads(outcome.body) == _FIXED_ORIGIN_BODY
    assert outcome.app_calls == 0


def _assert_served(outcome: _Outcome) -> None:
    assert outcome.status == 204
    assert outcome.body == b""
    assert outcome.app_calls == 1


@pytest.mark.parametrize("method", _WRITE_METHODS)
async def test_a_write_with_a_foreign_origin_is_refused(method: str) -> None:
    outcome = await _run(_scope(method, origins=(_FOREIGN_ORIGIN,)))
    _assert_refused(outcome)


@pytest.mark.parametrize("method", ["patch", "post", "get", "Get", "PROPFIND", "TRACE", "CONNECT"])
async def test_a_method_outside_the_exact_case_safe_set_is_judged_like_a_write(
    method: str,
) -> None:
    outcome = await _run(_scope(method, origins=(_FOREIGN_ORIGIN,)))
    _assert_refused(outcome)


_BAD_ORIGINS: dict[str, tuple[str, ...]] = {
    "null": ("null",),
    "empty": ("",),
    "duplicate_same": (_LOOPBACK_ORIGIN, _LOOPBACK_ORIGIN),
    "duplicate_mixed": (_LOOPBACK_ORIGIN, _FOREIGN_ORIGIN),
    "malformed": ("http://[bad",),
    "no_scheme": ("localhost",),
    "with_path": ("http://localhost/some/path",),
    "non_http_scheme": ("ftp://localhost",),
    "port_out_of_range": ("http://localhost:99999",),
    "lookalike": ("http://localhost.evil.test",),
    "unreachable_zero": ("http://0.0.0.0",),  # noqa: S104
}


@pytest.mark.parametrize("origins", _BAD_ORIGINS.values(), ids=_BAD_ORIGINS.keys())
async def test_a_write_with_an_unusable_origin_is_refused(origins: tuple[str, ...]) -> None:
    outcome = await _run(_scope("POST", origins=origins))
    _assert_refused(outcome)


async def test_a_write_with_a_trailing_dot_loopback_origin_is_refused() -> None:
    outcome = await _run(_scope("POST", origins=("http://localhost.:8585",)))
    _assert_refused(outcome)


async def test_a_write_with_a_userinfo_origin_is_refused() -> None:
    outcome = await _run(_scope("POST", origins=("http://localhost@evil.example",)))
    _assert_refused(outcome)


async def test_a_write_with_an_uppercase_loopback_origin_is_served() -> None:
    outcome = await _run(_scope("POST", origins=("HTTP://LOCALHOST",)))
    _assert_served(outcome)


async def test_a_write_with_a_bracketed_ipv6_loopback_origin_is_served() -> None:
    outcome = await _run(_scope("POST", origins=("http://[::1]:8585",)))
    _assert_served(outcome)


@pytest.mark.parametrize(
    "origin",
    [
        "http://localhost",
        "http://localhost:3000",
        "http://localhost:1",
        "http://localhost:65535",
        "http://127.0.0.1:5173",
        "http://127.0.0.1:8585",
        "http://[::1]:1",
    ],
)
@pytest.mark.parametrize("method", _WRITE_METHODS)
async def test_a_write_with_a_loopback_origin_on_any_port_is_served(
    method: str, origin: str
) -> None:
    outcome = await _run(_scope(method, origins=(origin,)))
    _assert_served(outcome)


@pytest.mark.parametrize("method", _WRITE_METHODS)
async def test_a_write_with_the_configured_prep_chat_origin_is_served(
    monkeypatch: pytest.MonkeyPatch, method: str
) -> None:
    with_prep_chat_origin(monkeypatch, _NEUTRAL_ORIGIN)
    outcome = await _run(
        _scope(
            method,
            origins=("https://prep.example.test:9000",),
            host="prep.example.test:9000",
        )
    )
    _assert_served(outcome)


async def test_a_write_with_the_prep_chat_origin_is_refused_once_it_is_not_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with_prep_chat_origin(monkeypatch, None)
    outcome = await _run(_scope("POST", origins=(_NEUTRAL_ORIGIN,)))
    _assert_refused(outcome)


async def test_a_foreign_origin_decides_over_a_same_origin_fetch_site() -> None:
    outcome = await _run(
        _scope("POST", origins=(_FOREIGN_ORIGIN,), fetch_sites=("same-origin",))
    )
    _assert_refused(outcome)


async def test_a_loopback_origin_decides_over_a_cross_site_fetch_site() -> None:
    outcome = await _run(
        _scope("POST", origins=(_LOOPBACK_ORIGIN,), fetch_sites=("cross-site",))
    )
    _assert_served(outcome)


async def test_a_loopback_origin_on_another_port_with_a_same_site_fetch_site_is_served() -> None:
    outcome = await _run(
        _scope("POST", origins=("http://localhost:3000",), fetch_sites=("same-site",))
    )
    _assert_served(outcome)


@pytest.mark.parametrize("site", ["cross-site", "same-site", "garbage", "Same-Origin", ""])
@pytest.mark.parametrize("method", _WRITE_METHODS)
async def test_a_write_without_an_origin_and_a_non_same_origin_fetch_site_is_refused(
    method: str, site: str
) -> None:
    outcome = await _run(_scope(method, fetch_sites=(site,)))
    _assert_refused(outcome)


@pytest.mark.parametrize(
    "sites",
    [("same-origin", "same-origin"), ("same-origin", "cross-site"), ("none", "same-origin")],
    ids=["same_twice", "same_then_cross", "none_then_same"],
)
async def test_a_write_without_an_origin_and_a_duplicate_fetch_site_is_refused(
    sites: tuple[str, ...],
) -> None:
    outcome = await _run(_scope("POST", fetch_sites=sites))
    _assert_refused(outcome)


@pytest.mark.parametrize("site", ["same-origin", "none"])
@pytest.mark.parametrize("method", _WRITE_METHODS)
async def test_a_write_without_an_origin_and_a_single_same_origin_fetch_site_is_served(
    method: str, site: str
) -> None:
    outcome = await _run(_scope(method, fetch_sites=(site,)))
    _assert_served(outcome)


@pytest.mark.parametrize("method", _WRITE_METHODS)
async def test_a_write_with_neither_origin_nor_fetch_site_is_served(method: str) -> None:
    outcome = await _run(_scope(method))
    _assert_served(outcome)


_SAFE_METHOD_HEADERS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "foreign_origin": ((_FOREIGN_ORIGIN,), ()),
    "null_origin": (("null",), ()),
    "duplicate_origin": ((_LOOPBACK_ORIGIN, _LOOPBACK_ORIGIN), ()),
    "cross_site_fetch": ((), ("cross-site",)),
    "foreign_origin_and_cross_site_fetch": ((_FOREIGN_ORIGIN,), ("cross-site",)),
}


@pytest.mark.parametrize(
    "origins, fetch_sites", _SAFE_METHOD_HEADERS.values(), ids=_SAFE_METHOD_HEADERS.keys()
)
@pytest.mark.parametrize("method", _SAFE_METHODS)
async def test_a_safe_method_is_served_whatever_its_origin_and_fetch_site(
    method: str, origins: tuple[str, ...], fetch_sites: tuple[str, ...]
) -> None:
    outcome = await _run(_scope(method, origins=origins, fetch_sites=fetch_sites))
    _assert_served(outcome)


async def test_an_http_scope_without_a_method_raises_a_key_error() -> None:
    app = _RecordingApp()
    scope: Scope = {"type": "http", "headers": [(b"host", b"localhost")]}
    with pytest.raises(KeyError):
        await _drive(server.HostAllowlistGuard(app), scope)
    assert app.call_count == 0


async def test_a_foreign_host_is_refused_as_a_host_before_the_write_rule() -> None:
    outcome = await _run(
        _scope("POST", origins=(_FOREIGN_ORIGIN,), host="rebind.example.test")
    )
    assert outcome.status == 400
    assert json.loads(outcome.body) == {"detail": "host not allowed"}
    assert outcome.app_calls == 0


@pytest.fixture
def projects_client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[TestClient]:
    monkeypatch.setenv("ALAN_PROJECTS_ROOT", str(tmp_path / "projects"))
    with TestClient(server.served_app, base_url=_LOOPBACK_BASE_URL) as client:
        yield client


def test_served_app_refuses_a_foreign_origin_write_and_leaves_state_unchanged(
    projects_client: TestClient,
) -> None:
    refused = projects_client.post(
        _PROJECTS_PATH, json={"title": "Smuggled"}, headers={"Origin": _FOREIGN_ORIGIN}
    )
    listing = projects_client.get(_PROJECTS_PATH)
    assert refused.status_code == 403
    assert refused.json() == _FIXED_ORIGIN_BODY
    assert listing.status_code == 200
    assert listing.json() == []


def test_served_app_refuses_a_cross_site_fetch_write_and_leaves_state_unchanged(
    projects_client: TestClient,
) -> None:
    refused = projects_client.post(
        _PROJECTS_PATH, json={"title": "Smuggled"}, headers={"Sec-Fetch-Site": "cross-site"}
    )
    assert refused.status_code == 403
    assert refused.json() == _FIXED_ORIGIN_BODY
    assert projects_client.get(_PROJECTS_PATH).json() == []


def test_served_app_lets_a_write_without_browser_headers_reach_the_route(
    projects_client: TestClient,
) -> None:
    reached = projects_client.post(_PROJECTS_PATH, json={})
    assert reached.status_code == 422
    assert projects_client.get(_PROJECTS_PATH).json() == []


def test_served_app_lets_a_same_origin_browser_write_reach_the_route(
    projects_client: TestClient,
) -> None:
    reached = projects_client.post(
        _PROJECTS_PATH,
        json={},
        headers={"Origin": "http://127.0.0.1:8585", "Sec-Fetch-Site": "same-origin"},
    )
    assert reached.status_code == 422


def test_served_app_still_serves_a_foreign_origin_read(projects_client: TestClient) -> None:
    res = projects_client.get(_PROJECTS_PATH, headers={"Origin": _FOREIGN_ORIGIN})
    assert res.status_code == 200
