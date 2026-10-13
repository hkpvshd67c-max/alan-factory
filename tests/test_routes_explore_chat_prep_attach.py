from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

import alan.explore_chat as explore_chat_mod
import alan.prep_chat as prep_chat_mod
from alan.dispatcher import PubSub
from tests.conftest import register_test_engine
from tests.prep_chat_link_support import (
    LOCAL_BASE,
    UNKNOWN_SESSION_ID,
    TurnWatcher,
    World,
    archive_session,
    build_session,
    conversation,
    explore_session,
    make_world,
    served,
    stub_cli,
)

_BUILD = prep_chat_mod.PrepChatKind.BUILD
_EXPLORE = prep_chat_mod.PrepChatKind.EXPLORE


@pytest.fixture
def world(tmp_path: Path) -> Iterator[World]:
    built = make_world(tmp_path)
    yield built
    built.queue.close()


@pytest.fixture
def pubsub() -> PubSub:
    return PubSub()


@pytest.fixture
def client(world: World, pubsub: PubSub) -> Iterator[TestClient]:
    with served(world, pubsub) as test_client:
        yield test_client


def _in_flight_text(world: World, session_id: str) -> str | None:
    in_flight = explore_chat_mod.load_session(session_id, root=world.explore_root).in_flight_turn
    return None if in_flight is None else in_flight.user_text


def _send(
    client: TestClient,
    world: World,
    pubsub: PubSub,
    monkeypatch: pytest.MonkeyPatch,
    typed: str,
    *,
    engine_key: str | None = None,
) -> tuple[str, dict[str, Any], list[tuple[str, str | None]]]:
    chat = explore_chat_mod.create_session(
        "architecture", root=world.explore_root, engine_key=engine_key
    )
    seen = stub_cli(monkeypatch, lambda: _in_flight_text(world, chat.session_id))
    watcher = TurnWatcher(pubsub, f"explore_chat:{chat.session_id}")
    response = client.post(
        f"/api/explore-chat/sessions/{chat.session_id}/messages", json={"content": typed}
    )
    assert response.status_code == 202
    watcher.wait()
    assert not any("<preparation_chat" in json.dumps(event.payload) for event in watcher.events)
    return chat.session_id, response.json(), seen


def _stored_message(client: TestClient, session_id: str) -> dict[str, Any]:
    body = client.get(f"/api/explore-chat/sessions/{session_id}").json()
    return next(m for m in body["messages"] if m["role"] == "user")


def test_the_workspace_prep_chat_link_reaches_the_cli_without_any_public_address(
    client: TestClient, world: World, pubsub: PubSub, monkeypatch: pytest.MonkeyPatch
) -> None:
    earlier = explore_session(
        world, transcript=conversation("Is it viable?", "Probably"), brief="Viability brief"
    )
    ws = archive_session(world, _EXPLORE, earlier, "Viability study")
    typed = f"Build on {LOCAL_BASE}/workspaces/{ws.task_id}/prep-chat"

    session_id, ack, seen = _send(client, world, pubsub, monkeypatch, typed)

    cli_text, in_flight_text = seen[0]
    assert cli_text.startswith(f"{typed}\n\n<preparation_chat ")
    assert "Viability brief" in cli_text
    assert "Probably" in cli_text
    assert in_flight_text == typed
    assert ack == {
        "status": "accepted",
        "prep_chat_reads": [{"status": "read", "title": "Viability study"}],
    }
    stored = _stored_message(client, session_id)
    assert stored["content"] == typed
    assert stored["prep_chat_reads"] == ack["prep_chat_reads"]


def test_an_archived_build_conversation_is_readable_from_an_explore_chat(
    client: TestClient, world: World, pubsub: PubSub, monkeypatch: pytest.MonkeyPatch
) -> None:
    earlier = build_session(world, transcript=conversation("Build question", "Build answer"))
    archive_session(world, _BUILD, earlier, "Build work")

    _, ack, seen = _send(
        client, world, pubsub, monkeypatch, f"{LOCAL_BASE}/api/build-chat/sessions/{earlier}"
    )

    assert ack["prep_chat_reads"] == [{"status": "read", "title": "Build work"}]
    assert "Build answer" in seen[0][0]


