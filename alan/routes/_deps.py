from __future__ import annotations

import asyncio
import hmac
import json
import logging
import os
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, Literal, TypeGuard, cast

import yaml
from fastapi import Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

import alan.build_chat as build_chat_mod
import alan.explore_chat as explore_chat_mod
import alan.queue as queue_mod
import alan.workspace as workspace_mod
from alan import control_token_store, prep_chat_link, roots
from alan._chat_core import PrepChatRead
from alan.control_plane import CONTROL_TOKEN_HEADER, expected_control_token
from alan.dispatcher import Dispatcher
from alan.errors import AppError
from alan.events import PubSub
from alan.handover import Handover, list_handovers
from alan.queue import Queue
from alan.teams._base import TeamEvent
from alan.workspace_index import WorkspaceIndex
from alan.yaml_normalize import recover_item

logger = logging.getLogger("alan.server")

_DISPATCHER_DISABLED_ENV = "ALAN_DISABLE_DISPATCHER"
_DISPATCHER_CONCURRENCY_ENV = "ALAN_DISPATCHER_CONCURRENCY"
_QUEUE_DB_ENV = "ALAN_QUEUE_DB_PATH"
_TRUTHY = {"1", "true", "yes", "on"}
_DEFAULT_DISPATCHER_CONCURRENCY = 6

SSE_HEADERS = {"X-Accel-Buffering": "no"}
SSE_MEDIA = "text/event-stream"
RAW_OUTPUT_TRUNCATE = 2000

_MISSING_TOKEN_DETAIL = "control token required"  # noqa: S105  # nosec B105
_MISMATCHED_TOKEN_DETAIL = "control token mismatch"  # noqa: S105  # nosec B105
_EXPIRED_TOKEN_DETAIL = "control token expired"  # noqa: S105  # nosec B105
_UNCONFIGURED_TOKEN_DETAIL = "control token not configured"  # noqa: S105  # nosec B105

GUARDED: dict[int | str, dict[str, str]] = {
    401: {"description": "control token missing"},
    403: {"description": "control token mismatch"},
    500: {"description": "control token not configured"},
}
GUARDED_EXPIRABLE: dict[int | str, dict[str, str]] = {
    **GUARDED,
    401: {"description": "control token missing or expired"},
}
ENGINE_NOT_FOUND_DETAIL = "engine not found"


def _is_expired(record: control_token_store.ControlTokenMeta, expected: str) -> bool:
    return (
        record.token is not None
        and hmac.compare_digest(record.token, expected)
        and record.expires_at is not None
        and datetime.now(UTC) >= record.expires_at
    )


def require_control_token(request: Request) -> None:
    expected = expected_control_token()
    if not expected:
        raise HTTPException(status_code=500, detail=_UNCONFIGURED_TOKEN_DETAIL)
    supplied = request.headers.get(CONTROL_TOKEN_HEADER)
    if not supplied:
        raise HTTPException(status_code=401, detail=_MISSING_TOKEN_DETAIL)
    if not hmac.compare_digest(supplied, expected):
        raise HTTPException(status_code=403, detail=_MISMATCHED_TOKEN_DETAIL)
    if _is_expired(control_token_store.current_meta(), expected):
        raise HTTPException(status_code=401, detail=_EXPIRED_TOKEN_DETAIL)


def app_error_handler(_request: Request, exc: Exception) -> JSONResponse:
    error = cast(AppError, exc)
    return JSONResponse(status_code=error.status_code, content={"detail": error.detail})


def dispatcher_disabled() -> bool:
    raw = os.environ.get(_DISPATCHER_DISABLED_ENV, "")
    return raw.strip().lower() in _TRUTHY


