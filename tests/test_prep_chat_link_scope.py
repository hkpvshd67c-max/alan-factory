from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest

import alan.prep_chat as prep_chat_mod
from alan import engine_config, prep_chat_link
from tests.conftest import register_test_engine
from tests.prep_chat_link_support import (
    LOCAL_BASE,
    UNKNOWN_SESSION_ID,
    World,
    archive_session,
    build_session,
    conversation,
    explore_session,
    make_world,
)

_BUILD = prep_chat_mod.PrepChatKind.BUILD
_EXPLORE = prep_chat_mod.PrepChatKind.EXPLORE
_OWN_SLUG = engine_config.default_engine_config().repo.slug
_WITHHELD_TITLE = "Payroll migration"
_WITHHELD_BRIEF = "Brief with payroll internals"
_WITHHELD_LINE = "Our payroll schema has a ledger table"


@pytest.fixture
def world(tmp_path: Path) -> Iterator[World]:
    built = make_world(tmp_path)
    register_test_engine("acme/widget")
    yield built
    built.queue.close()


def _attach(
    world: World, link: str, *, engine_key: str | None = None
) -> prep_chat_link.Attachment:
    return prep_chat_link.attach(
        link, world.env(), engine_key=engine_key, session_id=UNKNOWN_SESSION_ID
    )


def _private_session(world: World, *, engine_key: str | None) -> str:
    return build_session(
        world,
        engine_key=engine_key,
        transcript=conversation(_WITHHELD_LINE, "Noted"),
        brief=_WITHHELD_BRIEF,
    )


def _assert_withheld(attachment: prep_chat_link.Attachment, status: str) -> None:
    assert [(read.status, read.title) for read in attachment.reads] == [(status, None)]
    (block,) = attachment.blocks
    for secret in (_WITHHELD_TITLE, _WITHHELD_BRIEF, _WITHHELD_LINE, "Noted", UNKNOWN_SESSION_ID):
        assert secret not in block
    assert "localhost" not in block
    assert "http" not in block


def test_a_conversation_bound_to_another_registered_repository_is_withheld(
    world: World,
) -> None:
    session_id = _private_session(world, engine_key="acme/widget")
    ws = archive_session(world, _BUILD, session_id, _WITHHELD_TITLE)

    by_workspace = _attach(world, f"{LOCAL_BASE}/workspaces/{ws.task_id}/prep-chat")
    by_session = _attach(world, f"{LOCAL_BASE}/api/build-chat/sessions/{session_id}")

    _assert_withheld(by_workspace, "other_repository")
    _assert_withheld(by_session, "other_repository")
    assert 'status="other_repository"' in by_workspace.blocks[0]


def test_a_live_conversation_bound_to_another_repository_is_withheld(world: World) -> None:
    session_id = _private_session(world, engine_key="acme/widget")

    attachment = _attach(world, f"{LOCAL_BASE}/build?s={session_id}")

    _assert_withheld(attachment, "other_repository")


def test_a_conversation_bound_to_an_unknown_repository_is_withheld(world: World) -> None:
    session_id = _private_session(world, engine_key="ghost/unknown")

    attachment = _attach(world, f"{LOCAL_BASE}/build?s={session_id}")

    _assert_withheld(attachment, "other_repository")


def test_a_chat_bound_to_an_unknown_repository_reads_nothing(world: World) -> None:
    session_id = _private_session(world, engine_key=None)

    attachment = _attach(world, f"{LOCAL_BASE}/build?s={session_id}", engine_key="ghost/unknown")

    _assert_withheld(attachment, "other_repository")


def test_a_chat_bound_to_a_registered_repository_does_not_read_alans_own_conversation(
    world: World,
) -> None:
    session_id = _private_session(world, engine_key=None)

    attachment = _attach(world, f"{LOCAL_BASE}/build?s={session_id}", engine_key="acme/widget")

    _assert_withheld(attachment, "other_repository")


def test_a_chat_bound_to_a_registered_repository_reads_that_repositorys_conversation(
    world: World,
) -> None:
    session_id = _private_session(world, engine_key="acme/widget")

    attachment = _attach(world, f"{LOCAL_BASE}/build?s={session_id}", engine_key="acme/widget")

    assert [read.status for read in attachment.reads] == ["read"]
    assert _WITHHELD_LINE in attachment.blocks[0]


def test_an_unbound_chat_reads_a_conversation_bound_to_alans_own_slug(world: World) -> None:
    session_id = _private_session(world, engine_key=_OWN_SLUG)

    attachment = _attach(world, f"{LOCAL_BASE}/build?s={session_id}", engine_key=None)

    assert [read.status for read in attachment.reads] == ["read"]


def test_a_chat_bound_to_alans_own_slug_reads_an_unbound_conversation(world: World) -> None:
    session_id = _private_session(world, engine_key=None)

    attachment = _attach(world, f"{LOCAL_BASE}/build?s={session_id}", engine_key=_OWN_SLUG)

    assert [read.status for read in attachment.reads] == ["read"]


def test_a_legacy_conversation_without_an_engine_key_is_treated_as_alans_own(
    world: World,
) -> None:
    session_id = _private_session(world, engine_key=None)
    session_file = world.build_root / session_id / "session.json"
    raw = json.loads(session_file.read_text(encoding="utf-8"))
    del raw["engine_key"]
    session_file.write_text(json.dumps(raw), encoding="utf-8")

    attachment = _attach(world, f"{LOCAL_BASE}/build?s={session_id}", engine_key=None)

    assert [read.status for read in attachment.reads] == ["read"]


def test_scope_is_enforced_for_explore_conversations_too(world: World) -> None:
    session_id = explore_session(
        world,
        engine_key="acme/widget",
        transcript=conversation(_WITHHELD_LINE),
        brief=_WITHHELD_BRIEF,
    )
    ws = archive_session(world, _EXPLORE, session_id, _WITHHELD_TITLE)

    live_withheld = _attach(world, f"{LOCAL_BASE}/api/explore-chat/sessions/{session_id}")
    archived_withheld = _attach(world, f"{LOCAL_BASE}/workspaces/{ws.task_id}")

    _assert_withheld(live_withheld, "other_repository")
    _assert_withheld(archived_withheld, "other_repository")


def test_each_link_in_a_message_is_scoped_on_its_own(world: World) -> None:
    allowed = build_session(world, transcript=conversation("allowed question"))
    withheld = _private_session(world, engine_key="acme/widget")

    attachment = _attach(
        world, f"{LOCAL_BASE}/build?s={withheld} {LOCAL_BASE}/build?s={allowed}"
    )

    assert [read.status for read in attachment.reads] == ["other_repository", "read"]
    assert "allowed question" in attachment.blocks[1]
    assert _WITHHELD_LINE not in "".join(attachment.blocks)
