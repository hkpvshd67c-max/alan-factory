from __future__ import annotations

import logging
import re
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, TypeAdapter, ValidationError

import alan.build_chat as build_chat_mod
import alan.explore_chat as explore_chat_mod
from alan import _chat_core
from alan import dispatch_provenance as provenance_mod

logger = logging.getLogger("alan.prep_chat")

PREP_CHAT_DIR_NAME = "prep-chat"
_MARKER_FILE = "prep_chat.json"
_SESSION_ID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)


class PrepChatKind(StrEnum):
    BUILD = "build"
    EXPLORE = "explore"


class PrepChatError(RuntimeError):
    pass


def dispatch_surface(kind: PrepChatKind | None) -> provenance_mod.DispatchSurface:
    if kind is None:
        return provenance_mod.DispatchSurface.API_DIRECT
    if kind is PrepChatKind.BUILD:
        return provenance_mod.DispatchSurface.BUILD_CHAT
    return provenance_mod.DispatchSurface.EXPLORE_CHAT


class PrepChatMarker(BaseModel):
    kind: PrepChatKind
    session_id: str
    consultant_key: str | None = None
    title: str
    created_at: datetime
    archived_at: datetime


class PrepChatRef(BaseModel):
    session_id: str
    kind: PrepChatKind
    consultant_key: str | None = None


_MarkerAdapter: TypeAdapter[PrepChatMarker] = TypeAdapter(PrepChatMarker)


def archived_root(ws_path: Path) -> Path:
    return ws_path / PREP_CHAT_DIR_NAME


def archived_session_dir(ws_path: Path, session_id: str) -> Path:
    return archived_root(ws_path) / session_id


def _live_session_dir(kind: PrepChatKind, session_id: str, root: Path | None) -> Path:
    if kind is PrepChatKind.BUILD:
        return build_chat_mod.session_dir(session_id, root=root)
    return explore_chat_mod.session_dir(session_id, root=root)


def _load_live_session(
    kind: PrepChatKind, session_id: str, root: Path | None
) -> _chat_core.ChatSession:
    if kind is PrepChatKind.BUILD:
        return build_chat_mod.load_session(session_id, root=root)
    return explore_chat_mod.load_session(session_id, root=root)


def archive(
    kind: PrepChatKind,
    session_id: str,
    ws_path: Path,
    *,
    title: str,
    root: Path | None = None,
) -> None:
    live_session = _load_live_session(kind, session_id, root)
    consultant_key = (
        live_session.consultant_key
        if isinstance(live_session, explore_chat_mod.ExploreChatSession)
        else None
    )
    marker = PrepChatMarker(
        kind=kind,
        session_id=session_id,
        consultant_key=consultant_key,
        title=title,
        created_at=live_session.created_at,
        archived_at=datetime.now(UTC),
    )
    live_dir = _live_session_dir(kind, session_id, root)
    dest_root = archived_root(ws_path)
    dest_dir = dest_root / session_id
    if dest_dir.exists():
        raise PrepChatError(f"prep chat archive destination already exists: {session_id}")
    _chat_core.write_json(live_dir / _MARKER_FILE, marker.model_dump(mode="json"))
    dest_root.mkdir(parents=True, exist_ok=True)
    shutil.move(str(live_dir), str(dest_dir))


def find_archived_session_id(ws_path: Path) -> str | None:
    root = archived_root(ws_path)
    if not root.is_dir():
        return None
    matches = sorted(
        entry.name
        for entry in root.iterdir()
        if entry.is_dir() and _SESSION_ID_RE.fullmatch(entry.name)
    )
    return matches[0] if matches else None


def load_marker(ws_path: Path, session_id: str) -> PrepChatMarker:
    path = archived_session_dir(ws_path, session_id) / _MARKER_FILE
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise PrepChatError(f"prep chat marker not found: {session_id}") from exc
    try:
        return _MarkerAdapter.validate_json(raw)
    except ValidationError as exc:
        raise PrepChatError(f"prep chat marker corrupt: {session_id}") from exc


def load_ref(ws_path: Path) -> PrepChatRef | None:
    session_id = find_archived_session_id(ws_path)
    if session_id is None:
        return None
    try:
        marker = load_marker(ws_path, session_id)
    except PrepChatError:
        logger.warning("prep_chat: marker unreadable for %s", session_id, exc_info=True)
        return None
    return PrepChatRef(
        session_id=marker.session_id, kind=marker.kind, consultant_key=marker.consultant_key
    )


def load_archived_session(
    kind: PrepChatKind, session_id: str, ws_path: Path
) -> _chat_core.ChatSession:
    root = archived_root(ws_path)
    try:
        if kind is PrepChatKind.BUILD:
            return _chat_core.peek_session(build_chat_mod.CONFIG, session_id, root=root)
        return _chat_core.peek_session(explore_chat_mod.CONFIG, session_id, root=root)
    except _chat_core.ChatCoreError as exc:
        raise PrepChatError(str(exc)) from exc


def load_archived_brief(
    kind: PrepChatKind, session_id: str, ws_path: Path
) -> _chat_core.ChatBrief | None:
    root = archived_root(ws_path)
    if kind is PrepChatKind.BUILD:
        return _chat_core.load_brief(build_chat_mod.CONFIG, session_id, root=root)
    return _chat_core.load_brief(explore_chat_mod.CONFIG, session_id, root=root)


@dataclass(frozen=True)
class ArchivedPrepChat:
    marker: PrepChatMarker
    session: _chat_core.ChatSession
    brief: _chat_core.ChatBrief | None


def read_archived(ws_path: Path, session_id: str) -> ArchivedPrepChat:
    marker = load_marker(ws_path, session_id)
    session = load_archived_session(marker.kind, session_id, ws_path)
    brief = load_archived_brief(marker.kind, session_id, ws_path)
    return ArchivedPrepChat(marker=marker, session=session, brief=brief)
