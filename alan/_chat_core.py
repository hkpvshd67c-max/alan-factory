from __future__ import annotations

import contextlib
import json
import logging
import re
import shutil
import subprocess
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Generic, Literal, TypeVar

from pydantic import BaseModel, Field, TypeAdapter, ValidationError

from alan import engine_config, store

_SESSION_FILE = "session.json"
_STREAM_FILE = "stream.jsonl"
_COLLISION_STREAM_FILE = "collision_stream.jsonl"
_BRIEF_FILE = "brief.json"
_SESSION_ID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)
_PREVIEW_MAX = 120
_LIST_CAP = 25
_INTERRUPTED_TEXT = (
    "alan was restarted while I was preparing a reply. "
    "Please re-send your last message to continue."
)


class ChatCoreError(RuntimeError):
    pass


def _empty_operations() -> list[str]:
    return []


class PrepChatRead(BaseModel):
    status: Literal["read", "other_repository", "not_found"]
    title: str | None = None


def _empty_prep_chat_reads() -> list[PrepChatRead]:
    return []


class ChatMessage(BaseModel):
    role: str = Field(pattern=r"^(user|assistant)$")
    content: str
    ts: datetime
    interrupted: bool = False
    operations: list[str] = Field(default_factory=_empty_operations)
    prep_chat_reads: list[PrepChatRead] = Field(default_factory=_empty_prep_chat_reads)


def _empty_transcript() -> list[ChatMessage]:
    return []


class ChatInFlightTurn(BaseModel):
    started_at: datetime
    user_text: str


class ChatSession(BaseModel):
    session_id: str
    created_at: datetime
    transcript: list[ChatMessage] = Field(default_factory=_empty_transcript)
    in_flight_turn: ChatInFlightTurn | None = None
    project_id: str | None = None
    cli_session_id: str | None = None
    engine_key: str | None = None


class ChatSessionSummary(BaseModel):
    session_id: str
    created_at: datetime
    last_message_preview: str
    in_flight: bool
    engine_key: str | None = None


class ChatBrief(BaseModel):
    content: str
    updated_at: datetime


S = TypeVar("S", bound=ChatSession)
_T = TypeVar("_T")

_BriefAdapter: TypeAdapter[ChatBrief] = TypeAdapter(ChatBrief)


@dataclass(frozen=True)
class ChatCoreConfig(Generic[S]):
    sessions_root: Callable[[], Path]
    logger: logging.Logger
    label: str
    error: type[ChatCoreError]
    session_type: type[S]
    session_adapter: TypeAdapter[S]


def validate_session_id(cfg: ChatCoreConfig[S], session_id: str) -> None:
    if not _SESSION_ID_RE.fullmatch(session_id):
        raise cfg.error(f"invalid session_id: {session_id}")


def _base_dir(cfg: ChatCoreConfig[S], root: Path | None) -> Path:
    return root if root is not None else cfg.sessions_root()


def _session_dir(cfg: ChatCoreConfig[S], session_id: str, root: Path | None) -> Path:
    validate_session_id(cfg, session_id)
    return _base_dir(cfg, root) / session_id


def resolve_sessions_root(cfg: ChatCoreConfig[S]) -> Path:
    return _base_dir(cfg, None)


def session_dir(cfg: ChatCoreConfig[S], session_id: str, *, root: Path | None = None) -> Path:
    return _session_dir(cfg, session_id, root)


def stream_path(cfg: ChatCoreConfig[S], session_id: str, *, root: Path | None = None) -> Path:
    return _session_dir(cfg, session_id, root) / _STREAM_FILE


def collision_stream_path(
    cfg: ChatCoreConfig[S], session_id: str, *, root: Path | None = None
) -> Path:
    return _session_dir(cfg, session_id, root) / _COLLISION_STREAM_FILE


def create_session(
    cfg: ChatCoreConfig[S], *, root: Path | None = None, **fields: Any
) -> S:
    base = _base_dir(cfg, root)
    base.mkdir(parents=True, exist_ok=True)
    for _ in range(8):
        session_id = str(uuid.uuid4())
        candidate = base / session_id
        try:
            candidate.mkdir()
        except FileExistsError:
            continue
        session = cfg.session_type(
            session_id=session_id, created_at=datetime.now(UTC), **fields
        )
        _write_session(candidate, session)
        return session
    raise cfg.error("could not allocate unique session_id")


