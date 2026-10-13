from __future__ import annotations

import logging
import re
from collections.abc import AsyncGenerator, Awaitable, Callable
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from starlette.responses import Response

import alan.build_chat as build_chat_mod
import alan.collision as collision_mod
import alan.explore_chat_repo as chat_repo_mod
import alan.project_doc as project_doc_mod
import alan.shop_stats as shop_stats_mod
from alan import _chat_core, agent_key
from alan.claude_cli import BUILD_SUMMARIZE_SENTINEL, ClaudeCliError, format_build_system_prompt
from alan.events import PubSub
from alan.predispatch_scan import scan_predispatch
from alan.routes import _chat_router
from alan.routes._chat_router import (
    ChatRouterCtx,
    PutBriefBody,
    SendMessageBody,
)
from alan.routes._deps import (
    SSE_HEADERS,
    SSE_MEDIA,
    BuildChatLocksDep,
    BuildChatSessionsRootDep,
    ChatMessageOut,
    CreateSessionResponse,
    PrepChatLinkEnvDep,
    PubSubDep,
    QueueDep,
    WorkspaceIndexDep,
    WorkspacesRootDep,
)
from alan.routes._prep_chat_redirect import (
    ARCHIVED_REDIRECT_RESPONSE,
    archived_prep_chat_redirect,
)
from alan.run_summary import RunSummary
from alan.scope_audit import ScopeGapFinding

__all__ = [
    "BuildChatSessionSummaryOut",
    "_clear_best_effort",
    "_event_stream",
    "build_chat_events",
    "build_chat_router",
]

logger = logging.getLogger("alan.server")

_LABEL = "build-chat"

_FROZEN_STATUSES = frozenset({
    build_chat_mod.RepoProvisionStatus.READY,
    build_chat_mod.RepoProvisionStatus.NOT_NEEDED,
})


def _build_pre_turn(session: build_chat_mod.BuildChatSession, root: Path) -> None:
    if session.repo_provision in _FROZEN_STATUSES:
        return
    engine_key = chat_repo_mod.needs_repo(session.engine_key)
    if engine_key is None:
        build_chat_mod.set_repo_provision(
            session.session_id, build_chat_mod.RepoProvisionStatus.NOT_NEEDED, root=root
        )
        return
    build_chat_mod.set_repo_provision(
        session.session_id, build_chat_mod.RepoProvisionStatus.PENDING, root=root
    )
    dest = build_chat_mod.repo_dir(session.session_id, root=root)
    try:
        chat_repo_mod.provision(engine_key, dest)
    except chat_repo_mod.RepoProvisionError as exc:
        build_chat_mod.set_repo_provision(
            session.session_id, build_chat_mod.RepoProvisionStatus.ERROR, root=root
        )
        raise ClaudeCliError(str(exc)) from exc
    build_chat_mod.set_repo_provision(
        session.session_id, build_chat_mod.RepoProvisionStatus.READY, root=root
    )


def _build_resolve_cwd(session_id: str, root: Path) -> Path | None:
    session = build_chat_mod.load_session(session_id, root=root)
    if session.repo_provision == build_chat_mod.RepoProvisionStatus.READY:
        return build_chat_mod.repo_dir(session_id, root=root)
    return None


_CTX: ChatRouterCtx[build_chat_mod.BuildChatSession] = ChatRouterCtx(
    core=build_chat_mod.CONFIG,
    channel_prefix="build_chat",
    label=_LABEL,
    summarize_sentinel=BUILD_SUMMARIZE_SENTINEL,
    system_prompt=lambda session: format_build_system_prompt(
        repo_label=chat_repo_mod.repo_label(session.engine_key)
    ),
    agent=lambda _session: agent_key.BUILD_CHAT,
    pre_turn=_build_pre_turn,
    resolve_cwd=_build_resolve_cwd,
)

def _prompt_with_shop_stats(
    shop_stats: str,
) -> Callable[[build_chat_mod.BuildChatSession], str]:
    def _prompt(session: build_chat_mod.BuildChatSession) -> str:
        return format_build_system_prompt(shop_stats, chat_repo_mod.repo_label(session.engine_key))

    return _prompt


