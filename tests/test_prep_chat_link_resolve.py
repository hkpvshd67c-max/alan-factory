from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from pathlib import Path

import pytest

import alan.prep_chat as prep_chat_mod
import alan.workspace as workspace_mod
from alan import prep_chat_link
from alan.prep_chat_link import LinkEnv
from alan.workspace_index import WorkspaceIndex
from tests.prep_chat_link_support import (
    LOCAL_BASE,
    UNKNOWN_SESSION_ID,
    World,
    archive_session,
    build_session,
    conversation,
    explore_session,
    make_world,
    snapshot,
)

_BUILD = prep_chat_mod.PrepChatKind.BUILD
_EXPLORE = prep_chat_mod.PrepChatKind.EXPLORE
_CHAT_SESSION_ID = "00000000-0000-0000-0000-00000000c0de"


@pytest.fixture
def world(tmp_path: Path) -> Iterator[World]:
    built = make_world(tmp_path)
    yield built
    built.queue.close()


def _attach(
    world: World, text: str, *, own_session: str = _CHAT_SESSION_ID
) -> prep_chat_link.Attachment:
    return prep_chat_link.attach(text, world.env(), engine_key=None, session_id=own_session)


def _statuses(attachment: prep_chat_link.Attachment) -> list[str]:
    return [read.status for read in attachment.reads]


def test_the_workspace_page_link_reads_the_archived_preparation_chat(world: World) -> None:
    session_id = build_session(
        world, transcript=conversation("Build a login page", "Sure"), brief="Login brief"
    )
    ws = archive_session(world, _BUILD, session_id, "Login page")

    attachment = _attach(world, f"{LOCAL_BASE}/workspaces/{ws.task_id}")

    assert [(r.status, r.title) for r in attachment.reads] == [("read", "Login page")]
    (block,) = attachment.blocks
    assert "Login brief" in block
    assert "Build a login page" in block
    assert "Sure" in block


def test_every_link_form_resolves_to_the_same_conversation(world: World) -> None:
    session_id = build_session(world, transcript=conversation("question", "answer"), brief="B")
    ws = archive_session(world, _BUILD, session_id, "Shared work")
    links = [
        f"{LOCAL_BASE}/workspaces/{ws.task_id}",
        f"{LOCAL_BASE}/workspaces/{ws.task_id}/prep-chat",
        f"{LOCAL_BASE}/api/workspaces/{ws.task_id}",
        f"{LOCAL_BASE}/api/workspaces/{ws.task_id}/prep-chat",
        f"{LOCAL_BASE}/build?s={session_id}",
        f"{LOCAL_BASE}/repos/acme/widget/build?s={session_id}",
        f"{LOCAL_BASE}/api/build-chat/sessions/{session_id}",
    ]

    blocks = {_attach(world, link).blocks[0] for link in links}

    assert len(blocks) == 1
    assert "Shared work" in next(iter(blocks))


def test_an_explore_session_link_reads_an_archived_explore_conversation(world: World) -> None:
    session_id = explore_session(world, transcript=conversation("Is it viable?", "Yes"))
    archive_session(world, _EXPLORE, session_id, "Viability")

    page = _attach(world, f"{LOCAL_BASE}/explore/architecture?s={session_id}")
    api = _attach(world, f"{LOCAL_BASE}/api/explore-chat/sessions/{session_id}")

    assert _statuses(page) == _statuses(api) == ["read"]
    assert page.blocks == api.blocks
    assert "Is it viable?" in page.blocks[0]


def test_a_live_build_session_is_read_before_any_archive_lookup(world: World) -> None:
    session_id = build_session(world, transcript=conversation("Live question", "Live answer"))

    attachment = _attach(world, f"{LOCAL_BASE}/build?s={session_id}")

    assert _statuses(attachment) == ["read"]
    assert attachment.reads[0].title == "Live question"
    assert "Live answer" in attachment.blocks[0]


def test_a_live_explore_session_is_read(world: World) -> None:
    session_id = explore_session(world, transcript=conversation("Explore question"), brief="EB")

    attachment = _attach(world, f"{LOCAL_BASE}/api/explore-chat/sessions/{session_id}")

    assert _statuses(attachment) == ["read"]
    assert "EB" in attachment.blocks[0]