def _read_session(
    cfg: ChatCoreConfig[S], session_id: str, root: Path | None
) -> tuple[S, dict[str, Any]]:
    path = _session_dir(cfg, session_id, root) / _SESSION_FILE
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise cfg.error(f"{cfg.label} session not found: {session_id}") from exc
    try:
        raw_obj = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise cfg.error(f"{cfg.label} session corrupt: {session_id}") from exc
    try:
        return cfg.session_adapter.validate_python(raw_obj), raw_obj
    except ValidationError as exc:
        raise cfg.error(f"{cfg.label} session corrupt: {session_id}") from exc


def _legacy_cli_session_id(session: S) -> S:
    has_assistant_message = any(m.role == "assistant" for m in session.transcript)
    if not has_assistant_message:
        return session
    return session.model_copy(update={"cli_session_id": session.session_id})


def _alan_own_repo_engine_key() -> str:
    return engine_config.default_engine_config().repo.slug


def _legacy_engine_key(session: S) -> S:
    return session.model_copy(update={"engine_key": _alan_own_repo_engine_key()})


def _peek_bridged(
    cfg: ChatCoreConfig[S], session_id: str, root: Path | None
) -> tuple[S, bool]:
    session, raw_obj = _read_session(cfg, session_id, root)
    bridged = session
    if "cli_session_id" not in raw_obj:
        bridged = _legacy_cli_session_id(bridged)
    if "engine_key" not in raw_obj:
        bridged = _legacy_engine_key(bridged)
    return bridged, bridged is not session


def peek_session(cfg: ChatCoreConfig[S], session_id: str, *, root: Path | None = None) -> S:
    return _peek_bridged(cfg, session_id, root)[0]


def load_session(cfg: ChatCoreConfig[S], session_id: str, *, root: Path | None = None) -> S:
    session, bridged = _peek_bridged(cfg, session_id, root)
    if bridged:
        _write_session(_session_dir(cfg, session_id, root), session)
    return session


def append_user_message_and_mark_started(
    cfg: ChatCoreConfig[S], session: S, message: ChatMessage, *, root: Path | None = None
) -> S:
    fresh = load_session(cfg, session.session_id, root=root)
    flag = ChatInFlightTurn(started_at=datetime.now(UTC), user_text=message.content)
    updated = fresh.model_copy(
        update={"transcript": [*fresh.transcript, message], "in_flight_turn": flag}
    )
    _write_session(_session_dir(cfg, session.session_id, root), updated)
    return updated


def append_assistant_and_clear_in_flight(
    cfg: ChatCoreConfig[S], session: S, message: ChatMessage, *, root: Path | None = None
) -> S:
    fresh = load_session(cfg, session.session_id, root=root)
    updated = fresh.model_copy(
        update={"transcript": [*fresh.transcript, message], "in_flight_turn": None}
    )
    _write_session(_session_dir(cfg, session.session_id, root), updated)
    return updated


def clear_in_flight_turn(cfg: ChatCoreConfig[S], session_id: str, *, root: Path | None = None) -> S:
    fresh = load_session(cfg, session_id, root=root)
    updated = fresh.model_copy(update={"in_flight_turn": None})
    _write_session(_session_dir(cfg, session_id, root), updated)
    return updated


def update_session(
    cfg: ChatCoreConfig[S], session_id: str, updates: dict[str, Any], *, root: Path | None = None
) -> S:
    fresh = load_session(cfg, session_id, root=root)
    updated = fresh.model_copy(update=updates)
    _write_session(_session_dir(cfg, session_id, root), updated)
    return updated


def _summarize_session(session: ChatSession) -> ChatSessionSummary:
    preview = session.transcript[-1].content[:_PREVIEW_MAX] if session.transcript else ""
    return ChatSessionSummary(
        session_id=session.session_id,
        created_at=session.created_at,
        last_message_preview=preview,
        in_flight=session.in_flight_turn is not None,
        engine_key=session.engine_key,
    )


def _filter_by_project(sessions: list[S], project_id: str | None) -> list[S]:
    if project_id is None:
        return sessions
    return [s for s in sessions if s.project_id == project_id]