def _ctx_with_shop_stats(shop_stats: str) -> ChatRouterCtx[build_chat_mod.BuildChatSession]:
    return replace(_CTX, system_prompt=_prompt_with_shop_stats(shop_stats))


_NOT_FOUND = _chat_router.not_found_responses(_LABEL)
_TURN_RESPONSES = _chat_router.turn_responses(_LABEL)
_INTERRUPT_RESPONSES = _chat_router.interrupt_responses(_LABEL)
_CLI_RESPONSES = _chat_router.cli_responses(_LABEL)
_PROPOSE_RESPONSES: dict[int | str, dict[str, str]] = {
    404: {"description": f"{_LABEL} session not found"},
    409: {"description": "chat turn already in progress"},
    422: {"description": _chat_router.DISPATCH_PROPOSAL_PARSE_DETAIL},
    502: {"description": "claude CLI failed"},
}

_SCAN_TITLE_MAX = 50
_HEADING_RE = re.compile(r"^#+\s*")


def _derive_scan_title(description: str) -> str:
    first_line = next((line.strip() for line in description.splitlines() if line.strip()), "")
    stripped = _HEADING_RE.sub("", first_line)[:_SCAN_TITLE_MAX]
    return stripped or "build"


class CreateBuildChatBody(BaseModel):
    engine_key: str = Field(min_length=1, max_length=200)
    project_id: str | None = Field(default=None, min_length=1, max_length=200)


class BuildChatSessionSummaryOut(BaseModel):
    session_id: str
    created_at: datetime
    last_message_preview: str
    in_flight: bool
    summary_ready: bool
    engine_key: str | None


class BuildChatSessionResponse(BaseModel):
    session_id: str
    project_id: str | None
    engine_key: str | None
    repo_provision: build_chat_mod.RepoProvisionStatus
    created_at: datetime
    messages: list[ChatMessageOut]
    in_flight_turn: _chat_core.ChatInFlightTurn | None = None
    brief: build_chat_mod.BuildChatBrief | None = None
    run_summary: RunSummary
    collision_run_summary: RunSummary | None = None


def _to_response(
    session: build_chat_mod.BuildChatSession,
    brief: build_chat_mod.BuildChatBrief | None,
    run_summary: RunSummary,
    collision_run_summary: RunSummary | None,
) -> BuildChatSessionResponse:
    return BuildChatSessionResponse(
        session_id=session.session_id,
        project_id=session.project_id,
        engine_key=session.engine_key,
        repo_provision=session.repo_provision,
        created_at=session.created_at,
        messages=[
            ChatMessageOut.model_validate(m, from_attributes=True) for m in session.transcript
        ],
        in_flight_turn=session.in_flight_turn,
        brief=brief,
        run_summary=run_summary,
        collision_run_summary=collision_run_summary,
    )


def _event_stream(session_id: str, pubsub: PubSub) -> AsyncGenerator[str, None]:
    return _chat_router.event_stream(_CTX, session_id, pubsub)


def _clear_best_effort(session_id: str, root: Path) -> None:
    _chat_router.clear_best_effort(_CTX, session_id, root)


build_chat_router = APIRouter(prefix="/api/build-chat")


@build_chat_router.get("/sessions")
async def list_build_chat_sessions(
    root: BuildChatSessionsRootDep,
    project_id: Annotated[str | None, Query(min_length=1, max_length=200)] = None,
    engine_key: Annotated[str | None, Query(min_length=1, max_length=200)] = None,
) -> list[BuildChatSessionSummaryOut]:
    def _list() -> list[BuildChatSessionSummaryOut]:
        summaries = build_chat_mod.list_sessions(
            root=root, project_id=project_id, engine_key=engine_key
        )
        return [
            BuildChatSessionSummaryOut(
                session_id=s.session_id,
                created_at=s.created_at,
                last_message_preview=s.last_message_preview,
                in_flight=s.in_flight,
                summary_ready=build_chat_mod.load_brief(s.session_id, root=root) is not None,
                engine_key=s.engine_key,
            )
            for s in summaries
        ]
    return await run_in_threadpool(_list)


