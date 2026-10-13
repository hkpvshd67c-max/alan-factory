from __future__ import annotations

import pytest

from alan import prep_chat_link
from alan.prep_chat_link import LinkRef
from tests.prep_chat_link_support import with_prep_chat_origin

_SESSION = "0a1b2c3d-1111-2222-3333-444455556666"
_OTHER_SESSION = "9f8e7d6c-1111-2222-3333-444455556666"
_BASES = [
    "http://localhost",
    "http://localhost:5173",
    "http://localhost:8585",
    "https://localhost",
    "http://127.0.0.1:8585",
    "http://127.0.0.1:4173",
    "http://[::1]:8585",
    "http://[::1]",
    "HTTP://LOCALHOST:5173",
]
_WORKSPACE_PATHS = [
    "/workspaces/fix-login",
    "/workspaces/fix-login/",
    "/workspaces/fix-login/prep-chat",
    "/api/workspaces/fix-login",
    "/api/workspaces/fix-login/prep-chat",
]
_BUILD_SESSION_PATHS = [
    f"/build?s={_SESSION}",
    f"/repos/acme/widget/build?s={_SESSION}",
    f"/repos/acme/widget/build/?s={_SESSION}",
    f"/api/build-chat/sessions/{_SESSION}",
]
_EXPLORE_SESSION_PATHS = [
    f"/explore/architecture?s={_SESSION}",
    f"/repos/acme/widget/explore/architecture?s={_SESSION}&engine=acme%2Fwidget",
    f"/api/explore-chat/sessions/{_SESSION}",
]


@pytest.mark.parametrize("base", _BASES)
@pytest.mark.parametrize("path", _WORKSPACE_PATHS)
def test_every_workspace_form_names_the_workspace_on_any_loopback_host_and_port(
    base: str, path: str
) -> None:
    assert prep_chat_link.extract_refs(f"read {base}{path} please") == [
        LinkRef("workspace", "fix-login")
    ]


@pytest.mark.parametrize("base", _BASES)
@pytest.mark.parametrize("path", _BUILD_SESSION_PATHS)
def test_every_build_session_form_names_the_session(base: str, path: str) -> None:
    assert prep_chat_link.extract_refs(f"{base}{path}") == [LinkRef("build", _SESSION)]


@pytest.mark.parametrize("base", _BASES)
@pytest.mark.parametrize("path", _EXPLORE_SESSION_PATHS)
def test_every_explore_session_form_names_the_session(base: str, path: str) -> None:
    assert prep_chat_link.extract_refs(f"{base}{path}") == [LinkRef("explore", _SESSION)]


def test_the_configured_origin_host_is_recognised_on_any_port(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with_prep_chat_origin(monkeypatch, "https://prep.example.test")
    text = (
        "https://prep.example.test/workspaces/a-ws/prep-chat "
        f"https://prep.example.test:9443/build?s={_SESSION}"
    )
    assert prep_chat_link.extract_refs(text) == [
        LinkRef("workspace", "a-ws"),
        LinkRef("build", _SESSION),
    ]


def test_the_configured_origin_host_is_not_recognised_once_unconfigured() -> None:
    assert prep_chat_link.extract_refs("https://prep.example.test/workspaces/a-ws/prep-chat") == []


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/acme/widget/issues/12",
        "https://github.com/acme/widget/pull/34",
        "https://example.com/workspaces/fix-login/prep-chat",
        "http://localhost.evil.test/workspaces/fix-login",
        "http://evil.localhost/workspaces/fix-login",
        "http://user@evil.test/workspaces/fix-login",
        "http://192.168.1.5:8585/workspaces/fix-login",
        "http://0.0.0.0:8585/workspaces/fix-login",
        "ftp://localhost/workspaces/fix-login",
    ],
)
def test_other_hosts_and_other_urls_produce_no_reference(url: str) -> None:
    assert prep_chat_link.extract_refs(url) == []


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:5173/",
        "http://localhost:5173/health",
        "http://localhost:5173/build",
        "http://localhost:5173/build?s=",
        "http://localhost:5173/build?other=1",
        "http://localhost:5173/explore/architecture",
        "http://localhost:5173/workspaces",
        "http://localhost:5173/workspaces/fix-login/invocations/inv-1",
        "http://localhost:5173/workspaces/fix-login/prep-chat/extra",
        f"http://localhost:8585/api/build-chat/sessions/{_SESSION}/brief",
        f"http://localhost:8585/api/build-chat/sessions/{_SESSION}/events",
        f"http://localhost:8585/api/explore-chat/sessions/{_SESSION}/messages",
        "http://localhost:8585/api/workspaces",
        "http://localhost:8585/api/engines",
        f"http://localhost:5173/projects/{_SESSION}?s={_SESSION}",
        f"http://localhost:5173/build/extra?s={_SESSION}",
        f"http://localhost:5173/explore?s={_SESSION}",
    ],
)
def test_loopback_urls_that_are_not_preparation_chat_links_produce_no_reference(
    url: str,
) -> None:
    assert prep_chat_link.extract_refs(url) == []


