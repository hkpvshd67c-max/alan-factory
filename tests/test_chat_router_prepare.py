from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

import alan.prep_chat as prep_chat_mod
from alan import _chat_core
from alan.build_chat import CONFIG as TEST_CHAT_CONFIG
from alan.build_chat import BuildChatSession
from alan.claude_cli import ChatTurnResult
from alan.dispatcher import PubSub
from alan.routes import _chat_router
from alan.teams._base import TeamEvent
from tests.prep_chat_link_support import (
    LOCAL_BASE,
    World,
    archive_session,
    build_session,
    conversation,
    make_world,
)

_READS = [_chat_core.PrepChatRead(status="read", title="Earlier work")]


class _TestChatLocked(_chat_core.ChatCoreError):
    pass


class _Turn:
    def __init__(self, ctx: _chat_router.ChatRouterCtx[BuildChatSession], pubsub: PubSub) -> None:
        self.done = threading.Event()
        self.events: list[TeamEvent] = []
        self.calls: list[dict[str, Any]] = []
        self.in_flight_user_text: list[str | None] = []
        self.ctx = ctx
        self.pubsub = pubsub

    def watch(self, session_id: str) -> None:
        def on_event(event: TeamEvent) -> None:
            self.events.append(event)
            if event.kind in {"assistant_done", "error"}:
                self.done.set()

        self.pubsub.subscribe(_chat_router.channel(self.ctx, session_id), on_event)


def _locks() -> _chat_core.ChatSessionLocks:
    return _chat_core.ChatSessionLocks(_TestChatLocked, "test-chat")


def _ctx() -> _chat_router.ChatRouterCtx[BuildChatSession]:
    return _chat_router.ChatRouterCtx(
        core=TEST_CHAT_CONFIG,
        channel_prefix="test-chat-router",
        label="test-chat",
        summarize_sentinel="",
        system_prompt=lambda _session: "sys",
    )


def _stub_cli(monkeypatch: pytest.MonkeyPatch, turn: _Turn, root: Path, session_id: str) -> None:
    def fake(**kwargs: Any) -> ChatTurnResult:
        turn.calls.append(kwargs)
        in_flight = _chat_core.load_session(TEST_CHAT_CONFIG, session_id, root=root).in_flight_turn
        turn.in_flight_user_text.append(None if in_flight is None else in_flight.user_text)
        return ChatTurnResult(result_text="done", claude_session_id=kwargs["session_id"])

    monkeypatch.setattr(_chat_router, "run_chat_turn", fake)


@pytest.fixture
def world(tmp_path: Path) -> Iterator[World]:
    built = make_world(tmp_path)
    yield built
    built.queue.close()


async def _prepared(session: _chat_core.ChatSession, content: str) -> _chat_router.PreparedTurn:
    return _chat_router.PreparedTurn(user_text=f"{content}\n\nATTACHED", prep_chat_reads=_READS)


async def test_the_cli_gets_the_prepared_text_while_everything_stored_keeps_the_typed_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = _ctx()
    session = _chat_core.create_session(TEST_CHAT_CONFIG, root=tmp_path)
    turn = _Turn(ctx, PubSub())
    turn.watch(session.session_id)
    _stub_cli(monkeypatch, turn, tmp_path, session.session_id)

    ack = await _chat_router.start_message_turn(
        ctx, session.session_id, "typed text", tmp_path, turn.pubsub, _locks(), _prepared
    )

    assert turn.done.wait(5)
    assert ack.prep_chat_reads == _READS
    assert turn.calls[0]["user_text"] == "typed text\n\nATTACHED"
    assert turn.in_flight_user_text == ["typed text"]
    stored = _chat_core.load_session(TEST_CHAT_CONFIG, session.session_id, root=tmp_path)
    assert stored.transcript[0].content == "typed text"
    assert stored.transcript[0].prep_chat_reads == _READS
    assert not any("ATTACHED" in json.dumps(event.payload) for event in turn.events)