@build_chat_router.post("/sessions", status_code=201)
async def create_build_chat_session(
    body: CreateBuildChatBody,
    root: BuildChatSessionsRootDep,
) -> CreateSessionResponse:
    await run_in_threadpool(
        _chat_router.validate_create_binding, body.engine_key, body.project_id
    )
    return await _chat_router.run_create(
        _CTX, root, project_id=body.project_id, engine_key=body.engine_key
    )


@build_chat_router.get(
    "/sessions/{session_id}",
    response_model=BuildChatSessionResponse,
    responses={**_NOT_FOUND, **ARCHIVED_REDIRECT_RESPONSE},
)
async def get_build_chat_session(
    session_id: str,
    root: BuildChatSessionsRootDep,
    queue: QueueDep,
    index: WorkspaceIndexDep,
) -> BuildChatSessionResponse | Response:
    shop_stats = await run_in_threadpool(shop_stats_mod.assemble_shop_stats_block, queue)
    ctx = _ctx_with_shop_stats(shop_stats)
    try:
        session = await run_in_threadpool(_chat_router.load_or_404, ctx, session_id, root)
    except HTTPException as exc:
        if exc.status_code != 404:
            raise
        redirect = await archived_prep_chat_redirect(session_id, index)
        if redirect is None:
            raise
        return redirect
    brief = await run_in_threadpool(build_chat_mod.load_brief, session_id, root=root)
    summary = await run_in_threadpool(_chat_router.compute_run_summary, ctx, session, root)
    collision_summary = await run_in_threadpool(
        _chat_router.compute_collision_run_summary, ctx, session, root
    )
    return _to_response(session, brief, summary, collision_summary)


@build_chat_router.delete("/sessions/{session_id}", status_code=204, responses=_NOT_FOUND)
async def delete_build_chat_session(
    session_id: str, root: BuildChatSessionsRootDep
) -> Response:
    return await _chat_router.run_delete(_CTX, session_id, root)


@build_chat_router.get("/sessions/{session_id}/events", responses=_NOT_FOUND)
async def build_chat_events(
    session_id: str, root: BuildChatSessionsRootDep, pubsub: PubSubDep
) -> StreamingResponse:
    await run_in_threadpool(_chat_router.load_or_404, _CTX, session_id, root)
    return StreamingResponse(
        _event_stream(session_id, pubsub), media_type=SSE_MEDIA, headers=SSE_HEADERS
    )


@build_chat_router.post(
    "/sessions/{session_id}/messages", status_code=202, responses=_TURN_RESPONSES
)
async def send_build_chat_message(
    session_id: str,
    body: SendMessageBody,
    root: BuildChatSessionsRootDep,
    pubsub: PubSubDep,
    locks: BuildChatLocksDep,
    queue: QueueDep,
    link_env: PrepChatLinkEnvDep,
) -> _chat_router.ChatAck:
    shop_stats = await run_in_threadpool(shop_stats_mod.assemble_shop_stats_block, queue)
    return await _chat_router.start_message_turn(
        _ctx_with_shop_stats(shop_stats),
        session_id,
        body.content,
        root,
        pubsub,
        locks,
        _chat_router.link_preparer(link_env),
    )


@build_chat_router.post(
    "/sessions/{session_id}/interrupt", status_code=204, responses=_INTERRUPT_RESPONSES
)
async def interrupt_build_chat_session(
    session_id: str, root: BuildChatSessionsRootDep, locks: BuildChatLocksDep
) -> Response:
    return await _chat_router.run_interrupt(_CTX, session_id, root, locks)


@build_chat_router.post("/sessions/{session_id}/summarize", responses=_CLI_RESPONSES)
async def summarize_build_chat(
    session_id: str,
    root: BuildChatSessionsRootDep,
    pubsub: PubSubDep,
    locks: BuildChatLocksDep,
    queue: QueueDep,
) -> build_chat_mod.BuildChatBrief:
    shop_stats = await run_in_threadpool(shop_stats_mod.assemble_shop_stats_block, queue)
    return await _chat_router.run_summarize(
        _ctx_with_shop_stats(shop_stats), session_id, root, pubsub, locks
    )


