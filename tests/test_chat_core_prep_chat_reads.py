from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from alan import _chat_core, engine_config
from alan.build_chat import CONFIG as BUILD_CONFIG
from tests.conftest import register_test_engine
from tests.prep_chat_link_support import snapshot

_ALAN_OWN_REPO_SLUG = engine_config.default_engine_config().repo.slug


def _session_path(session: _chat_core.ChatSession, root: Path) -> Path:
    return root / session.session_id / "session.json"


def _rewrite(path: Path, mutate: Callable[[dict[str, Any]], None]) -> None:
    raw = json.loads(path.read_text(encoding="utf-8"))
    mutate(raw)
    path.write_text(json.dumps(raw), encoding="utf-8")


def _legacy_session(root: Path, *, with_assistant: bool) -> _chat_core.ChatSession:
    session = _chat_core.create_session(BUILD_CONFIG, root=root)
    if with_assistant:
        reply = _chat_core.ChatMessage(role="assistant", content="hi", ts=datetime.now(UTC))
        _chat_core.update_session(
            BUILD_CONFIG, session.session_id, {"transcript": [reply]}, root=root
        )

    def drop_bridged_keys(raw: dict[str, Any]) -> None:
        raw.pop("cli_session_id", None)
        raw.pop("engine_key", None)

    _rewrite(_session_path(session, root), drop_bridged_keys)
    return session


def test_peek_bridges_legacy_keys_in_memory_without_writing(tmp_path: Path) -> None:
    session = _legacy_session(tmp_path, with_assistant=True)
    before = snapshot(tmp_path)

    peeked = _chat_core.peek_session(BUILD_CONFIG, session.session_id, root=tmp_path)

    assert peeked.engine_key == _ALAN_OWN_REPO_SLUG
    assert peeked.cli_session_id == session.session_id
    assert snapshot(tmp_path) == before


def test_peek_of_a_legacy_session_without_replies_leaves_the_cli_session_unset(
    tmp_path: Path,
) -> None:
    session = _legacy_session(tmp_path, with_assistant=False)
    peeked = _chat_core.peek_session(BUILD_CONFIG, session.session_id, root=tmp_path)
    assert peeked.cli_session_id is None


def test_peek_of_a_current_session_returns_it_unchanged(tmp_path: Path) -> None:
    session = _chat_core.create_session(BUILD_CONFIG, root=tmp_path, engine_key="acme/widget")
    before = snapshot(tmp_path)
    peeked = _chat_core.peek_session(BUILD_CONFIG, session.session_id, root=tmp_path)
    assert peeked == session
    assert snapshot(tmp_path) == before


def test_load_persists_what_peek_only_computes(tmp_path: Path) -> None:
    session = _legacy_session(tmp_path, with_assistant=True)

    loaded = _chat_core.load_session(BUILD_CONFIG, session.session_id, root=tmp_path)

    on_disk = json.loads(_session_path(session, tmp_path).read_text(encoding="utf-8"))
    assert on_disk["engine_key"] == loaded.engine_key == _ALAN_OWN_REPO_SLUG
    assert on_disk["cli_session_id"] == session.session_id


def test_load_of_a_current_session_does_not_rewrite_the_file(tmp_path: Path) -> None:
    session = _chat_core.create_session(BUILD_CONFIG, root=tmp_path)
    path = _session_path(session, tmp_path)
    path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    before = snapshot(tmp_path)
    _chat_core.load_session(BUILD_CONFIG, session.session_id, root=tmp_path)
    assert snapshot(tmp_path) == before


def test_peek_reports_a_missing_session(tmp_path: Path) -> None:
    with pytest.raises(_chat_core.ChatCoreError, match="not found"):
        _chat_core.peek_session(
            BUILD_CONFIG, "12345678-1234-1234-1234-1234567890ab", root=tmp_path
        )


def test_peek_reports_a_corrupt_session(tmp_path: Path) -> None:
    session = _chat_core.create_session(BUILD_CONFIG, root=tmp_path)
    _session_path(session, tmp_path).write_text("{not json", encoding="utf-8")
    with pytest.raises(_chat_core.ChatCoreError, match="corrupt"):
        _chat_core.peek_session(BUILD_CONFIG, session.session_id, root=tmp_path)


def test_peek_reports_an_invalid_session_shape(tmp_path: Path) -> None:
    session = _chat_core.create_session(BUILD_CONFIG, root=tmp_path)
    _session_path(session, tmp_path).write_text('{"session_id": 1}', encoding="utf-8")
    with pytest.raises(_chat_core.ChatCoreError, match="corrupt"):
        _chat_core.peek_session(BUILD_CONFIG, session.session_id, root=tmp_path)


def test_resolve_known_scope_maps_a_registered_slug_to_its_repository() -> None:
    identity = register_test_engine("acme/widget")
    assert _chat_core.resolve_known_scope("acme/widget") == identity


def test_resolve_known_scope_maps_alans_own_slug_to_the_canonical_scope() -> None:
    assert _chat_core.resolve_known_scope(_ALAN_OWN_REPO_SLUG) == engine_config.canonical_scope_key(
        None
    )


def test_resolve_known_scope_is_none_for_an_unknown_slug() -> None:
    assert _chat_core.resolve_known_scope("ghost/unknown") is None


def test_a_message_defaults_to_no_prep_chat_reads() -> None:
    message = _chat_core.ChatMessage(role="user", content="hello", ts=datetime.now(UTC))
    assert message.prep_chat_reads == []


def test_prep_chat_reads_survive_a_save_and_load(tmp_path: Path) -> None:
    session = _chat_core.create_session(BUILD_CONFIG, root=tmp_path)
    reads = [
        _chat_core.PrepChatRead(status="read", title="Fix login"),
        _chat_core.PrepChatRead(status="other_repository"),
        _chat_core.PrepChatRead(status="not_found"),
    ]
    user = _chat_core.ChatMessage(
        role="user", content="see links", ts=datetime.now(UTC), prep_chat_reads=reads
    )
    _chat_core.append_user_message_and_mark_started(
        BUILD_CONFIG, session, user, root=tmp_path
    )

    loaded = _chat_core.load_session(BUILD_CONFIG, session.session_id, root=tmp_path)

    assert loaded.transcript[0].prep_chat_reads == reads
    assert loaded.in_flight_turn is not None
    assert loaded.in_flight_turn.user_text == "see links"


def test_a_stored_message_without_the_field_loads_with_no_reads(tmp_path: Path) -> None:
    session = _chat_core.create_session(BUILD_CONFIG, root=tmp_path)
    user = _chat_core.ChatMessage(role="user", content="older", ts=datetime.now(UTC))
    _chat_core.append_user_message_and_mark_started(BUILD_CONFIG, session, user, root=tmp_path)

    def drop_reads(raw: dict[str, Any]) -> None:
        for stored in raw["transcript"]:
            del stored["prep_chat_reads"]

    _rewrite(_session_path(session, tmp_path), drop_reads)

    loaded = _chat_core.load_session(BUILD_CONFIG, session.session_id, root=tmp_path)

    assert loaded.transcript[0].prep_chat_reads == []


def test_an_unknown_read_status_is_rejected() -> None:
    with pytest.raises(ValueError, match="status"):
        _chat_core.PrepChatRead.model_validate({"status": "skimmed"})
