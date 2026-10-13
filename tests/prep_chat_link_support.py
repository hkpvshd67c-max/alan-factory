from __future__ import annotations

import threading
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

import alan.build_chat as build_chat_mod
import alan.explore_chat as explore_chat_mod
import alan.prep_chat as prep_chat_mod
import alan.workspace as workspace_mod
from alan import _chat_core, default_profile, server
from alan.claude_cli import ChatTurnResult
from alan.default_profile import Resolved, Source
from alan.dispatcher import PubSub
from alan.prep_chat_link import LinkEnv
from alan.queue import Queue
from alan.teams._base import TeamEvent
from alan.workspace_index import WorkspaceIndex

UNKNOWN_SESSION_ID = "12345678-1234-1234-1234-1234567890ab"
LOCAL_BASE = "http://localhost:5173"


def with_prep_chat_origin(monkeypatch: pytest.MonkeyPatch, origin: str | None) -> None:
    profile = default_profile.resolved().profile
    resolved = Resolved(profile.model_copy(update={"prep_chat_origin": origin}), Source.profile)
    monkeypatch.setattr(default_profile, "resolved", lambda: resolved)


@dataclass
class World:
    build_root: Path
    explore_root: Path
    ws_root: Path
    queue: Queue
    index: WorkspaceIndex

    def env(self) -> LinkEnv:
        return LinkEnv(
            build_root=self.build_root,
            explore_root=self.explore_root,
            workspaces_root=self.ws_root,
            index=lambda: self.index,
        )


def make_world(tmp_path: Path) -> World:
    ws_root = tmp_path / "ws"
    queue = Queue(db_path=tmp_path / "queue.db")
    return World(
        build_root=tmp_path / "bc-sessions",
        explore_root=tmp_path / "ec-sessions",
        ws_root=ws_root,
        queue=queue,
        index=WorkspaceIndex(root=ws_root, queue=queue),
    )


def message(role: str, content: str) -> _chat_core.ChatMessage:
    return _chat_core.ChatMessage(role=role, content=content, ts=datetime.now(UTC))


def conversation(*texts: str) -> list[_chat_core.ChatMessage]:
    roles = ("user", "assistant")
    return [message(roles[position % 2], text) for position, text in enumerate(texts)]


def build_session(
    world: World,
    *,
    engine_key: str | None = None,
    transcript: Sequence[_chat_core.ChatMessage] = (),
    brief: str | None = None,
) -> str:
    session = build_chat_mod.create_session(root=world.build_root, engine_key=engine_key)
    _chat_core.update_session(
        build_chat_mod.CONFIG,
        session.session_id,
        {"transcript": list(transcript)},
        root=world.build_root,
    )
    if brief is not None:
        build_chat_mod.save_brief(session.session_id, brief, root=world.build_root)
    return session.session_id


def explore_session(
    world: World,
    *,
    engine_key: str | None = None,
    transcript: Sequence[_chat_core.ChatMessage] = (),
    brief: str | None = None,
) -> str:
    session = explore_chat_mod.create_session(
        "architecture", root=world.explore_root, engine_key=engine_key
    )
    _chat_core.update_session(
        explore_chat_mod.CONFIG,
        session.session_id,
        {"transcript": list(transcript)},
        root=world.explore_root,
    )
    if brief is not None:
        explore_chat_mod.save_brief(session.session_id, brief, root=world.explore_root)
    return session.session_id


def archive_session(
    world: World, kind: prep_chat_mod.PrepChatKind, session_id: str, title: str = "Archived work"
) -> workspace_mod.Workspace:
    ws = workspace_mod.create(title, "description", root=world.ws_root)
    root = world.build_root if kind is prep_chat_mod.PrepChatKind.BUILD else world.explore_root
    prep_chat_mod.archive(kind, session_id, ws.path, title=title, root=root)
    return ws


def snapshot(directory: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(directory)): path.read_bytes()
        for path in sorted(directory.rglob("*"))
        if path.is_file()
    }


class TurnWatcher:
    def __init__(self, pubsub: PubSub, channel: str) -> None:
        self.done = threading.Event()
        self.events: list[TeamEvent] = []
        pubsub.subscribe(channel, self._on_event)

    def _on_event(self, event: TeamEvent) -> None:
        self.events.append(event)
        if event.kind in {"assistant_done", "error"}:
            self.done.set()

    def wait(self) -> None:
        assert self.done.wait(5), [event.kind for event in self.events]


def stub_cli(
    monkeypatch: pytest.MonkeyPatch, read_in_flight: Callable[[], str | None]
) -> list[tuple[str, str | None]]:
    seen: list[tuple[str, str | None]] = []

    def fake(**kwargs: Any) -> ChatTurnResult:
        seen.append((kwargs["user_text"], read_in_flight()))
        return ChatTurnResult(result_text="ok", claude_session_id=kwargs["session_id"])

    monkeypatch.setattr("alan.routes._chat_router.run_chat_turn", fake)
    return seen


@contextmanager
def served(world: World, pubsub: PubSub) -> Iterator[TestClient]:
    build_locks = build_chat_mod.BuildChatSessionLocks()
    explore_locks = explore_chat_mod.ExploreChatSessionLocks()
    overrides: dict[Callable[..., Any], Callable[[], Any]] = {
        server._build_chat_sessions_root_dep: lambda: world.build_root,
        server._explore_chat_sessions_root_dep: lambda: world.explore_root,
        server._workspaces_root_dep: lambda: world.ws_root,
        server._queue_dep: lambda: world.queue,
        server._pubsub_dep: lambda: pubsub,
        server._build_chat_locks_dep: lambda: build_locks,
        server._explore_chat_locks_dep: lambda: explore_locks,
    }
    server.app.dependency_overrides.update(overrides)
    try:
        with TestClient(server.app) as client:
            yield client
    finally:
        server.app.dependency_overrides.clear()
