from __future__ import annotations

import re
from collections.abc import AsyncGenerator, Awaitable, Callable
from datetime import datetime
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator
from starlette.responses import Response

import alan.collision as collision_mod
import alan.explore_chat as explore_chat_mod
import alan.explore_chat_repo as explore_chat_repo_mod
from alan import _chat_core, agent_key
from alan.claude_cli import (
    CONSULTANTS,
    EXPLORE_SUMMARIZE_SENTINEL,
    ClaudeCliError,
    format_explore_system_prompt,
    get_consultant,
)
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
    ChatMessageOut,
    CreateSessionResponse,
    ExploreChatLocksDep,
    ExploreChatSessionsRootDep,
    PrepChatLinkEnvDep,
    PubSubDep,
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
    "ExploreChatSessionSummaryOut",
    "_clear_best_effort",
    "_event_stream",
    "explore_chat_events",
    "explore_chat_router",
    "propose_explore_dispatch",
]

_LABEL = "explore-chat"

_FROZEN_STATUSES = frozenset({
    explore_chat_mod.RepoProvisionStatus.READY,
    explore_chat_mod.RepoProvisionStatus.NOT_NEEDED,
})


def _explore_pre_turn(session: explore_chat_mod.ExploreChatSession, root: Path) -> None:
    if session.repo_provision in _FROZEN_STATUSES:
        return
    engine_key = explore_chat_repo_mod.needs_repo(session.engine_key)
    if engine_key is None:
        explore_chat_mod.set_repo_provision(
            session.session_id, explore_chat_mod.RepoProvisionStatus.NOT_NEEDED, root=root
        )
        return
    explore_chat_mod.set_repo_provision(
        session.session_id, explore_chat_mod.RepoProvisionStatus.PENDING, root=root
    )
    dest = explore_chat_mod.repo_dir(session.session_id, root=root)
    try:
        explore_chat_repo_mod.provision(engine_key, dest)
    except explore_chat_repo_mod.RepoProvisionError as exc:
        explore_chat_mod.set_repo_provision(
            session.session_id, explore_chat_mod.RepoProvisionStatus.ERROR, root=root
        )
        raise ClaudeCliError(str(exc)) from exc
    explore_chat_mod.set_repo_provision(
        session.session_id, explore_chat_mod.RepoProvisionStatus.READY, root=root
    )


def _explore_resolve_cwd(session_id: str, root: Path) -> Path | None:
    session = explore_chat_mod.load_session(session_id, root=root)
    if session.repo_provision == explore_chat_mod.RepoProvisionStatus.READY:
        return explore_chat_mod.repo_dir(session_id, root=root)
    return None


_CTX: ChatRouterCtx[explore_chat_mod.ExploreChatSession] = ChatRouterCtx(
    core=explore_chat_mod.CONFIG,
    channel_prefix="explore_chat",
    label=_LABEL,
    summarize_sentinel=EXPLORE_SUMMARIZE_SENTINEL,
    system_prompt=lambda session: format_explore_system_prompt(session.consultant_key),
    agent=lambda session: agent_key.explore_chat(session.consultant_key),
    pre_turn=_explore_pre_turn,
    resolve_cwd=_explore_resolve_cwd,
)

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
    return stripped or "explore"


class ExploreChatSessionSummaryOut(BaseModel):
    session_id: str
    created_at: datetime
    last_message_preview: str
    in_flight: bool
    summary_ready: bool
    consultant_key: str
    engine_key: str | None


class ExploreChatSessionResponse(BaseModel):
    session_id: str
    consultant_key: str
    project_id: str | None
    engine_key: str | None
    repo_provision: explore_chat_mod.RepoProvisionStatus
    created_at: datetime
    messages: list[ChatMessageOut]
    in_flight_turn: explore_chat_mod.ExploreChatInFlightTurn | None = None
    brief: explore_chat_mod.ExploreChatBrief | None = None
    run_summary: RunSummary


class CreateExploreChatBody(BaseModel):
    consultant_key: str = Field(min_length=1, pattern=r"^[a-z][a-z0-9_]{0,63}$")
    engine_key: str = Field(min_length=1, max_length=200)
    project_id: str | None = Field(default=None, min_length=1, max_length=200)

    @field_validator("consultant_key")
    @classmethod
    def _check_known_consultant(cls, v: str) -> str:
        valid = {c.key for c in CONSULTANTS}
        if v not in valid:
            raise ValueError(f"unknown consultant key: {v!r}")
        return v


class ConsultantOut(BaseModel):
    key: str
    label: str
    description: str
    team: str


