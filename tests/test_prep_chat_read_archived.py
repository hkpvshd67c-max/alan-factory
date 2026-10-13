from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

import alan.prep_chat as prep_chat_mod
from alan import engine_config, server
from tests.prep_chat_link_support import (
    World,
    archive_session,
    build_session,
    conversation,
    explore_session,
    make_world,
    snapshot,
)

_OWN_SLUG = engine_config.default_engine_config().repo.slug
_BUILD = prep_chat_mod.PrepChatKind.BUILD
_EXPLORE = prep_chat_mod.PrepChatKind.EXPLORE


@pytest.fixture
def world(tmp_path: Path) -> Iterator[World]:
    built = make_world(tmp_path)
    yield built
    built.queue.close()


def _strip_legacy_keys(archived_dir: Path) -> None:
    path = archived_dir / "session.json"
    raw: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    raw.pop("engine_key", None)
    raw.pop("cli_session_id", None)
    path.write_text(json.dumps(raw), encoding="utf-8")


def test_read_archived_returns_the_marker_session_and_brief(world: World) -> None:
    session_id = build_session(world, transcript=conversation("question", "answer"), brief="Brief")
    ws = archive_session(world, _BUILD, session_id, "Archived work")

    archived = prep_chat_mod.read_archived(ws.path, session_id)

    assert archived.marker.title == "Archived work"
    assert archived.marker.kind is _BUILD
    assert [m.content for m in archived.session.transcript] == ["question", "answer"]
    assert archived.brief is not None
    assert archived.brief.content == "Brief"


def test_read_archived_has_no_brief_when_none_was_saved(world: World) -> None:
    session_id = explore_session(world, transcript=conversation("question"))
    ws = archive_session(world, _EXPLORE, session_id)

    archived = prep_chat_mod.read_archived(ws.path, session_id)

    assert archived.marker.kind is _EXPLORE
    assert archived.brief is None


def test_read_archived_raises_for_a_missing_marker(world: World) -> None:
    session_id = build_session(world)
    ws = archive_session(world, _BUILD, session_id)
    (ws.path / "prep-chat" / session_id / "prep_chat.json").unlink()

    with pytest.raises(prep_chat_mod.PrepChatError, match="marker not found"):
        prep_chat_mod.read_archived(ws.path, session_id)


def test_read_archived_raises_for_a_missing_session_file(world: World) -> None:
    session_id = build_session(world)
    ws = archive_session(world, _BUILD, session_id)
    (ws.path / "prep-chat" / session_id / "session.json").unlink()

    with pytest.raises(prep_chat_mod.PrepChatError, match="not found"):
        prep_chat_mod.read_archived(ws.path, session_id)


def test_loading_an_archived_session_bridges_legacy_keys_without_writing(world: World) -> None:
    session_id = build_session(world, transcript=conversation("question", "answer"))
    ws = archive_session(world, _BUILD, session_id)
    archived_dir = ws.path / "prep-chat" / session_id
    _strip_legacy_keys(archived_dir)
    before = snapshot(archived_dir)

    loaded = prep_chat_mod.load_archived_session(_BUILD, session_id, ws.path)

    assert loaded.engine_key == _OWN_SLUG
    assert loaded.cli_session_id == session_id
    assert snapshot(archived_dir) == before


def test_reading_the_archived_transcript_over_http_leaves_the_archive_untouched(
    world: World,
) -> None:
    session_id = build_session(world, transcript=conversation("question", "answer"), brief="Brief")
    ws = archive_session(world, _BUILD, session_id)
    archived_dir = ws.path / "prep-chat" / session_id
    _strip_legacy_keys(archived_dir)
    before = snapshot(archived_dir)
    server.app.dependency_overrides[server._queue_dep] = lambda: world.queue
    server.app.dependency_overrides[server._workspaces_root_dep] = lambda: world.ws_root
    try:
        with TestClient(server.app) as client:
            response = client.get(f"/api/workspaces/{ws.task_id}/prep-chat")
    finally:
        server.app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["engine_key"] == _OWN_SLUG
    assert snapshot(archived_dir) == before