def test_a_live_session_without_a_user_message_has_no_title(world: World) -> None:
    session_id = build_session(world)

    attachment = _attach(world, f"{LOCAL_BASE}/build?s={session_id}")

    assert attachment.reads[0].status == "read"
    assert attachment.reads[0].title is None
    assert "title=" not in attachment.blocks[0]


def test_a_live_session_title_is_the_first_line_of_the_first_user_message(world: World) -> None:
    long_first_line = "x" * 300
    session_id = build_session(
        world, transcript=conversation(f"\n\n  {long_first_line}\nsecond line", "reply")
    )

    attachment = _attach(world, f"{LOCAL_BASE}/build?s={session_id}")

    assert attachment.reads[0].title == "x" * 120


def test_a_live_session_title_stops_at_the_end_of_the_first_line(world: World) -> None:
    session_id = build_session(world, transcript=conversation("Short title\nsecond line", "reply"))

    attachment = _attach(world, f"{LOCAL_BASE}/build?s={session_id}")

    assert attachment.reads[0].title == "Short title"


def test_an_archived_session_link_resolves_through_the_workspace_index(world: World) -> None:
    session_id = build_session(world, transcript=conversation("question", "answer"))
    archive_session(world, _BUILD, session_id, "Archived work")

    attachment = _attach(world, f"{LOCAL_BASE}/api/build-chat/sessions/{session_id}")

    assert [(r.status, r.title) for r in attachment.reads] == [("read", "Archived work")]
    assert "answer" in attachment.blocks[0]


def test_an_unknown_session_id_is_not_found(world: World) -> None:
    attachment = _attach(world, f"{LOCAL_BASE}/build?s={UNKNOWN_SESSION_ID}")

    assert [(r.status, r.title) for r in attachment.reads] == [("not_found", None)]
    assert 'status="not_found"' in attachment.blocks[0]


def test_an_unknown_workspace_is_not_found(world: World) -> None:
    attachment = _attach(world, f"{LOCAL_BASE}/workspaces/no-such-workspace/prep-chat")
    assert _statuses(attachment) == ["not_found"]


def test_a_workspace_with_no_archived_preparation_chat_is_not_found(world: World) -> None:
    ws = workspace_mod.create("Pre-archive work", "description", root=world.ws_root)

    attachment = _attach(world, f"{LOCAL_BASE}/workspaces/{ws.task_id}")

    assert _statuses(attachment) == ["not_found"]


@pytest.mark.parametrize(
    "link",
    [
        "/workspaces/..%2f..%2fetc/prep-chat",
        "/workspaces/has%20space",
        "/workspaces/.hidden",
        "/build?s=not-a-session-id",
        "/api/explore-chat/sessions/12345678",
        "/api/build-chat/sessions/%2e%2e",
    ],
)
def test_a_prep_shaped_link_with_an_invalid_identifier_is_not_found(
    world: World, link: str
) -> None:
    attachment = _attach(world, f"{LOCAL_BASE}{link}")
    assert _statuses(attachment) == ["not_found"]


def test_a_session_link_for_a_conversation_in_the_other_kind_is_not_found(
    world: World,
) -> None:
    session_id = explore_session(world, transcript=conversation("question"))

    attachment = _attach(world, f"{LOCAL_BASE}/api/build-chat/sessions/{session_id}")

    assert _statuses(attachment) == ["not_found"]


def test_a_corrupt_live_session_is_not_found(world: World) -> None:
    session_id = build_session(world)
    (world.build_root / session_id / "session.json").write_text("{broken", encoding="utf-8")

    attachment = _attach(world, f"{LOCAL_BASE}/build?s={session_id}")

    assert _statuses(attachment) == ["not_found"]