def test_a_live_explore_session_link_is_read(
    client: TestClient, world: World, pubsub: PubSub, monkeypatch: pytest.MonkeyPatch
) -> None:
    earlier = explore_session(world, transcript=conversation("Live question", "Live answer"))

    _, ack, seen = _send(
        client,
        world,
        pubsub,
        monkeypatch,
        f"{LOCAL_BASE}/explore/architecture?s={earlier}",
    )

    assert ack["prep_chat_reads"][0]["status"] == "read"
    assert "Live answer" in seen[0][0]


def test_a_conversation_from_another_repository_is_withheld_server_side(
    client: TestClient, world: World, pubsub: PubSub, monkeypatch: pytest.MonkeyPatch
) -> None:
    register_test_engine("acme/widget")
    earlier = explore_session(
        world,
        engine_key="acme/widget",
        transcript=conversation("Widget secret question"),
        brief="Widget secret brief",
    )
    ws = archive_session(world, _EXPLORE, earlier, "Widget secret title")

    session_id, ack, seen = _send(
        client, world, pubsub, monkeypatch, f"{LOCAL_BASE}/workspaces/{ws.task_id}"
    )

    attached = seen[0][0].split("\n\n", 1)[1]
    assert 'status="other_repository"' in attached
    assert "Widget secret" not in attached
    assert earlier not in attached
    assert ack["prep_chat_reads"] == [{"status": "other_repository", "title": None}]
    assert _stored_message(client, session_id)["prep_chat_reads"] == ack["prep_chat_reads"]


def test_a_chat_bound_to_another_repository_is_not_given_alans_own_conversation(
    client: TestClient, world: World, pubsub: PubSub, monkeypatch: pytest.MonkeyPatch
) -> None:
    register_test_engine("acme/widget")
    earlier = explore_session(world, transcript=conversation("Own secret question"))
    ws = archive_session(world, _EXPLORE, earlier, "Own secret title")

    session_id, ack, _ = _send(
        client,
        world,
        pubsub,
        monkeypatch,
        f"{LOCAL_BASE}/workspaces/{ws.task_id}",
        engine_key="acme/widget",
    )

    assert ack["prep_chat_reads"] == [{"status": "other_repository", "title": None}]
    assert _stored_message(client, session_id)["prep_chat_reads"] == ack["prep_chat_reads"]


def test_an_unresolvable_link_is_reported_as_not_found(
    client: TestClient, world: World, pubsub: PubSub, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, ack, seen = _send(
        client, world, pubsub, monkeypatch, f"{LOCAL_BASE}/workspaces/no-such-workspace"
    )

    assert ack["prep_chat_reads"] == [{"status": "not_found", "title": None}]
    assert 'status="not_found"' in seen[0][0]


def test_a_message_without_a_link_reaches_the_cli_exactly_as_typed(
    client: TestClient, world: World, pubsub: PubSub, monkeypatch: pytest.MonkeyPatch
) -> None:
    typed = "Compare https://example.com/pricing with http://localhost:5173/health"

    session_id, ack, seen = _send(client, world, pubsub, monkeypatch, typed)

    assert seen == [(typed, typed)]
    assert ack == {"status": "accepted", "prep_chat_reads": []}
    assert _stored_message(client, session_id)["prep_chat_reads"] == []


def test_a_link_to_an_unknown_session_id_is_not_found(
    client: TestClient, world: World, pubsub: PubSub, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, ack, _ = _send(
        client,
        world,
        pubsub,
        monkeypatch,
        f"{LOCAL_BASE}/api/explore-chat/sessions/{UNKNOWN_SESSION_ID}",
    )

    assert ack["prep_chat_reads"] == [{"status": "not_found", "title": None}]