def resolve_dispatcher_concurrency() -> int:
    raw = os.environ.get(_DISPATCHER_CONCURRENCY_ENV, "").strip()
    if not raw:
        return _DEFAULT_DISPATCHER_CONCURRENCY
    if not raw.isdecimal() or int(raw) < 1:
        logger.warning(
            "invalid %s=%r; using default %d",
            _DISPATCHER_CONCURRENCY_ENV,
            raw,
            _DEFAULT_DISPATCHER_CONCURRENCY,
        )
        return _DEFAULT_DISPATCHER_CONCURRENCY
    return int(raw)


def resolve_workspaces_root() -> Path:
    return roots.workspaces_root()


def resolve_queue_db() -> Path:
    override = os.environ.get(_QUEUE_DB_ENV)
    return Path(override) if override else queue_mod.QUEUE_DB


def resolve_build_chat_sessions_root() -> Path:
    return build_chat_mod.resolve_sessions_root()


def resolve_explore_chat_sessions_root() -> Path:
    return explore_chat_mod.resolve_sessions_root()


def queue_dep(request: Request) -> Queue:
    return cast(Queue, request.app.state.queue)


def pubsub_dep(request: Request) -> PubSub:
    return cast(PubSub, request.app.state.pubsub)


def dispatcher_dep(request: Request) -> Dispatcher:
    dispatcher = request.app.state.dispatcher
    if dispatcher is None:
        raise HTTPException(status_code=503, detail="dispatcher disabled (ALAN_DISABLE_DISPATCHER)")
    return cast(Dispatcher, dispatcher)


def optional_dispatcher_dep(request: Request) -> Dispatcher | None:
    dispatcher = getattr(request.app.state, "dispatcher", None)
    if dispatcher is None:
        return None
    return cast(Dispatcher, dispatcher)


def workspaces_root_dep(request: Request) -> Path:
    return cast(Path, request.app.state.workspaces_root)


def workspace_index_dep(
    request: Request,
    root: Annotated[Path, Depends(workspaces_root_dep)],
    queue: Annotated[Queue, Depends(queue_dep)],
) -> WorkspaceIndex:
    indexes = cast("dict[Path, WorkspaceIndex]", request.app.state.workspace_indexes)
    index = indexes.get(root)
    if index is None:
        index = WorkspaceIndex(root=root, queue=queue)
        index.rebuild()
        indexes[root] = index
    return index


def build_chat_sessions_root_dep(request: Request) -> Path:
    return cast(Path, request.app.state.build_chat_sessions_root)


def build_chat_locks_dep(request: Request) -> build_chat_mod.BuildChatSessionLocks:
    return cast(build_chat_mod.BuildChatSessionLocks, request.app.state.build_chat_locks)


def explore_chat_sessions_root_dep(request: Request) -> Path:
    return cast(Path, request.app.state.explore_chat_sessions_root)


def explore_chat_locks_dep(request: Request) -> explore_chat_mod.ExploreChatSessionLocks:
    return cast(explore_chat_mod.ExploreChatSessionLocks, request.app.state.explore_chat_locks)


def prep_chat_link_env_dep(
    request: Request,
    build_root: Annotated[Path, Depends(build_chat_sessions_root_dep)],
    explore_root: Annotated[Path, Depends(explore_chat_sessions_root_dep)],
    workspaces_root: Annotated[Path, Depends(workspaces_root_dep)],
    queue: Annotated[Queue, Depends(queue_dep)],
) -> prep_chat_link.LinkEnv:
    return prep_chat_link.LinkEnv(
        build_root=build_root,
        explore_root=explore_root,
        workspaces_root=workspaces_root,
        index=lambda: workspace_index_dep(request, workspaces_root, queue),
    )