def test_an_archive_with_a_corrupt_marker_is_not_found(world: World) -> None:
    session_id = build_session(world)
    ws = archive_session(world, _BUILD, session_id)
    (ws.path / "prep-chat" / session_id / "prep_chat.json").write_text("{", encoding="utf-8")

    by_workspace = _attach(world, f"{LOCAL_BASE}/workspaces/{ws.task_id}")
    by_session = _attach(world, f"{LOCAL_BASE}/build?s={session_id}")

    assert _statuses(by_workspace) == _statuses(by_session) == ["not_found"]


def test_an_unexpected_failure_while_resolving_is_reported_as_not_found(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    def explode() -> WorkspaceIndex:
        raise RuntimeError("index unavailable")

    env = LinkEnv(
        build_root=world.build_root,
        explore_root=world.explore_root,
        workspaces_root=world.ws_root,
        index=explode,
    )

    with caplog.at_level(logging.WARNING, logger="alan.prep_chat_link"):
        attachment = prep_chat_link.attach(
            f"{LOCAL_BASE}/build?s={UNKNOWN_SESSION_ID}",
            env,
            engine_key=None,
            session_id="00000000-0000-0000-0000-000000000000",
        )

    assert _statuses(attachment) == ["not_found"]
    assert "resolving a build link failed" in caplog.text


def test_resolving_a_link_leaves_every_file_of_the_linked_conversation_unchanged(
    world: World,
) -> None:
    live_id = build_session(world, transcript=conversation("live question", "live answer"))
    archived_id = explore_session(world, transcript=conversation("old question", "old answer"))
    ws = archive_session(world, _EXPLORE, archived_id)
    for session_file in (
        world.build_root / live_id / "session.json",
        ws.path / "prep-chat" / archived_id / "session.json",
    ):
        raw = json.loads(session_file.read_text(encoding="utf-8"))
        raw.pop("engine_key")
        raw.pop("cli_session_id", None)
        session_file.write_text(json.dumps(raw), encoding="utf-8")
    before = (snapshot(world.build_root), snapshot(ws.path))

    attachment = _attach(
        world,
        f"{LOCAL_BASE}/build?s={live_id} {LOCAL_BASE}/workspaces/{ws.task_id} "
        f"{LOCAL_BASE}/api/explore-chat/sessions/{archived_id}",
    )

    assert _statuses(attachment) == ["read", "read", "read"]
    assert (snapshot(world.build_root), snapshot(ws.path)) == before


def test_a_link_to_the_chat_itself_produces_no_outcome(world: World) -> None:
    own = build_session(world)
    other = build_session(world, transcript=conversation("other question"))

    attachment = _attach(
        world,
        f"{LOCAL_BASE}/build?s={own} {LOCAL_BASE}/api/build-chat/sessions/{own} "
        f"{LOCAL_BASE}/build?s={other}",
        own_session=own,
    )

    assert _statuses(attachment) == ["read"]
    assert "other question" in attachment.blocks[0]


def test_only_the_first_three_links_get_an_outcome(world: World) -> None:
    sessions = [
        build_session(world, transcript=conversation(f"question {n}")) for n in range(5)
    ]
    text = " ".join(f"{LOCAL_BASE}/build?s={session_id}" for session_id in sessions)

    attachment = _attach(world, text)

    assert prep_chat_link.MAX_LINKS == 3
    assert len(attachment.reads) == len(attachment.blocks) == 3
    assert "question 2" in attachment.blocks[2]
    assert not any("question 3" in block for block in attachment.blocks)


def test_blocks_are_numbered_by_the_position_of_the_link(world: World) -> None:
    session_id = build_session(world, transcript=conversation("question"))

    attachment = _attach(
        world, f"{LOCAL_BASE}/build?s={UNKNOWN_SESSION_ID} {LOCAL_BASE}/build?s={session_id}"
    )

    assert attachment.blocks[0].startswith('<preparation_chat link="1" status="not_found">')
    assert attachment.blocks[1].startswith('<preparation_chat link="2" status="read"')


def test_other_urls_and_plain_text_produce_no_outcome(world: World) -> None:
    attachment = _attach(
        world, "see https://github.com/acme/widget/issues/3 and https://example.com/x"
    )

    assert attachment.reads == []
    assert attachment.blocks == []