async def _resolve_build_anti_scope(project_id: str | None) -> str | None:
    if not project_id:
        return None
    try:
        return await run_in_threadpool(project_doc_mod.read_scope_section, project_id)
    except Exception:  # noqa: BLE001 - anti-scope resolution must not surface as 500
        logger.warning("propose_build_dispatch: anti-scope resolution failed", exc_info=True)
        return None


def _build_dispatch_scan(
    session_id: str,
    root: Path,
    project_id: str | None,
    engine_key: str | None,
    workspaces_root: Path,
) -> Callable[[], Awaitable[list[ScopeGapFinding]]]:
    async def scan() -> list[ScopeGapFinding]:
        brief = await run_in_threadpool(build_chat_mod.load_brief, session_id, root=root)
        description = brief.content if brief is not None else ""
        anti_scope = await _resolve_build_anti_scope(project_id)
        return await run_in_threadpool(
            scan_predispatch,
            _derive_scan_title(description),
            description,
            workspaces_root=workspaces_root,
            exclude_task_id=None,
            anti_scope=anti_scope,
            scope_engine_key=engine_key,
        )

    return scan


def _build_dispatch_collision(
    session_id: str,
    root: Path,
    workspaces_root: Path,
) -> Callable[[], Awaitable[collision_mod.CollisionResult]]:
    async def collision() -> collision_mod.CollisionResult:
        brief = await run_in_threadpool(build_chat_mod.load_brief, session_id, root=root)
        description = brief.content if brief is not None else ""
        stream_path = _chat_core.collision_stream_path(build_chat_mod.CONFIG, session_id, root=root)
        return await run_in_threadpool(
            collision_mod.check_collision,
            description,
            workspaces_root=workspaces_root,
            stream_path=stream_path,
        )

    return collision


def _require_build_proposal_title(
    result: _chat_router.DispatchProposalReady | _chat_router.DispatchProposalNeedsInfo,
) -> _chat_router.DispatchProposalReady | _chat_router.DispatchProposalNeedsInfo:
    if isinstance(result, _chat_router.DispatchProposalReady) and not (
        result.proposal.title or ""
    ).strip():
        raise HTTPException(
            status_code=422,
            detail={
                "detail": _chat_router.DISPATCH_PROPOSAL_PARSE_DETAIL,
                "reason": "build dispatch requires a non-empty proposal.title",
            },
        )
    return result


@build_chat_router.post("/sessions/{session_id}/propose", responses=_PROPOSE_RESPONSES)
async def propose_build_dispatch(
    session_id: str,
    root: BuildChatSessionsRootDep,
    workspaces_root: WorkspacesRootDep,
    pubsub: PubSubDep,
    locks: BuildChatLocksDep,
    queue: QueueDep,
) -> _chat_router.DispatchProposalReady | _chat_router.DispatchProposalNeedsInfo:
    session = await run_in_threadpool(_chat_router.load_or_404, _CTX, session_id, root)
    shop_stats = await run_in_threadpool(shop_stats_mod.assemble_shop_stats_block, queue)
    result = await _chat_router.run_dispatch_proposal(
        ctx=_ctx_with_shop_stats(shop_stats),
        session=session,
        root=root,
        pubsub=pubsub,
        locks=locks,
        scan=_build_dispatch_scan(
            session_id, root, session.project_id, session.engine_key, workspaces_root
        ),
        collision=_build_dispatch_collision(session_id, root, workspaces_root),
    )
    return _require_build_proposal_title(result)


@build_chat_router.put("/sessions/{session_id}/brief", responses=_NOT_FOUND)
async def upsert_build_chat_brief(
    session_id: str, body: PutBriefBody, root: BuildChatSessionsRootDep
) -> build_chat_mod.BuildChatBrief:
    return await _chat_router.run_put_brief(_CTX, session_id, body.content, root)


@build_chat_router.get("/sessions/{session_id}/brief", responses=_NOT_FOUND)
async def get_build_chat_brief(
    session_id: str, root: BuildChatSessionsRootDep
) -> build_chat_mod.BuildChatBrief | None:
    return await _chat_router.run_get_brief(_CTX, session_id, root)
