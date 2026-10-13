from __future__ import annotations

import asyncio
import functools
import json
import logging
import os
import re
import threading
from collections.abc import AsyncGenerator, AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field, TypeAdapter, ValidationError
from starlette.datastructures import Headers
from starlette.types import ASGIApp, Receive, Scope, Send

import alan.build_chat as build_chat_mod
import alan.collision as collision_mod
import alan.explore_chat as explore_chat_mod
import alan.project as project_mod
import alan.project_amendment as project_amendment_mod
import alan.project_doc as project_doc_mod
import alan.project_roadmap as project_roadmap_mod
from alan import _chat_core, control_plane, host_allowlist, roots
from alan.errors import AppError
from alan.forge import set_active_forge
from alan.forge_gh import GhForge
from alan.routes import _chat_router, _deps
from alan.routes import infra as _infra
from alan.routes._deps import (
    BuildChatSessionsRootDep,
    ExploreChatSessionsRootDep,
    PubSubDep,
    QueueDep,
    WorkspacesRootDep,
    app_error_handler,
)
from alan.routes.answer import (
    answer_router,
    operator_clause_router,
)
from alan.routes.build_chat import (
    build_chat_router,
)
from alan.routes.control_token import (
    control_token_router,
)
from alan.routes.dispatch_instructions import (
    dispatch_instructions_router,
)
from alan.routes.driver_loops import (
    driver_loops_router,
)
from alan.routes.engine_presets import (
    engine_presets_router,
)
from alan.routes.engine_sources import (
    engine_sources_router,
)
from alan.routes.engines import (
    engines_router,
)
from alan.routes.engines_accent import (
    engines_accent_router,
)
from alan.routes.engines_agents import (
    engines_agents_router,
)
from alan.routes.engines_lane import (
    engines_lane_router,
)
from alan.routes.engines_recurring_status import (
    engines_recurring_status_router,
)
from alan.routes.engines_visibility import (
    engines_visibility_router,
)
from alan.routes.events import events_router
from alan.routes.explore_chat import (
    explore_chat_router,
)
from alan.routes.github import (
    github_router,
)
from alan.routes.health import (
    health_router,
)
from alan.routes.infra import (
    infra_router,
)
from alan.routes.onboarding import (
    onboarding_router,
)
from alan.routes.operator_terminal import (
    operator_terminal_router,
)
from alan.routes.pr_attach import (
    pr_attach_router,
)
from alan.routes.projects import (
    projects_router,
)
from alan.routes.scenario_binding import (
    scenario_binding_router,
)
from alan.routes.secrets import (
    secrets_router,
)
from alan.routes.workspaces import (
    tasks_router,
    workspaces_router,
)
from alan.routes.workspaces import (
    workspace_events as workspace_events,
)
from alan.teams._base import TeamEvent
from alan.teams.implementation_plan import run_team

logger = logging.getLogger("alan.server")

_SSE_HEADERS = _deps.SSE_HEADERS
_SSE_MEDIA = _deps.SSE_MEDIA
_build_chat_locks_dep = _deps.build_chat_locks_dep
_build_chat_sessions_root_dep = _deps.build_chat_sessions_root_dep
_dispatcher_dep = _deps.dispatcher_dep
_explore_chat_locks_dep = _deps.explore_chat_locks_dep
_explore_chat_sessions_root_dep = _deps.explore_chat_sessions_root_dep
_extract_yaml_object = _deps.extract_yaml_object
_format_sse = _deps.format_sse
_optional_dispatcher_dep = _deps.optional_dispatcher_dep
_pubsub_dep = _deps.pubsub_dep
_queue_dep = _deps.queue_dep
_read_history_entries = _deps.read_history_entries
_read_workspace_or_404 = _deps.read_workspace_or_404
_resolve_dispatcher_concurrency = _deps.resolve_dispatcher_concurrency
_strip_code_fence = _deps.strip_code_fence
_workspace_event_stream = _deps.workspace_event_stream
_workspaces_root_dep = _deps.workspaces_root_dep
_infra_lifespan = _infra.lifespan
_looks_like_spa_route = _infra.looks_like_spa_route
_mount_ui = _infra.mount_ui

__all__ = [
    "health_router",
    "_build_chat_locks_dep",
    "_build_chat_sessions_root_dep",
    "_explore_chat_locks_dep",
    "_explore_chat_sessions_root_dep",
    "_SSE_HEADERS",
    "_SSE_MEDIA",
    "_dispatcher_dep",
    "_optional_dispatcher_dep",
    "_extract_yaml_object",
    "_format_sse",
    "_looks_like_spa_route",
    "_mount_ui",
    "_pubsub_dep",
    "_queue_dep",
    "_read_history_entries",
    "_read_workspace_or_404",
    "_resolve_dispatcher_concurrency",
    "_strip_code_fence",
    "_workspace_event_stream",
    "_workspaces_root_dep",
    "workspace_events",
]

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    set_active_forge(GhForge())
    control_plane.bootstrap_control_token(roots.state_root())
    try:
        async with _infra_lifespan(app):
            yield
    finally:
        control_plane.clear_control_token()