def resolve_known_scope(engine_key: str) -> engine_config.RepoIdentity | None:
    cfg = engine_config.lookup_by_slug(engine_key)
    if cfg is not None:
        return cfg.repo
    if engine_key == _alan_own_repo_engine_key():
        return engine_config.canonical_scope_key(None)
    return None


def _filter_by_engine_key(sessions: list[S], engine_key: str | None) -> list[S]:
    if engine_key is None:
        return sessions
    target = resolve_known_scope(engine_key)
    if target is None:
        return []
    return [s for s in sessions if engine_config.canonical_scope_key(s.engine_key) == target]


def _load_all_valid_sessions(cfg: ChatCoreConfig[S], root: Path | None) -> list[S]:
    base = _base_dir(cfg, root)
    if not base.is_dir():
        return []
    sessions: list[S] = []
    for entry in base.iterdir():
        if not entry.is_dir() or not _SESSION_ID_RE.fullmatch(entry.name):
            continue
        try:
            session = load_session(cfg, entry.name, root=root)
        except cfg.error as exc:
            cfg.logger.warning("skipping %s session %s: %s", cfg.label, entry.name, exc)
            continue
        sessions.append(session)
    return sessions


def iter_all_valid_sessions(cfg: ChatCoreConfig[S], *, root: Path | None = None) -> list[S]:
    return _load_all_valid_sessions(cfg, root)


def _iter_valid_sessions(
    cfg: ChatCoreConfig[S],
    root: Path | None,
    project_id: str | None = None,
    *,
    engine_key: str | None = None,
) -> list[S]:
    sessions = _filter_by_project(_load_all_valid_sessions(cfg, root), project_id)
    sessions = _filter_by_engine_key(sessions, engine_key)
    sessions.sort(key=lambda s: s.created_at, reverse=True)
    return sessions[:_LIST_CAP]


def list_sessions_mapped(
    cfg: ChatCoreConfig[S],
    mapper: Callable[[S], _T],
    *,
    root: Path | None = None,
    project_id: str | None = None,
    engine_key: str | None = None,
) -> list[_T]:
    return [
        mapper(s) for s in _iter_valid_sessions(cfg, root, project_id, engine_key=engine_key)
    ]


def list_sessions(
    cfg: ChatCoreConfig[S],
    *,
    root: Path | None = None,
    project_id: str | None = None,
    engine_key: str | None = None,
) -> list[ChatSessionSummary]:
    return [
        _summarize_session(s)
        for s in _iter_valid_sessions(cfg, root, project_id, engine_key=engine_key)
    ]


def delete_session(cfg: ChatCoreConfig[S], session_id: str, *, root: Path | None = None) -> None:
    path = _session_dir(cfg, session_id, root)
    if not path.is_dir():
        raise cfg.error(f"{cfg.label} session not found: {session_id}")
    try:
        shutil.rmtree(path)
    except FileNotFoundError as exc:
        raise cfg.error(f"{cfg.label} session not found: {session_id}") from exc


def load_brief(
    cfg: ChatCoreConfig[S], session_id: str, *, root: Path | None = None
) -> ChatBrief | None:
    path = _session_dir(cfg, session_id, root) / _BRIEF_FILE
    if not path.is_file():
        return None
    try:
        return _BriefAdapter.validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError) as exc:
        cfg.logger.warning("%s brief corrupt for %s: %s", cfg.label, session_id, exc)
        return None


def save_brief(
    cfg: ChatCoreConfig[S], session_id: str, content: str, *, root: Path | None = None
) -> ChatBrief:
    sdir = _session_dir(cfg, session_id, root)
    brief = ChatBrief(content=content, updated_at=datetime.now(UTC))
    write_json(sdir / _BRIEF_FILE, brief.model_dump(mode="json"))
    return brief


def _reconcile_session_entry(cfg: ChatCoreConfig[S], entry: Path, root: Path | None) -> bool:
    try:
        session = load_session(cfg, entry.name, root=root)
    except cfg.error as exc:
        cfg.logger.warning("reconcile: skipping %s session %s: %s", cfg.label, entry.name, exc)
        return False
    if session.in_flight_turn is None:
        return False
    try:
        append_assistant_and_clear_in_flight(
            cfg,
            session,
            ChatMessage(role="assistant", content=_INTERRUPTED_TEXT, ts=datetime.now(UTC)),
            root=root,
        )
        return True
    except Exception:  # noqa: BLE001
        cfg.logger.exception("reconcile: %s sweep failed for %s", cfg.label, entry.name)
        return False