def _to_response(
    session: explore_chat_mod.ExploreChatSession,
    brief: explore_chat_mod.ExploreChatBrief | None,
    run_summary: RunSummary,
) -> ExploreChatSessionResponse:
    return ExploreChatSessionResponse(
        session_id=session.session_id,
        consultant_key=session.consultant_key,
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
    )


def _event_stream(session_id: str, pubsub: PubSub) -> AsyncGenerator[str, None]:
    return _chat_router.event_stream(_CTX, session_id, pubsub)


def _clear_best_effort(session_id: str, root: Path) -> None:
    _chat_router.clear_best_effort(_CTX, session_id, root)


def _explore_dispatch_scan(
    session_id: str,
    root: Path,
    workspaces_root: Path,
    engine_key: str | None,
) -> Callable[[], Awaitable[list[ScopeGapFinding]]]:
    async def scan() -> list[ScopeGapFinding]:
        brief = await run_in_threadpool(explore_chat_mod.load_brief, session_id, root=root)
        description = brief.content if brief is not None else ""
        return await run_in_threadpool(
            scan_predispatch,
            _derive_scan_title(description),
            description,
            workspaces_root=workspaces_root,
            exclude_task_id=None,
            scope_engine_key=engine_key,
        )

    return scan


def _explore_dispatch_collision(
    session_id: str,
    root: Path,
    workspaces_root: Path,
    engine_key: str | None,
) -> Callable[[], Awaitable[collision_mod.CollisionResult]]:
    async def collision() -> collision_mod.CollisionResult:
        brief = await run_in_threadpool(explore_chat_mod.load_brief, session_id, root=root)
        description = brief.content if brief is not None else ""
        stream_path = _chat_core.collision_stream_path(
            explore_chat_mod.CONFIG, session_id, root=root
        )
        return await run_in_threadpool(
            collision_mod.check_collision,
            description,
            workspaces_root=workspaces_root,
            stream_path=stream_path,
            scope_engine_key=engine_key,
        )

    return collision


def _require_explore_proposal_title(
    result: _chat_router.DispatchProposalReady | _chat_router.DispatchProposalNeedsInfo,
) -> _chat_router.DispatchProposalReady | _chat_router.DispatchProposalNeedsInfo:
    if isinstance(result, _chat_router.DispatchProposalReady) and not (
        result.proposal.title or ""
    ).strip():
        raise HTTPException(
            status_code=422,
            detail={
                "detail": _chat_router.DISPATCH_PROPOSAL_PARSE_DETAIL,
                "reason": "explore dispatch requires a non-empty proposal.title",
            },
        )
    return result


explore_chat_router = APIRouter(prefix="/api/explore-chat")


@explore_chat_router.get("/sessions")
async def list_explore_chat_sessions(
    root: ExploreChatSessionsRootDep,
    project_id: Annotated[str | None, Query(min_length=1, max_length=200)] = None,
    engine_key: Annotated[str | None, Query(min_length=1, max_length=200)] = None,
) -> list[ExploreChatSessionSummaryOut]:
    def _list() -> list[ExploreChatSessionSummaryOut]:
        summaries = explore_chat_mod.list_sessions(
            root=root, project_id=project_id, engine_key=engine_key
        )
        return [
            ExploreChatSessionSummaryOut(
                session_id=s.session_id,
                created_at=s.created_at,
                last_message_preview=s.last_message_preview,
                in_flight=s.in_flight,
                summary_ready=explore_chat_mod.load_brief(s.session_id, root=root) is not None,
                consultant_key=s.consultant_key,
                engine_key=s.engine_key,
            )
            for s in summaries
        ]
    return await run_in_threadpool(_list)


@explore_chat_router.get("/consultants")
async def list_consultants() -> list[ConsultantOut]:
    return [
        ConsultantOut(key=c.key, label=c.label, description=c.description, team=c.team)
        for c in CONSULTANTS
    ]


@explore_chat_router.post("/sessions", status_code=201)
async def create_explore_chat_session(
    body: CreateExploreChatBody, root: ExploreChatSessionsRootDep
) -> CreateSessionResponse:
    await run_in_threadpool(
        _chat_router.validate_create_binding, body.engine_key, body.project_id
    )
    session = await run_in_threadpool(
        explore_chat_mod.create_session,
        body.consultant_key,
        root=root,
        project_id=body.project_id,
        engine_key=body.engine_key,
    )
    return CreateSessionResponse(session_id=session.session_id)


