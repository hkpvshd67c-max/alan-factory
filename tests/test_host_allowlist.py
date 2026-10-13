from __future__ import annotations

import pytest

from alan import host_allowlist
from tests.prep_chat_link_support import with_prep_chat_origin

_NEUTRAL_ORIGIN = "https://Prep.Example.Test:8443"
_NEUTRAL_HOST = "prep.example.test"


def test_loopback_hostnames_are_exactly_the_three_loopback_names() -> None:
    assert host_allowlist.LOOPBACK_HOSTNAMES == frozenset({"localhost", "127.0.0.1", "::1"})


def test_configured_origin_is_none_under_the_suite_profile() -> None:
    assert host_allowlist.configured_prep_chat_origin() is None


def test_configured_origin_reads_the_resolved_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    with_prep_chat_origin(monkeypatch, _NEUTRAL_ORIGIN)
    assert host_allowlist.configured_prep_chat_origin() == _NEUTRAL_ORIGIN


def test_without_an_origin_only_loopback_hostnames_are_allowed() -> None:
    assert host_allowlist.allowed_hostnames() == host_allowlist.LOOPBACK_HOSTNAMES


def test_a_configured_origin_adds_its_lowercased_hostname(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with_prep_chat_origin(monkeypatch, _NEUTRAL_ORIGIN)
    assert host_allowlist.allowed_hostnames() == host_allowlist.LOOPBACK_HOSTNAMES | {
        _NEUTRAL_HOST
    }


@pytest.mark.parametrize(
    "origin", [_NEUTRAL_HOST, "https://[bad", "https://"], ids=["no_scheme", "malformed", "empty"]
)
def test_an_unusable_origin_adds_nothing(monkeypatch: pytest.MonkeyPatch, origin: str) -> None:
    with_prep_chat_origin(monkeypatch, origin)
    assert host_allowlist.allowed_hostnames() == host_allowlist.LOOPBACK_HOSTNAMES


def test_the_allowlist_is_resolved_on_every_call(monkeypatch: pytest.MonkeyPatch) -> None:
    with_prep_chat_origin(monkeypatch, "https://first.example.test")
    first = host_allowlist.allowed_hostnames()
    with_prep_chat_origin(monkeypatch, "https://second.example.test")
    second = host_allowlist.allowed_hostnames()
    assert "first.example.test" in first
    assert "first.example.test" not in second
    assert "second.example.test" in second
