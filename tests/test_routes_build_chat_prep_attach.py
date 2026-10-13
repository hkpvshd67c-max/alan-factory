from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

import alan.build_chat as build_chat_mod
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


def _send(
    client: TestClient,
    world: World,
    pubsub: PubSub,
    monkeypatch: pytest.MonkeyPatch,
    typed: str,
    *,
    engine_key: str | None = None,
) -> tuple[str, dict[str, Any], list[tuple[str, str | None]]]:
    chat = build_chat_mod.create_session(root=world.build_root, engine_key=engine_key)
    seen = stub_cli(
        monkeypatch,
        lambda: _in_flight_text(world, chat.session_id),
    )
    watcher = TurnWatcher(pubsub, f"build_chat:{chat.session_id}")
    response = client.post(
        f"/api/build-chat/sessions/{chat.session_id}/messages", json={"content": typed}
    )
    assert response.status_code == 202
    watcher.wait()
    assert not any("<preparation_chat" in json.dumps(event.payload) for event in watcher.events)
    return chat.session_id, response.json(), seen


def _in_flight_text(world: World, session_id: str) -> str | None:
    in_flight = build_chat_mod.load_session(session_id, root=world.build_root).in_flight_turn
    return None if in_flight is None else in_flight.user_text


def _stored_message(client: TestClient, session_id: str) -> dict[str, Any]:
    body = client.get(f"/api/build-chat/sessions/{session_id}").json()
    return next(m for m in body["messages"] if m["role"] == "user")


def test_the_workspace_prep_chat_link_reaches_the_cli_without_any_public_address(
    client: TestClient, world: World, pubsub: PubSub, monkeypatch: pytest.MonkeyPatch
) -> None:
    earlier = build_session(
        world,
        transcript=conversation("Build a login page", "Use a form"),
        brief="Login page brief",
    )
    ws = archive_session(world, _BUILD, earlier, "Login page")
    typed = f"Continue from {LOCAL_BASE}/workspaces/{ws.task_id}/prep-chat"

    session_id, ack, seen = _send(client, world, pubsub, monkeypatch, typed)

    cli_text, in_flight_text = seen[0]
    assert cli_text.startswith(f"{typed}\n\n<preparation_chat ")
    assert "Login page brief" in cli_text
    assert "Build a login page" in cli_text
    assert "Use a form" in cli_text
    assert in_flight_text == typed
    assert ack == {
        "status": "accepted",
        "prep_chat_reads": [{"status": "read", "title": "Login page"}],
    }
    stored = _stored_message(client, session_id)
    assert stored["content"] == typed
    assert stored["prep_chat_reads"] == [{"status": "read", "title": "Login page"}]


def test_an_archived_explore_conversation_is_readable_from_a_build_chat(
    client: TestClient, world: World, pubsub: PubSub, monkeypatch: pytest.MonkeyPatch
) -> None:
    earlier = explore_session(world, transcript=conversation("Is it viable?", "Yes"))
    ws = archive_session(world, _EXPLORE, earlier, "Viability")

    typed = f"{LOCAL_BASE}/workspaces/{ws.task_id}"

    _, ack, seen = _send(client, world, pubsub, monkeypatch, typed)

    assert ack["prep_chat_reads"] == [{"status": "read", "title": "Viability"}]
    assert "Is it viable?" in seen[0][0]


def test_a_live_session_link_in_the_dev_server_port_is_read(
    client: TestClient, world: World, pubsub: PubSub, monkeypatch: pytest.MonkeyPatch
) -> None:
    earlier = build_session(world, transcript=conversation("Live question", "Live answer"))

    _, ack, seen = _send(
        client, world, pubsub, monkeypatch, f"http://127.0.0.1:5173/build?s={earlier}"
    )

    assert ack["prep_chat_reads"][0]["status"] == "read"
    assert "Live answer" in seen[0][0]


def test_a_conversation_from_another_repository_is_withheld_server_side(
    client: TestClient, world: World, pubsub: PubSub, monkeypatch: pytest.MonkeyPatch
) -> None:
    register_test_engine("acme/widget")
    earlier = build_session(
        world,
        engine_key="acme/widget",
        transcript=conversation("Widget secret question", "Widget secret answer"),
        brief="Widget secret brief",
    )
    ws = archive_session(world, _BUILD, earlier, "Widget secret title")

    session_id, ack, seen = _send(
        client, world, pubsub, monkeypatch, f"{LOCAL_BASE}/workspaces/{ws.task_id}"
    )

    cli_text = seen[0][0]
    assert 'status="other_repository"' in cli_text
    attached = cli_text.split("\n\n", 1)[1]
    assert "Widget secret" not in attached
    assert earlier not in attached
    assert ack["prep_chat_reads"] == [{"status": "other_repository", "title": None}]
    assert _stored_message(client, session_id)["prep_chat_reads"] == ack["prep_chat_reads"]


def test_an_unresolvable_link_is_reported_as_not_found(
    client: TestClient, world: World, pubsub: PubSub, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, ack, seen = _send(
        client, world, pubsub, monkeypatch, f"{LOCAL_BASE}/build?s={UNKNOWN_SESSION_ID}"
    )

    assert ack["prep_chat_reads"] == [{"status": "not_found", "title": None}]
    assert 'status="not_found"' in seen[0][0]


def test_a_message_without_a_link_reaches_the_cli_exactly_as_typed(
    client: TestClient, world: World, pubsub: PubSub, monkeypatch: pytest.MonkeyPatch
) -> None:
    typed = "What about https://github.com/acme/widget/issues/3 and http://localhost:5173/health?"

    session_id, ack, seen = _send(client, world, pubsub, monkeypatch, typed)

    assert seen == [(typed, typed)]
    assert ack == {"status": "accepted", "prep_chat_reads": []}
    assert _stored_message(client, session_id)["prep_chat_reads"] == []


def test_a_later_turn_does_not_repeat_an_earlier_attachment(
    client: TestClient, world: World, pubsub: PubSub, monkeypatch: pytest.MonkeyPatch
) -> None:
    earlier = build_session(world, transcript=conversation("Earlier question", "Earlier answer"))
    session_id, _, seen = _send(
        client, world, pubsub, monkeypatch, f"{LOCAL_BASE}/build?s={earlier}"
    )
    watcher = TurnWatcher(pubsub, f"build_chat:{session_id}")

    follow_up = client.post(
        f"/api/build-chat/sessions/{session_id}/messages", json={"content": "and then?"}
    )
    watcher.wait()

    assert follow_up.json()["prep_chat_reads"] == []
    assert seen[1][0] == "and then?"


def test_a_link_to_the_chat_itself_is_not_attached(
    client: TestClient, world: World, pubsub: PubSub, monkeypatch: pytest.MonkeyPatch
) -> None:
    chat = build_chat_mod.create_session(root=world.build_root)
    seen = stub_cli(monkeypatch, lambda: _in_flight_text(world, chat.session_id))
    watcher = TurnWatcher(pubsub, f"build_chat:{chat.session_id}")
    typed = f"{LOCAL_BASE}/build?s={chat.session_id}"

    response = client.post(
        f"/api/build-chat/sessions/{chat.session_id}/messages", json={"content": typed}
    )
    watcher.wait()

    assert response.json()["prep_chat_reads"] == []
    assert seen[0][0] == typed