@explore_chat_router.get(
    "/sessions/{session_id}",
    response_model=ExploreChatSessionResponse,
    responses={**_NOT_FOUND, **ARCHIVED_REDIRECT_RESPONSE},
)
async def get_explore_chat_session(
    session_id: str, root: ExploreChatSessionsRootDep, index: WorkspaceIndexDep
) -> ExploreChatSessionResponse | Response:
    try:
        session = await run_in_threadpool(_chat_router.load_or_404, _CTX, session_id, root)
    except HTTPException as exc:
        if exc.status_code != 404:
            raise
        redirect = await archived_prep_chat_redirect(session_id, index)
        if redirect is None:
            raise
        return redirect
    brief = await run_in_threadpool(explore_chat_mod.load_brief, session_id, root=root)
    summary = await run_in_threadpool(_chat_router.compute_run_summary, _CTX, session, root)
    return _to_response(session, brief, summary)


@explore_chat_router.delete("/sessions/{session_id}", status_code=204, responses=_NOT_FOUND)
async def delete_explore_chat_session(
    session_id: str, root: ExploreChatSessionsRootDep
) -> Response:
    return await _chat_router.run_delete(_CTX, session_id, root)


@explore_chat_router.get("/sessions/{session_id}/events", responses=_NOT_FOUND)
async def explore_chat_events(
    session_id: str, root: ExploreChatSessionsRootDep, pubsub: PubSubDep
) -> StreamingResponse:
    await run_in_threadpool(_chat_router.load_or_404, _CTX, session_id, root)
    return StreamingResponse(
        _event_stream(session_id, pubsub), media_type=SSE_MEDIA, headers=SSE_HEADERS
    )


@explore_chat_router.post(
    "/sessions/{session_id}/messages", status_code=202, responses=_TURN_RESPONSES
)
async def send_explore_chat_message(
    session_id: str,
    body: SendMessageBody,
    root: ExploreChatSessionsRootDep,
    pubsub: PubSubDep,
    locks: ExploreChatLocksDep,
    link_env: PrepChatLinkEnvDep,
) -> _chat_router.ChatAck:
    return await _chat_router.start_message_turn(
        _CTX,
        session_id,
        body.content,
        root,
        pubsub,
        locks,
        _chat_router.link_preparer(link_env),
    )


@explore_chat_router.post(
    "/sessions/{session_id}/interrupt", status_code=204, responses=_INTERRUPT_RESPONSES
)
async def interrupt_explore_chat_session(
    session_id: str, root: ExploreChatSessionsRootDep, locks: ExploreChatLocksDep
) -> Response:
    return await _chat_router.run_interrupt(_CTX, session_id, root, locks)


@explore_chat_router.post("/sessions/{session_id}/summarize", responses=_CLI_RESPONSES)
async def summarize_explore_chat(
    session_id: str,
    root: ExploreChatSessionsRootDep,
    pubsub: PubSubDep,
    locks: ExploreChatLocksDep,
) -> explore_chat_mod.ExploreChatBrief:
    return await _chat_router.run_summarize(_CTX, session_id, root, pubsub, locks)


@explore_chat_router.post("/sessions/{session_id}/propose", responses=_PROPOSE_RESPONSES)
async def propose_explore_dispatch(
    session_id: str,
    root: ExploreChatSessionsRootDep,
    workspaces_root: WorkspacesRootDep,
    pubsub: PubSubDep,
    locks: ExploreChatLocksDep,
) -> _chat_router.DispatchProposalReady | _chat_router.DispatchProposalNeedsInfo:
    session = await run_in_threadpool(_chat_router.load_or_404, _CTX, session_id, root)
    consultant = get_consultant(session.consultant_key)
    result = await _chat_router.run_dispatch_proposal(
        ctx=_CTX,
        session=session,
        root=root,
        pubsub=pubsub,
        locks=locks,
        scan=_explore_dispatch_scan(session_id, root, workspaces_root, session.engine_key),
        collision=_explore_dispatch_collision(
            session_id, root, workspaces_root, session.engine_key
        ),
        allowed_new_workspace_teams=frozenset({consultant.team}),
    )
    return _require_explore_proposal_title(result)


@explore_chat_router.put("/sessions/{session_id}/brief", responses=_NOT_FOUND)
async def upsert_explore_chat_brief(
    session_id: str, body: PutBriefBody, root: ExploreChatSessionsRootDep
) -> explore_chat_mod.ExploreChatBrief:
    return await _chat_router.run_put_brief(_CTX, session_id, body.content, root)


@explore_chat_router.get("/sessions/{session_id}/brief", responses=_NOT_FOUND)
async def get_explore_chat_brief(
    session_id: str, root: ExploreChatSessionsRootDep
) -> explore_chat_mod.ExploreChatBrief | None:
    return await _chat_router.run_get_brief(_CTX, session_id, root)