app = FastAPI(title="alan", lifespan=lifespan)
app.add_exception_handler(AppError, app_error_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["DELETE", "GET", "POST", "PUT", "OPTIONS"],
    allow_headers=["Content-Type"],
)


def openapi_json() -> str:
    return json.dumps(app.openapi(), sort_keys=True, indent=2) + "\n"


class PlanBody(BaseModel):
    title: str = Field(default="", max_length=2000)
    description: str = Field(default="", max_length=20000)


class CollisionCheckBody(BaseModel):
    brief: str = Field(min_length=1, max_length=20000)
    chat_family: Literal["build", "explore"] | None = None
    session_id: str | None = None
    exclude_task_id: str | None = Field(default=None, max_length=200)
    engine_key: str | None = Field(default=None, min_length=1, max_length=200)


async def _plan_event_stream(title: str, description: str) -> AsyncIterator[str]:
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[TeamEvent | None] = asyncio.Queue()
    closed = threading.Event()

    runner = asyncio.create_task(
        run_in_threadpool(run_team, title, description, _make_emit(loop, queue, closed))
    )
    runner.add_done_callback(functools.partial(_signal_done, loop, queue))

    try:
        while True:
            event = await queue.get()
            if event is None:
                return
            yield _format_sse(event)
    finally:
        closed.set()
        runner.cancel()


def _make_emit(
    loop: asyncio.AbstractEventLoop,
    queue: asyncio.Queue[TeamEvent | None],
    closed: threading.Event,
) -> Callable[[TeamEvent], None]:
    def emit(event: TeamEvent) -> None:
        if closed.is_set():
            return
        loop.call_soon_threadsafe(queue.put_nowait, event)

    return emit


def _signal_done(
    loop: asyncio.AbstractEventLoop,
    queue: asyncio.Queue[TeamEvent | None],
    task: asyncio.Task[Any],
) -> None:
    try:
        exc = task.exception()
    except asyncio.CancelledError:
        exc = None
    if exc is not None:
        loop.call_soon_threadsafe(
            queue.put_nowait,
            TeamEvent("error", {"role": "team", "detail": str(exc)}),
        )
    loop.call_soon_threadsafe(queue.put_nowait, None)


@app.post(
    "/api/plan",
    responses={400: {"description": "title or description required"}},
)
async def plan_endpoint(body: PlanBody) -> StreamingResponse:
    if not body.title.strip() and not body.description.strip():
        raise AppError(status_code=400, detail="title or description required")
    return StreamingResponse(
        _plan_event_stream(body.title, body.description),
        media_type=_SSE_MEDIA,
        headers=_SSE_HEADERS,
    )


_COLLISION_CHAT_CONFIGS: dict[str, _chat_core.ChatCoreConfig[Any]] = {
    "build": build_chat_mod.CONFIG,
    "explore": explore_chat_mod.CONFIG,
}


def _resolve_collision_stream_path(
    body: CollisionCheckBody,
    build_chat_root: Path,
    explore_chat_root: Path,
) -> Path | None:
    if body.chat_family is None or body.session_id is None:
        return None
    cfg = _COLLISION_CHAT_CONFIGS[body.chat_family]
    root = build_chat_root if body.chat_family == "build" else explore_chat_root
    try:
        _chat_core.load_session(cfg, body.session_id, root=root)
        return _chat_core.collision_stream_path(cfg, body.session_id, root=root)
    except _chat_core.ChatCoreError:
        return None


def _resolve_collision_scope_engine_key(
    body: CollisionCheckBody, explore_chat_root: Path
) -> str | None:
    if body.chat_family != "explore" or body.session_id is None:
        return body.engine_key
    try:
        session = explore_chat_mod.load_session(body.session_id, root=explore_chat_root)
    except explore_chat_mod.ExploreChatError:
        return None
    return session.engine_key