def reconcile_in_flight_turns(cfg: ChatCoreConfig[S], *, root: Path | None = None) -> list[str]:
    base = _base_dir(cfg, root)
    if not base.is_dir():
        return []
    cleared: list[str] = []
    for entry in base.iterdir():
        if not entry.is_dir() or not _SESSION_ID_RE.fullmatch(entry.name):
            continue
        if _reconcile_session_entry(cfg, entry, root):
            cleared.append(entry.name)
    return cleared


def _has_engine_key_on_disk(cfg: ChatCoreConfig[S], session_id: str, root: Path | None) -> bool:
    path = _session_dir(cfg, session_id, root) / _SESSION_FILE
    try:
        raw_obj = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return True
    return "engine_key" in raw_obj


def _is_legacy_session_entry(cfg: ChatCoreConfig[S], entry: Path, root: Path | None) -> bool:
    return (
        entry.is_dir()
        and bool(_SESSION_ID_RE.fullmatch(entry.name))
        and not _has_engine_key_on_disk(cfg, entry.name, root)
    )


def _backfill_legacy_session(cfg: ChatCoreConfig[S], session_id: str, root: Path | None) -> bool:
    try:
        load_session(cfg, session_id, root=root)
    except cfg.error as exc:
        cfg.logger.warning(
            "sweep_legacy_engine_keys: skipping %s session %s: %s", cfg.label, session_id, exc
        )
        return False
    return True


def sweep_legacy_engine_keys(cfg: ChatCoreConfig[S], *, root: Path | None = None) -> list[str]:
    base = _base_dir(cfg, root)
    if not base.is_dir():
        return []
    legacy = [
        entry.name for entry in base.iterdir() if _is_legacy_session_entry(cfg, entry, root)
    ]
    return [
        session_id for session_id in legacy if _backfill_legacy_session(cfg, session_id, root)
    ]


def _write_session(path: Path, session: ChatSession) -> None:
    write_json(path / _SESSION_FILE, session.model_dump(mode="json"))


def write_json(path: Path, data: Any) -> None:
    store.write_text_atomic(path, json.dumps(data, indent=2, sort_keys=False))


@dataclass
class TurnEntry:
    session_id: str
    proc: subprocess.Popen[str] | None = None
    cancel_requested: bool = False
    released: threading.Event = field(default_factory=threading.Event)


class ChatSessionLocks:
    def __init__(self, locked_exc: type[ChatCoreError], label: str) -> None:
        self._locked_exc = locked_exc
        self._label = label
        self._lock = threading.Lock()
        self._entries: dict[str, TurnEntry] = {}

    def acquire(self, session_id: str) -> TurnEntry:
        with self._lock:
            if session_id in self._entries:
                raise self._locked_exc(f"{self._label} turn already in progress: {session_id}")
            entry = TurnEntry(session_id=session_id)
            self._entries[session_id] = entry
            return entry

    def set_proc(self, entry: TurnEntry, proc: subprocess.Popen[str]) -> None:
        with self._lock:
            entry.proc = proc
            cancel_requested = entry.cancel_requested
        if cancel_requested:
            proc.terminate()

    def cancel(self, session_id: str) -> bool:
        with self._lock:
            entry = self._entries.get(session_id)
            if entry is None:
                return False
            entry.cancel_requested = True
            proc = entry.proc
        if proc is not None:
            proc.terminate()
        return True

    def was_cancelled(self, entry: TurnEntry) -> bool:
        with self._lock:
            return entry.cancel_requested

    def release(self, entry: TurnEntry) -> None:
        with self._lock:
            if self._entries.get(entry.session_id) is entry:
                del self._entries[entry.session_id]
        entry.released.set()

    def wait_released(self, session_id: str, timeout: float) -> bool:
        with self._lock:
            entry = self._entries.get(session_id)
        if entry is None:
            return True
        return entry.released.wait(timeout)

    def force_release(self, session_id: str) -> None:
        with self._lock:
            entry = self._entries.pop(session_id, None)
        if entry is None:
            return
        if entry.proc is not None:
            with contextlib.suppress(Exception):
                entry.proc.kill()
        entry.released.set()