async def test_without_a_prepare_seam_the_text_is_unchanged_and_nothing_is_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = _ctx()
    session = _chat_core.create_session(TEST_CHAT_CONFIG, root=tmp_path)
    turn = _Turn(ctx, PubSub())
    turn.watch(session.session_id)
    _stub_cli(monkeypatch, turn, tmp_path, session.session_id)

    ack = await _chat_router.start_message_turn(
        ctx, session.session_id, "plain text", tmp_path, turn.pubsub, _locks()
    )

    assert turn.done.wait(5)
    assert ack.prep_chat_reads == []
    assert ack.model_dump() == {"status": "accepted", "prep_chat_reads": []}
    assert turn.calls[0]["user_text"] == "plain text"
    stored = _chat_core.load_session(TEST_CHAT_CONFIG, session.session_id, root=tmp_path)
    assert stored.transcript[0].prep_chat_reads == []


async def test_a_failing_prepare_releases_the_lock_and_starts_no_turn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = _ctx()
    session = _chat_core.create_session(TEST_CHAT_CONFIG, root=tmp_path)
    turn = _Turn(ctx, PubSub())
    _stub_cli(monkeypatch, turn, tmp_path, session.session_id)
    locks = _locks()

    async def failing(_session: _chat_core.ChatSession, _content: str) -> _chat_router.PreparedTurn:
        raise RuntimeError("prepare failed")

    with pytest.raises(RuntimeError, match="prepare failed"):
        await _chat_router.start_message_turn(
            ctx, session.session_id, "typed text", tmp_path, turn.pubsub, locks, failing
        )

    assert turn.calls == []
    locks.release(locks.acquire(session.session_id))
    stored = _chat_core.load_session(TEST_CHAT_CONFIG, session.session_id, root=tmp_path)
    assert stored.transcript == []
    assert stored.in_flight_turn is None


def test_a_bare_ack_carries_no_reads() -> None:
    assert _chat_router.ChatAck(status="accepted").prep_chat_reads == []


async def test_the_link_preparer_attaches_the_block_to_the_cli_text_only(
    world: World,
) -> None:
    earlier = build_session(world, transcript=conversation("Earlier question", "Earlier answer"))
    ws = archive_session(world, prep_chat_mod.PrepChatKind.BUILD, earlier, "Earlier work")
    chat = _chat_core.create_session(TEST_CHAT_CONFIG, root=world.build_root)
    typed = f"please use {LOCAL_BASE}/workspaces/{ws.task_id}/prep-chat"

    prepared = await _chat_router.link_preparer(world.env())(chat, typed)

    assert prepared.user_text.startswith(f"{typed}\n\n<preparation_chat ")
    assert "Earlier answer" in prepared.user_text
    assert prepared.prep_chat_reads == _READS


async def test_the_link_preparer_leaves_a_message_without_a_link_exactly_as_typed(
    world: World,
) -> None:
    chat = _chat_core.create_session(TEST_CHAT_CONFIG, root=world.build_root)

    prepared = await _chat_router.link_preparer(world.env())(chat, "no links here")

    assert prepared.user_text == "no links here"
    assert prepared.prep_chat_reads == []


async def test_the_link_preparer_scopes_by_the_chats_own_repository(
    world: World,
) -> None:
    other_repo = build_session(world, transcript=conversation("Other question"))
    ws = archive_session(world, prep_chat_mod.PrepChatKind.BUILD, other_repo, "Other work")
    chat = _chat_core.create_session(
        TEST_CHAT_CONFIG, root=world.build_root, engine_key="ghost/unknown"
    )

    prepared = await _chat_router.link_preparer(world.env())(
        chat, f"{LOCAL_BASE}/workspaces/{ws.task_id}"
    )

    assert [read.status for read in prepared.prep_chat_reads] == ["other_repository"]
    assert "Other question" not in prepared.user_text
    assert "Other work" not in prepared.user_text


async def test_a_link_to_the_chat_itself_is_not_attached(world: World) -> None:
    chat = _chat_core.create_session(TEST_CHAT_CONFIG, root=world.build_root)

    prepared = await _chat_router.link_preparer(world.env())(
        chat, f"{LOCAL_BASE}/build?s={chat.session_id}"
    )

    assert prepared.prep_chat_reads == []
    assert prepared.user_text == f"{LOCAL_BASE}/build?s={chat.session_id}"