@app.post("/api/collision-check")
async def collision_check_endpoint(
    body: CollisionCheckBody,
    workspaces_root: WorkspacesRootDep,
    build_chat_root: BuildChatSessionsRootDep,
    explore_chat_root: ExploreChatSessionsRootDep,
) -> _chat_router.CollisionVerdictOut:
    try:
        stream_path = _resolve_collision_stream_path(body, build_chat_root, explore_chat_root)
        scope_engine_key = await run_in_threadpool(
            _resolve_collision_scope_engine_key, body, explore_chat_root
        )
        result = await run_in_threadpool(
            collision_mod.check_collision,
            body.brief,
            workspaces_root=workspaces_root,
            stream_path=stream_path,
            exclude_task_id=body.exclude_task_id,
            scope_engine_key=scope_engine_key,
        )
        return _chat_router.collision_verdict_out(result)
    except Exception:  # noqa: BLE001
        logger.warning("collision_check_endpoint: check_collision raised", exc_info=True)
        return _chat_router.collision_verdict_out(collision_mod.SAFE_RESULT)


class AmendmentApprovalResponse(BaseModel):
    fingerprint: str
    project_id: str
    approved_at: str


_NO_PENDING_AMENDMENT_DETAIL = "no pending amendment for this project/fingerprint"
_ROADMAP_AMENDMENT_PAYLOAD_INVALID_DETAIL = "malformed roadmap amendment payload"

_AMENDMENT_APPROVE_RESPONSES: dict[int | str, dict[str, str]] = {
    404: {"description": _NO_PENDING_AMENDMENT_DETAIL},
    409: {
        "description": (
            "roadmap amendment could not be applied: payload no longer matches the "
            "roadmap state, or is malformed"
        )
    },
    422: {"description": "fingerprint must be a 16-character lowercase hex string"},
}


_ORDERED_IDS_ADAPTER = TypeAdapter(list[str])


class _RoadmapEditPayload(BaseModel):
    item_id: str = Field(min_length=1)
    title: str | None = None
    text: str | None = None


def _roadmap_reorder_ids(payload: dict[str, Any]) -> list[str]:
    try:
        return _ORDERED_IDS_ADAPTER.validate_python(payload.get("ordered_ids"))
    except ValidationError as exc:
        raise project_roadmap_mod.RoadmapError(
            _ROADMAP_AMENDMENT_PAYLOAD_INVALID_DETAIL
        ) from exc


def _roadmap_edit_fields(payload: dict[str, Any]) -> tuple[str, str | None, str | None]:
    try:
        parsed = _RoadmapEditPayload.model_validate(payload)
    except ValidationError as exc:
        raise project_roadmap_mod.RoadmapError(
            _ROADMAP_AMENDMENT_PAYLOAD_INVALID_DETAIL
        ) from exc
    return parsed.item_id, parsed.title, parsed.text


def _apply_amendment(
    project: project_mod.Project, claimed: project_amendment_mod.PendingAmendment
) -> None:
    if claimed.kind == "roadmap_reorder":
        project_roadmap_mod.reorder_items(project.id, _roadmap_reorder_ids(claimed.payload))
    elif claimed.kind == "roadmap_edit":
        item_id, title, text = _roadmap_edit_fields(claimed.payload)
        project_roadmap_mod.edit_item(project.id, item_id, title=title, text=text)
    else:
        project_doc_mod.apply_vision_scope_amendment(
            project,
            vision=claimed.vision,
            scope=claimed.scope,
            decision_entry=claimed.decision_entry,
        )


@projects_router.post(
    "/{project_id}/amendments/{fingerprint}/approve", responses=_AMENDMENT_APPROVE_RESPONSES
)
async def approve_project_amendment(
    project_id: str, fingerprint: str, queue: QueueDep, pubsub: PubSubDep
) -> AmendmentApprovalResponse:
    project_amendment_mod.require_valid_fingerprint(fingerprint)
    resolved = await run_in_threadpool(
        project_amendment_mod.resolve_pending_amendment, queue, fingerprint
    )
    if resolved is None:
        raise AppError(status_code=404, detail=_NO_PENDING_AMENDMENT_DETAIL)
    _record, project = resolved
    if project.id != project_id:
        raise AppError(status_code=404, detail=_NO_PENDING_AMENDMENT_DETAIL)
    # Atomically claim (pop) the pending record before mutating so that a
    # concurrent, duplicate approve for the same fingerprint always loses this
    # race and 404s instead of applying the amendment a second time.
    claimed = await run_in_threadpool(
        project_amendment_mod.claim_pending_amendment, queue, fingerprint
    )
    if claimed is None:
        raise AppError(status_code=404, detail=_NO_PENDING_AMENDMENT_DETAIL)
    now = datetime.now(UTC)
    # Approval is recorded as a durable audit row BEFORE the mutation below runs —
    # this ordering is what makes "written by exactly one audited path" provable.
    await run_in_threadpool(
        project_amendment_mod.record_amendment_approval, fingerprint, claimed, now=now
    )
    try:
        await run_in_threadpool(_apply_amendment, project, claimed)
    except project_roadmap_mod.RoadmapError as exc:
        raise AppError(status_code=409, detail=str(exc)) from exc
    pubsub.publish_global(TeamEvent("project_amendment_approved", {"fingerprint": fingerprint}))
    return AmendmentApprovalResponse(
        fingerprint=fingerprint, project_id=project_id, approved_at=now.isoformat()
    )