def test_text_without_any_link_produces_no_reference() -> None:
    assert prep_chat_link.extract_refs("nothing to see here, localhost:5173 or 127.0.0.1") == []


@pytest.mark.parametrize(
    "text",
    [
        "see (http://localhost:5173/workspaces/fix-login/prep-chat).",
        "see [the chat](http://localhost:5173/workspaces/fix-login/prep-chat)",
        "see <http://localhost:5173/workspaces/fix-login/prep-chat>",
        "see http://localhost:5173/workspaces/fix-login/prep-chat.",
        "see http://localhost:5173/workspaces/fix-login/prep-chat, then",
        'see "http://localhost:5173/workspaces/fix-login/prep-chat"',
        "see `http://localhost:5173/workspaces/fix-login/prep-chat`!",
        "see http://localhost:5173/workspaces/fix-login/prep-chat?",
        "see http://localhost:5173/workspaces/fix-login/prep-chat\nand more",
    ],
)
def test_surrounding_punctuation_is_not_part_of_the_link(text: str) -> None:
    assert prep_chat_link.extract_refs(text) == [LinkRef("workspace", "fix-login")]


def test_a_repeated_link_is_one_reference_in_order_of_first_appearance() -> None:
    text = (
        "http://localhost:5173/workspaces/b-ws "
        f"http://localhost:5173/build?s={_SESSION} "
        "http://localhost:5173/workspaces/b-ws/prep-chat "
        "http://localhost:5173/workspaces/b-ws"
    )
    assert prep_chat_link.extract_refs(text) == [
        LinkRef("workspace", "b-ws"),
        LinkRef("build", _SESSION),
    ]


def test_references_are_returned_for_every_link_in_the_message() -> None:
    links = [f"http://localhost:5173/workspaces/ws-{n}" for n in range(6)]
    assert prep_chat_link.extract_refs(" ".join(links)) == [
        LinkRef("workspace", f"ws-{n}") for n in range(6)
    ]


def test_an_invalid_ipv6_host_is_skipped_rather_than_raised() -> None:
    assert prep_chat_link.extract_refs("http://[1:2:3:4:5:6:7:8:9]/workspaces/fix-login") == []


def test_a_prep_shaped_path_keeps_an_invalid_identifier_for_resolution_to_reject() -> None:
    text = (
        "http://localhost:5173/workspaces/..%2f..%2fetc/prep-chat "
        "http://localhost:5173/build?s=not-a-session-id"
    )
    assert prep_chat_link.extract_refs(text) == [
        LinkRef("workspace", "..%2f..%2fetc"),
        LinkRef("build", "not-a-session-id"),
    ]


def test_the_first_value_of_a_repeated_session_parameter_wins() -> None:
    url = f"http://localhost:5173/build?s={_SESSION}&s={_OTHER_SESSION}"
    assert prep_chat_link.extract_refs(url) == [LinkRef("build", _SESSION)]