QueueDep = Annotated[Queue, Depends(queue_dep)]
PubSubDep = Annotated[PubSub, Depends(pubsub_dep)]
WorkspaceIndexDep = Annotated[WorkspaceIndex, Depends(workspace_index_dep)]
DispatcherDep = Annotated[Dispatcher, Depends(dispatcher_dep)]
OptionalDispatcherDep = Annotated[Dispatcher | None, Depends(optional_dispatcher_dep)]
WorkspacesRootDep = Annotated[Path, Depends(workspaces_root_dep)]
BuildChatSessionsRootDep = Annotated[Path, Depends(build_chat_sessions_root_dep)]
BuildChatLocksDep = Annotated[build_chat_mod.BuildChatSessionLocks, Depends(build_chat_locks_dep)]
ExploreChatSessionsRootDep = Annotated[Path, Depends(explore_chat_sessions_root_dep)]
PrepChatLinkEnvDep = Annotated[prep_chat_link.LinkEnv, Depends(prep_chat_link_env_dep)]
ExploreChatLocksDep = Annotated[
    explore_chat_mod.ExploreChatSessionLocks, Depends(explore_chat_locks_dep)
]


def _empty_prep_chat_reads() -> list[PrepChatRead]:
    return []


class ChatMessageOut(BaseModel):
    role: Literal["user", "assistant"]
    content: str
    ts: datetime
    interrupted: bool = False
    operations: list[str] = Field(default_factory=list)
    prep_chat_reads: list[PrepChatRead] = Field(default_factory=_empty_prep_chat_reads)


class CreateSessionResponse(BaseModel):
    session_id: str


def _is_list(v: Any) -> TypeGuard[list[Any]]:
    return isinstance(v, list)


def require_str_list(value: Any, name: str) -> list[str]:
    if not _is_list(value):
        raise ValueError(f"{name} must be a list of strings")
    items: list[str] = []
    for item in value:
        recovered = recover_item(item)
        if recovered:
            items.append(recovered)
    return items


def format_sse(event: TeamEvent) -> str:
    return f"event: {event.kind}\ndata: {json.dumps(event.payload)}\n\n"


def strip_code_fence(raw: str) -> str:
    text = raw.strip()
    if text.startswith("```"):
        first_nl = text.find("\n")
        if first_nl != -1:
            text = text[first_nl + 1 :]
        if text.endswith("```"):
            text = text[:-3]
    return text.strip()


def extract_yaml_object(raw: str) -> Any:
    text = strip_code_fence(raw)
    if not text:
        raise ValueError("empty response")
    return yaml.safe_load(text)


async def workspace_event_stream(
    task_id: str, pubsub: PubSub, *, filter_task_id: str | None = None
) -> AsyncGenerator[str, None]:
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[TeamEvent] = asyncio.Queue()

    def on_event(event: TeamEvent) -> None:
        if filter_task_id is None or event.payload.get("task_id") == filter_task_id:
            loop.call_soon_threadsafe(queue.put_nowait, event)

    unsubscribe = pubsub.subscribe(task_id, on_event)
    try:
        while True:
            event = await queue.get()
            yield format_sse(event)
    finally:
        unsubscribe()


async def read_workspace_or_404(task_id: str, workspaces_root: Path) -> workspace_mod.Workspace:
    try:
        return await run_in_threadpool(workspace_mod.read, task_id, root=workspaces_root)
    except workspace_mod.WorkspaceError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def latest_handover(ws: workspace_mod.Workspace) -> Handover | None:
    handovers = list_handovers(ws)
    return handovers[-1] if handovers else None


def read_stream_events(path: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for raw in handle:
            line = raw.strip()
            if not line:
                continue
            try:
                parsed: object = json.loads(line)
            except json.JSONDecodeError:
                logger.warning("stream line not JSON in %s", path)
                continue
            if isinstance(parsed, dict):
                events.append(cast(dict[str, Any], parsed))
    return events


def read_history_entries(workspace_path: Path) -> list[dict[str, Any]]:
    path = workspace_path / "history.json"
    if not path.is_file():
        return []
    try:
        with path.open("r", encoding="utf-8") as handle:
            loaded: object = json.load(handle)
    except (OSError, json.JSONDecodeError):
        logger.warning("history.json unreadable at %s", path)
        return []
    if isinstance(loaded, list):
        return cast(list[dict[str, Any]], loaded)
    return []