app.include_router(events_router)
app.include_router(infra_router)
app.include_router(health_router)
app.include_router(driver_loops_router)
app.include_router(answer_router)
app.include_router(operator_clause_router)
app.include_router(build_chat_router)
app.include_router(control_token_router)
app.include_router(explore_chat_router)
app.include_router(projects_router)
app.include_router(secrets_router)
app.include_router(github_router)
app.include_router(engines_router)
app.include_router(engines_accent_router)
app.include_router(engines_agents_router)
app.include_router(engines_lane_router)
app.include_router(engines_recurring_status_router)
app.include_router(engines_visibility_router)
app.include_router(engine_presets_router)
app.include_router(engine_sources_router)
app.include_router(onboarding_router)
app.include_router(operator_terminal_router)
app.include_router(workspaces_router)
app.include_router(tasks_router)
app.include_router(dispatch_instructions_router)
app.include_router(scenario_binding_router)
app.include_router(pr_attach_router)


_HOST_HEADER_RE = re.compile(
    r"^(?:\[(?P<v6>[0-9A-Fa-f:.]+)\]|(?P<name>[A-Za-z0-9.-]+))(?::(?P<port>\d+))?$"
)
_SERIALIZED_ORIGIN_RE = re.compile(
    r"https?://(?:\[(?P<v6>[0-9a-f:.]+)\]|(?P<name>[a-z0-9.-]+))(?::(?P<port>\d{1,5}))?",
    re.IGNORECASE,
)
_MAX_PORT = 65535
_HOST_REFUSED_RESPONSE = JSONResponse({"detail": "host not allowed"}, status_code=400)
_ORIGIN_REFUSED_RESPONSE = JSONResponse({"detail": "origin not allowed"}, status_code=403)
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
_SAME_ORIGIN_FETCH_SITES = frozenset({"same-origin", "none"})


def _host_allowed(raw: str) -> bool:
    match = _HOST_HEADER_RE.fullmatch(raw)
    if match is None:
        return False
    hostname = match["v6"] or match["name"]
    return hostname.lower() in host_allowlist.allowed_hostnames()


def _serialized_origin_hostname(origin: str) -> str | None:
    match = _SERIALIZED_ORIGIN_RE.fullmatch(origin)
    if match is None:
        return None
    port = match["port"]
    if port is not None and not 1 <= int(port) <= _MAX_PORT:
        return None
    return match["v6"] or match["name"]


def _origin_allowed(headers: Headers) -> bool:
    origins = headers.getlist("origin")
    if len(origins) != 1:
        return False
    hostname = _serialized_origin_hostname(origins[0])
    return hostname is not None and hostname.lower() in host_allowlist.allowed_hostnames()


def _fetch_site_allowed(headers: Headers) -> bool:
    sites = headers.getlist("sec-fetch-site")
    if not sites:
        return True
    return len(sites) == 1 and sites[0] in _SAME_ORIGIN_FETCH_SITES


def _cross_site_write(scope: Scope, headers: Headers) -> bool:
    if scope["method"] in _SAFE_METHODS:
        return False
    if "origin" in headers:
        return not _origin_allowed(headers)
    return not _fetch_site_allowed(headers)


def _refusal(scope: Scope, headers: Headers) -> JSONResponse | None:
    hosts = headers.getlist("host")
    if len(hosts) != 1 or not _host_allowed(hosts[0]):
        return _HOST_REFUSED_RESPONSE
    if scope["type"] == "websocket":
        return None if _origin_allowed(headers) else _ORIGIN_REFUSED_RESPONSE
    return _ORIGIN_REFUSED_RESPONSE if _cross_site_write(scope, headers) else None


class HostAllowlistGuard:
    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "lifespan":
            await self._app(scope, receive, send)
            return
        refusal = _refusal(scope, Headers(scope=scope))
        if refusal is not None:
            await refusal(scope, receive, send)
            return
        await self._app(scope, receive, send)


def _dist_dir() -> Path:
    override = os.environ.get("ALAN_WEB_DIST_DIR", "").strip()
    return Path(override) if override else Path(__file__).parent / "web" / "dist"


_mount_ui(app, _dist_dir())

served_app = HostAllowlistGuard(app)
