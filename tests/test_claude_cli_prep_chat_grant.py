from __future__ import annotations

import pytest

from alan.claude_cli import (
    CONSULTANTS,
    format_build_system_prompt,
    format_explore_system_prompt,
)
from alan.claude_cli_grants import (
    BUILD_NO_IMPL,
    BUILD_REPO_BINDING,
    EXPLORE_NO_IMPL,
    PREP_CHAT_ATTACHMENT_CLAUSE,
)
from tests.prep_chat_link_support import with_prep_chat_origin

_NEUTRAL_ORIGIN = "https://prep.example.test"
_SESSION_FETCH_PATHS = ("/api/build-chat/sessions/", "/api/explore-chat/sessions/")


def test_build_prompt_carries_the_attachment_clause() -> None:
    assert PREP_CHAT_ATTACHMENT_CLAUSE in format_build_system_prompt()


def test_explore_prompt_carries_the_attachment_clause_for_every_consultant() -> None:
    for consultant in CONSULTANTS:
        assert PREP_CHAT_ATTACHMENT_CLAUSE in format_explore_system_prompt(consultant.key)


def test_both_postures_end_with_the_attachment_clause() -> None:
    assert BUILD_NO_IMPL.endswith(PREP_CHAT_ATTACHMENT_CLAUSE)
    assert EXPLORE_NO_IMPL.endswith(PREP_CHAT_ATTACHMENT_CLAUSE)


def test_build_prompt_embeds_the_posture_block() -> None:
    assert BUILD_NO_IMPL in format_build_system_prompt(repo_label="acme/widget")


def test_explore_prompt_embeds_the_posture_block_for_every_consultant() -> None:
    for consultant in CONSULTANTS:
        assert EXPLORE_NO_IMPL in format_explore_system_prompt(consultant.key)


def test_the_build_prompt_is_the_same_whatever_origin_is_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    unconfigured = format_build_system_prompt(repo_label="acme/widget")
    with_prep_chat_origin(monkeypatch, _NEUTRAL_ORIGIN)
    assert format_build_system_prompt(repo_label="acme/widget") == unconfigured


def test_the_explore_prompt_is_the_same_whatever_origin_is_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    unconfigured = format_explore_system_prompt("architecture")
    with_prep_chat_origin(monkeypatch, _NEUTRAL_ORIGIN)
    assert format_explore_system_prompt("architecture") == unconfigured


@pytest.mark.parametrize("prompt", [BUILD_NO_IMPL, EXPLORE_NO_IMPL], ids=["build", "explore"])
def test_no_posture_grants_a_session_fetch(prompt: str) -> None:
    assert not any(path in prompt for path in _SESSION_FETCH_PATHS)
    assert "{prep_chat_origin}" not in prompt


@pytest.mark.parametrize(
    "fragment",
    [
        "<preparation_chat>",
        'status="read"',
        'status="other_repository"',
        'status="not_found"',
        "truncated: K of N earlier messages omitted",
        "plainly in one line",
        "different repository",
    ],
)
def test_the_attachment_clause_explains_every_outcome(fragment: str) -> None:
    assert fragment in PREP_CHAT_ATTACHMENT_CLAUSE


@pytest.mark.parametrize(
    "fragment",
    [
        "Never fetch such a link with WebFetch",
        "never call localhost or any loopback address",
        "never read alan's state directory or any file on disk",
    ],
)
def test_the_attachment_clause_forbids_every_other_read_path(fragment: str) -> None:
    assert fragment in PREP_CHAT_ATTACHMENT_CLAUSE


def test_the_attachment_clause_is_static_text() -> None:
    assert "{" not in PREP_CHAT_ATTACHMENT_CLAUSE
    assert "}" not in PREP_CHAT_ATTACHMENT_CLAUSE


def test_build_repo_binding_carves_out_prep_chat_reads() -> None:
    rendered = BUILD_REPO_BINDING.format(repo_label="acme/widget")
    assert "not a violation of this binding" in rendered
    assert "preparation conversation" in rendered


def test_build_repo_binding_carve_out_survives_non_alan_repo_label() -> None:
    prompt = format_build_system_prompt(repo_label="acme/widget")
    assert BUILD_REPO_BINDING.format(repo_label="acme/widget") in prompt
