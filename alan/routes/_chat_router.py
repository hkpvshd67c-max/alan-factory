from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import subprocess
import threading
import uuid
from collections.abc import AsyncGenerator, Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, Generic, Literal, TypeVar, cast

import yaml
from fastapi import HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field, ValidationError
from starlette.responses import Response

import alan.dispatch_proposal as dispatch_proposal_mod
import alan.engine_config as engine_config_mod
import alan.project as project_mod
from alan import _chat_core, prep_chat_link
from alan import collision as collision_mod
from alan.agent_key import BUILD_CHAT, AgentKey
from alan.block_correction import BlockCorrectionError
from alan.claude_cli import (
    DISPATCH_PROPOSAL_SENTINEL,
    ChatTurnResult,
    ClaudeCliError,
    ClaudeCliTimeout,
    ResumeSessionLostError,
    UsageLimitError,
    _effort_for_model,  # pyright: ignore[reportPrivateUsage]
    run_chat_turn,
)
from alan.events import PubSub
from alan.routes import _chat_correction
from alan.routes._deps import (
    RAW_OUTPUT_TRUNCATE,
    CreateSessionResponse,
    extract_yaml_object,
    format_sse,
    require_str_list,
)
from alan.run_summary import (
    DEFAULT_CHAT_MODEL,
    RunSummary,
    derive_run_summary,
    run_summary_placeholder,
    tool_use_event_label,
)
from alan.scope_audit import ScopeGapFinding
from alan.teams import MANUALLY_DISPATCHABLE_TEAMS
from alan.teams._base import TeamEvent

logger = logging.getLogger("alan.server")

DISPATCH_PROPOSAL_PARSE_DETAIL = "dispatch_proposal_parse_failed"
_RELEASE_WAIT_SECONDS = 5.0
_MAX_STORED_OPERATIONS = 200

_GENERIC_CHAT_ERROR_TEXT = "Something went wrong while generating a response. Please try again."
_USAGE_LIMIT_CHAT_ERROR_TEXT = "The usage limit was reached. Please try again later."
_TIMEOUT_CHAT_ERROR_TEXT = "The assistant took too long to respond. Please try again."
_SESSION_LOST_CHAT_ERROR_TEXT = (
    "The conversation session expired. Please send your message again."
)

S = TypeVar("S", bound=_chat_core.ChatSession)

Responses = dict[int | str, dict[str, str]]


class SendMessageBody(BaseModel):
    content: str = Field(min_length=1, max_length=20000)


class PutBriefBody(BaseModel):
    content: str = Field(max_length=20000)


def _empty_prep_chat_reads() -> list[_chat_core.PrepChatRead]:
    return []


class ChatAck(BaseModel):
    status: Literal["accepted"]
    prep_chat_reads: list[_chat_core.PrepChatRead] = Field(default_factory=_empty_prep_chat_reads)


@dataclass(frozen=True)
class PreparedTurn:
    user_text: str
    prep_chat_reads: list[_chat_core.PrepChatRead]


PrepareTurn = Callable[[_chat_core.ChatSession, str], Awaitable[PreparedTurn]]


def link_preparer(env: prep_chat_link.LinkEnv) -> PrepareTurn:
    async def prepare(session: _chat_core.ChatSession, content: str) -> PreparedTurn:
        attachment = await run_in_threadpool(
            prep_chat_link.attach,
            content,
            env,
            engine_key=session.engine_key,
            session_id=session.session_id,
        )
        return PreparedTurn(
            user_text=prep_chat_link.append_blocks(content, attachment),
            prep_chat_reads=attachment.reads,
        )

    return prepare


async def _prepared_turn(
    prepare: PrepareTurn | None, session: _chat_core.ChatSession, content: str
) -> PreparedTurn:
    if prepare is None:
        return PreparedTurn(user_text=content, prep_chat_reads=[])
    return await prepare(session, content)


def _default_agent(_session: object) -> AgentKey:
    return BUILD_CHAT


@dataclass(frozen=True)
class ChatRouterCtx(Generic[S]):
    core: _chat_core.ChatCoreConfig[S]
    channel_prefix: str
    label: str
    summarize_sentinel: str
    system_prompt: Callable[[S], str]
    cwd: Path | None = None
    allowed_tools: list[str] | None = None
    pre_turn: Callable[[S, Path], None] | None = None
    resolve_cwd: Callable[[str, Path], Path | None] | None = None
    agent: Callable[[S], AgentKey] = _default_agent


def not_found_responses(label: str) -> Responses:
    return {404: {"description": f"{label} session not found"}}


def turn_responses(label: str) -> Responses:
    return {
        404: {"description": f"{label} session not found"},
        409: {"description": "chat turn already in progress"},
    }


def interrupt_responses(label: str) -> Responses:
    return not_found_responses(label)


def cli_responses(label: str) -> Responses:
    return {
        404: {"description": f"{label} session not found"},
        409: {"description": "chat turn already in progress"},
        502: {"description": "claude CLI failed"},
    }


def channel(ctx: ChatRouterCtx[S], session_id: str) -> str:
    return f"{ctx.channel_prefix}:{session_id}"


def publish(
    ctx: ChatRouterCtx[S], pubsub: PubSub, session_id: str, kind: str, payload: dict[str, str]
) -> None:
    pubsub.publish(channel(ctx, session_id), TeamEvent(kind, payload))


def is_known_engine_key(engine_key: str) -> bool:
    if engine_config_mod.lookup_by_slug(engine_key) is not None:
        return True
    return engine_key == engine_config_mod.default_engine_config().repo.slug


def validate_create_binding(engine_key: str, project_id: str | None) -> None:
    if not is_known_engine_key(engine_key):
        raise HTTPException(status_code=400, detail=f"unknown engine: {engine_key!r}")
    if project_id is None:
        return
    bound_engine_key = project_mod.engine_key_for(project_id)
    if bound_engine_key is not None and bound_engine_key != engine_key:
        raise HTTPException(
            status_code=400,
            detail=f"project {project_id!r} is bound to a different engine",
        )


def load_or_404(ctx: ChatRouterCtx[S], session_id: str, root: Path | None) -> S:
    try:
        return _chat_core.load_session(ctx.core, session_id, root=root)
    except _chat_core.ChatCoreError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def compute_run_summary(ctx: ChatRouterCtx[S], session: S, root: Path) -> RunSummary:
    stream_path = _chat_core.stream_path(ctx.core, session.session_id, root=root)
    system_prompt = ctx.system_prompt(session)
    summary = derive_run_summary(stream_path, system_prompt=system_prompt)
    if summary is not None:
        return summary
    return run_summary_placeholder(
        model=DEFAULT_CHAT_MODEL,
        effort=_effort_for_model(DEFAULT_CHAT_MODEL),
        system_prompt=system_prompt,
    )


def compute_collision_run_summary(
    ctx: ChatRouterCtx[S], session: S, root: Path
) -> RunSummary | None:
    stream_path = _chat_core.collision_stream_path(ctx.core, session.session_id, root=root)
    return collision_mod.read_collision_usage(stream_path=stream_path)


async def event_stream(
    ctx: ChatRouterCtx[S], session_id: str, pubsub: PubSub
) -> AsyncGenerator[str, None]:
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[TeamEvent] = asyncio.Queue()

    def on_event(event: TeamEvent) -> None:
        loop.call_soon_threadsafe(queue.put_nowait, event)

    unsubscribe = pubsub.subscribe(channel(ctx, session_id), on_event)
    try:
        while True:
            event = await queue.get()
            yield format_sse(event)
    finally:
        unsubscribe()


def _block_text(block: object) -> str | None:
    if not isinstance(block, dict):
        return None
    block = cast(dict[str, Any], block)
    if block.get("type") != "text":
        return None
    text = block.get("text")
    return text if isinstance(text, str) else None


def _assistant_event_text(event: dict[str, Any]) -> str:
    if event.get("type") != "assistant":
        return ""
    message = event.get("message")
    if not isinstance(message, dict):
        return ""
    message = cast(dict[str, Any], message)
    content = message.get("content")
    if not isinstance(content, list):
        return ""
    content = cast("list[object]", content)
    texts = (_block_text(block) for block in content)
    return "".join(text for text in texts if text is not None)


def _empty_str_list() -> list[str]:
    return []


@dataclass
class _PartialTextTracker:
    chunks: list[str] = field(default_factory=_empty_str_list)
    operations: list[str] = field(default_factory=_empty_str_list)

    def observe(self, event: dict[str, Any]) -> None:
        text = _assistant_event_text(event)
        if text:
            self.chunks.append(text)

    def observe_operation(self, event: dict[str, Any]) -> None:
        label = tool_use_event_label(event)
        if label is None:
            return
        self.operations.append(label)
        if len(self.operations) > _MAX_STORED_OPERATIONS:
            del self.operations[0]

    def text(self) -> str:
        return "\n\n".join(self.chunks)


def _plain_chat_error_text(exc: ClaudeCliError) -> str:
    if isinstance(exc, UsageLimitError):
        return _USAGE_LIMIT_CHAT_ERROR_TEXT
    if isinstance(exc, ClaudeCliTimeout):
        return _TIMEOUT_CHAT_ERROR_TEXT
    if isinstance(exc, ResumeSessionLostError):
        return _SESSION_LOST_CHAT_ERROR_TEXT
    return _GENERIC_CHAT_ERROR_TEXT


def do_turn(
    *,
    ctx: ChatRouterCtx[S],
    session: S,
    user_text: str,
    pubsub: PubSub,
    root: Path,
    locks: _chat_core.ChatSessionLocks,
    entry: _chat_core.TurnEntry,
    partial: _PartialTextTracker | None = None,
) -> ChatTurnResult:
    if ctx.pre_turn is not None:
        ctx.pre_turn(session, root)
    cwd = ctx.resolve_cwd(session.session_id, root) if ctx.resolve_cwd is not None else ctx.cwd
    sink = _chat_core.stream_path(ctx.core, session.session_id, root=root)
    sid = session.session_id
    if session.cli_session_id is not None:
        resume = True
        turn_session_id = session.cli_session_id
    else:
        resume = False
        turn_session_id = str(uuid.uuid4())

    def emit(event: dict[str, Any]) -> None:
        if partial is not None:
            partial.observe(event)
            partial.observe_operation(event)
        publish(ctx, pubsub, sid, "chat_event", {"event": json.dumps(event)})

    def remember_proc(proc: subprocess.Popen[str]) -> None:
        locks.set_proc(entry, proc)

    def remember_cli_session_id(cli_session_id: str) -> None:
        _chat_core.update_session(ctx.core, sid, {"cli_session_id": cli_session_id}, root=root)

    try:
        return run_chat_turn(
            user_text=user_text,
            session_id=turn_session_id,
            resume=resume,
            system_prompt=ctx.system_prompt(session),
            on_event=emit,
            on_proc_started=remember_proc,
            on_session_id=remember_cli_session_id,
            stream_path=sink,
            allowed_tools=ctx.allowed_tools,
            cwd=cwd,
            agent=ctx.agent(session),
        )
    except ResumeSessionLostError:
        _chat_core.update_session(ctx.core, sid, {"cli_session_id": None}, root=root)
        raise


def clear_best_effort(ctx: ChatRouterCtx[S], session_id: str, root: Path) -> None:
    try:
        _chat_core.clear_in_flight_turn(ctx.core, session_id, root=root)
    except Exception:  # noqa: BLE001
        logger.exception("failed to clear %s in_flight for %s", ctx.label, session_id)


def _finish_stopped_turn(
    ctx: ChatRouterCtx[S], session: S, root: Path, partial: _PartialTextTracker
) -> tuple[str, dict[str, str]]:
    content = partial.text()
    _chat_core.append_assistant_and_clear_in_flight(
        ctx.core,
        session,
        _chat_core.ChatMessage(
            role="assistant",
            content=content,
            ts=datetime.now(UTC),
            interrupted=True,
            operations=partial.operations,
        ),
        root=root,
    )
    return "turn_stopped", {"content": content}


def execute_turn(
    *,
    ctx: ChatRouterCtx[S],
    session: S,
    user_text: str,
    pubsub: PubSub,
    root: Path,
    locks: _chat_core.ChatSessionLocks,
    entry: _chat_core.TurnEntry,
) -> tuple[str, dict[str, str]]:
    sid = session.session_id
    partial = _PartialTextTracker()
    try:
        try:
            result = do_turn(
                ctx=ctx,
                session=session,
                user_text=user_text,
                pubsub=pubsub,
                root=root,
                locks=locks,
                entry=entry,
                partial=partial,
            )
            _chat_core.append_assistant_and_clear_in_flight(
                ctx.core,
                session,
                _chat_core.ChatMessage(
                    role="assistant",
                    content=result.result_text,
                    ts=datetime.now(UTC),
                    operations=partial.operations,
                ),
                root=root,
            )
        except ClaudeCliError as exc:
            if locks.was_cancelled(entry):
                return _finish_stopped_turn(ctx, session, root, partial)
            logger.warning("%s turn failed for %s: %s", ctx.label, sid, exc)
            clear_best_effort(ctx, sid, root)
            return "error", {"detail": _plain_chat_error_text(exc)}
        except Exception as exc:  # noqa: BLE001
            logger.exception("%s turn unexpected failure for %s", ctx.label, sid)
            clear_best_effort(ctx, sid, root)
            return "error", {"detail": str(exc)}
        return "assistant_done", {"content": result.result_text, "usage": json.dumps(result.usage)}
    finally:
        locks.release(entry)


def run_turn_background(
    *,
    ctx: ChatRouterCtx[S],
    session: S,
    user_text: str,
    pubsub: PubSub,
    root: Path,
    locks: _chat_core.ChatSessionLocks,
    entry: _chat_core.TurnEntry,
) -> None:
    event_kind, event_data = execute_turn(
        ctx=ctx,
        session=session,
        user_text=user_text,
        pubsub=pubsub,
        root=root,
        locks=locks,
        entry=entry,
    )
    publish(ctx, pubsub, session.session_id, event_kind, event_data)


async def run_create(
    ctx: ChatRouterCtx[S],
    root: Path,
    *,
    project_id: str | None = None,
    engine_key: str | None = None,
) -> CreateSessionResponse:
    session = await run_in_threadpool(
        _chat_core.create_session,
        ctx.core,
        root=root,
        project_id=project_id,
        engine_key=engine_key,
    )
    return CreateSessionResponse(session_id=session.session_id)


async def run_delete(ctx: ChatRouterCtx[S], session_id: str, root: Path) -> Response:
    try:
        await run_in_threadpool(_chat_core.delete_session, ctx.core, session_id, root=root)
    except _chat_core.ChatCoreError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return Response(status_code=204)


def _acquire_or_409(
    session: _chat_core.ChatSession, locks: _chat_core.ChatSessionLocks
) -> _chat_core.TurnEntry:
    try:
        return locks.acquire(session.session_id)
    except _chat_core.ChatCoreError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


async def start_message_turn(
    ctx: ChatRouterCtx[S],
    session_id: str,
    content: str,
    root: Path,
    pubsub: PubSub,
    locks: _chat_core.ChatSessionLocks,
    prepare: PrepareTurn | None = None,
) -> ChatAck:
    session = await run_in_threadpool(load_or_404, ctx, session_id, root)
    entry = _acquire_or_409(session, locks)
    sid = session.session_id
    try:
        prepared = await _prepared_turn(prepare, session, content)
        user_msg = _chat_core.ChatMessage(
            role="user",
            content=content,
            ts=datetime.now(UTC),
            prep_chat_reads=prepared.prep_chat_reads,
        )
        session = await run_in_threadpool(
            _chat_core.append_user_message_and_mark_started, ctx.core, session, user_msg, root=root
        )
        thread = threading.Thread(
            target=run_turn_background,
            kwargs={
                "ctx": ctx,
                "session": session,
                "user_text": prepared.user_text,
                "pubsub": pubsub,
                "root": root,
                "locks": locks,
                "entry": entry,
            },
            daemon=True,
            name=f"{ctx.label}-turn-{session_id}",
        )
        thread.start()
    except BaseException:
        clear_best_effort(ctx, sid, root)
        locks.release(entry)
        raise
    return ChatAck(status="accepted", prep_chat_reads=prepared.prep_chat_reads)


async def run_interrupt(
    ctx: ChatRouterCtx[S], session_id: str, root: Path, locks: _chat_core.ChatSessionLocks
) -> Response:
    await run_in_threadpool(load_or_404, ctx, session_id, root)
    cancelled = await run_in_threadpool(locks.cancel, session_id)
    if cancelled:
        released = await run_in_threadpool(locks.wait_released, session_id, _RELEASE_WAIT_SECONDS)
        if not released:
            await run_in_threadpool(locks.force_release, session_id)
    return Response(status_code=204)


async def run_summarize(
    ctx: ChatRouterCtx[S],
    session_id: str,
    root: Path,
    pubsub: PubSub,
    locks: _chat_core.ChatSessionLocks,
) -> _chat_core.ChatBrief:
    session = await run_in_threadpool(load_or_404, ctx, session_id, root)
    entry = _acquire_or_409(session, locks)
    try:
        result = await run_in_threadpool(
            do_turn,
            ctx=ctx,
            session=session,
            user_text=ctx.summarize_sentinel,
            pubsub=pubsub,
            root=root,
            locks=locks,
            entry=entry,
        )
        return await run_in_threadpool(
            _chat_core.save_brief, ctx.core, session_id, result.result_text, root=root
        )
    except ClaudeCliError as exc:
        logger.warning("%s summarize failure: %s", ctx.label, exc)
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    finally:
        locks.release(entry)


async def run_put_brief(
    ctx: ChatRouterCtx[S], session_id: str, content: str, root: Path
) -> _chat_core.ChatBrief:
    await run_in_threadpool(load_or_404, ctx, session_id, root)
    return await run_in_threadpool(_chat_core.save_brief, ctx.core, session_id, content, root=root)


async def run_get_brief(
    ctx: ChatRouterCtx[S], session_id: str, root: Path
) -> _chat_core.ChatBrief | None:
    await run_in_threadpool(load_or_404, ctx, session_id, root)
    return await run_in_threadpool(_chat_core.load_brief, ctx.core, session_id, root=root)


class CollisionMatchOut(BaseModel):
    label: str
    kind: Literal["pr", "workspace"]
    deep_link: str


class CollisionVerdictOut(BaseModel):
    state: Literal["green", "yellow", "red"]
    sentence: str
    match: CollisionMatchOut | None = None


def collision_verdict_out(result: collision_mod.CollisionResult) -> CollisionVerdictOut:
    match: CollisionMatchOut | None = None
    if result.match is not None:
        match = CollisionMatchOut(
            label=result.match.label, kind=result.match.kind, deep_link=result.match.deep_link
        )
    return CollisionVerdictOut(state=result.state, sentence=result.sentence, match=match)


class DispatchProposalReady(BaseModel):
    status: Annotated[str, Field(pattern=r"^ready$")] = "ready"
    proposal: dispatch_proposal_mod.DispatchProposal
    collision: CollisionVerdictOut = Field(
        default_factory=lambda: collision_verdict_out(collision_mod.SAFE_RESULT)
    )


class DispatchProposalNeedsInfo(BaseModel):
    status: Annotated[str, Field(pattern=r"^need_more_info$")] = "need_more_info"
    questions: list[str]


def _new_workspace_team_context(
    allowed_new_workspace_teams: frozenset[str] | None,
) -> dict[str, frozenset[str]] | None:
    if allowed_new_workspace_teams is None:
        return None
    return {
        dispatch_proposal_mod.ALLOWED_NEW_WORKSPACE_TEAMS_CONTEXT_KEY: allowed_new_workspace_teams
    }


def _build_dispatch_proposal_ready(
    obj: dict[str, Any],
    *,
    allowed_new_workspace_teams: frozenset[str] | None = None,
) -> DispatchProposalReady:
    proposal_obj = obj.get("proposal")
    if not isinstance(proposal_obj, dict):
        raise ValueError("proposal missing")
    context = _new_workspace_team_context(allowed_new_workspace_teams)
    proposal = dispatch_proposal_mod.DispatchProposal.model_validate(proposal_obj, context=context)
    if proposal.team not in MANUALLY_DISPATCHABLE_TEAMS:
        raise ValueError(
            f"team {proposal.team!r} is not manually dispatchable (must be "
            f"one of: {sorted(MANUALLY_DISPATCHABLE_TEAMS)})"
        )
    return DispatchProposalReady.model_validate(
        {"status": "ready", "proposal": proposal}, context=context
    )


def _validate_dispatch_yaml_shape(raw: str) -> dict[str, Any]:
    try:
        obj = extract_yaml_object(raw)
    except (ValueError, yaml.YAMLError) as exc:
        raise BlockCorrectionError(
            f"yaml extract failed: {exc}", kind="parse_error"
        ) from exc
    if not isinstance(obj, dict):
        raise BlockCorrectionError("top-level YAML value is not a mapping")
    return cast(dict[str, Any], obj)


def _dispatch_proposal_parse_error(raw: str, reason: str) -> HTTPException:
    logger.warning("dispatch proposal parse failed: %s", reason)
    logger.debug(
        "dispatch proposal parse failed raw output (truncated): %r",
        raw[:RAW_OUTPUT_TRUNCATE],
    )
    return HTTPException(
        status_code=422,
        detail={"detail": DISPATCH_PROPOSAL_PARSE_DETAIL, "reason": reason},
    )


def parse_dispatch_proposal_yaml(
    raw: str,
    *,
    allowed_new_workspace_teams: frozenset[str] | None = None,
) -> DispatchProposalReady | DispatchProposalNeedsInfo:
    try:
        obj = extract_yaml_object(raw)
    except (ValueError, yaml.YAMLError) as exc:
        raise _dispatch_proposal_parse_error(raw, f"yaml extract failed: {exc}") from exc
    if not isinstance(obj, dict):
        raise _dispatch_proposal_parse_error(raw, "top-level YAML value is not a mapping")
    obj_dict = cast(dict[str, Any], obj)
    status_val = obj_dict.get("status")
    try:
        if status_val == "ready":
            return _build_dispatch_proposal_ready(
                obj_dict, allowed_new_workspace_teams=allowed_new_workspace_teams
            )
        if status_val == "need_more_info":
            return DispatchProposalNeedsInfo(
                status="need_more_info",
                questions=require_str_list(obj_dict.get("questions"), "questions"),
            )
    except (ValueError, ValidationError) as exc:
        raise _dispatch_proposal_parse_error(raw, str(exc)) from exc
    raise _dispatch_proposal_parse_error(raw, f"unknown status: {status_val!r}")


async def _safe_predispatch_scan(
    scan: Callable[[], Awaitable[list[ScopeGapFinding]]],
) -> list[ScopeGapFinding]:
    try:
        return await scan()
    except Exception:  # noqa: BLE001 - scan failure must not surface as 500 on any propose door
        logger.warning("dispatch propose: predispatch scan failed", exc_info=True)
        return []


async def _safe_collision_check(
    collision: Callable[[], Awaitable[collision_mod.CollisionResult]],
) -> collision_mod.CollisionResult:
    try:
        return await collision()
    except Exception:  # noqa: BLE001 - collision failure must not surface as 500 or delay any propose door
        logger.warning("dispatch propose: collision check failed", exc_info=True)
        return collision_mod.SAFE_RESULT


# Real call sites run scan_predispatch/check_collision via run_in_threadpool, which
# anyio can't cancel promptly once the worker thread has started — bound how long an
# early exit (e.g. a CLI failure) waits for it, so cleanup can't add its full latency.
_SCAN_CANCEL_GRACE_SECONDS = 0.5


async def _cancel_with_grace(task: asyncio.Task[Any]) -> None:
    if not task.done():
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, TimeoutError):
            await asyncio.wait_for(asyncio.shield(task), timeout=_SCAN_CANCEL_GRACE_SECONDS)
    else:
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task


async def _corrected_dispatch_proposal_text(
    *,
    ctx: ChatRouterCtx[S],
    session: S,
    root: Path,
    pubsub: PubSub,
    locks: _chat_core.ChatSessionLocks,
    entry: _chat_core.TurnEntry,
    first_result_text: str,
) -> str:
    def _do_turn_again(error: str) -> str | None:
        try:
            refreshed = _chat_core.load_session(ctx.core, session.session_id, root=root)
        except _chat_core.ChatCoreError:
            return None
        result2 = do_turn(
            ctx=ctx,
            session=refreshed,
            user_text=_chat_correction.correction_user_text(error),
            pubsub=pubsub,
            root=root,
            locks=locks,
            entry=entry,
        )
        return result2.result_text

    correction = await run_in_threadpool(
        _chat_correction.run_dispatch_correction,
        first_result_text,
        validate=_validate_dispatch_yaml_shape,
        do_turn_again=_do_turn_again,
        role=ctx.label,
    )
    last_text = correction.attempts[-1].text
    if correction.value is None:
        raise _dispatch_proposal_parse_error(
            last_text, correction.final_error or "unparseable"
        )
    return last_text


async def _finalize_ready_proposal(
    parsed: DispatchProposalReady,
    *,
    scan_task: asyncio.Task[list[ScopeGapFinding]],
    collision_task: asyncio.Task[collision_mod.CollisionResult],
    allowed_new_workspace_teams: frozenset[str] | None,
) -> DispatchProposalReady:
    scan_findings = await scan_task
    collision_result = await collision_task
    merged = scan_findings + list(parsed.proposal.scope_audit)
    updated = parsed.proposal.model_copy(update={"scope_audit": merged[:20]})
    return DispatchProposalReady.model_validate(
        {
            "status": "ready",
            "proposal": updated,
            "collision": collision_verdict_out(collision_result),
        },
        context=_new_workspace_team_context(allowed_new_workspace_teams),
    )


async def run_dispatch_proposal(
    *,
    ctx: ChatRouterCtx[S],
    session: S,
    root: Path,
    pubsub: PubSub,
    locks: _chat_core.ChatSessionLocks,
    scan: Callable[[], Awaitable[list[ScopeGapFinding]]],
    collision: Callable[[], Awaitable[collision_mod.CollisionResult]],
    allowed_new_workspace_teams: frozenset[str] | None = None,
) -> DispatchProposalReady | DispatchProposalNeedsInfo:
    try:
        entry = locks.acquire(session.session_id)
    except _chat_core.ChatCoreError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    scan_task = asyncio.create_task(_safe_predispatch_scan(scan))
    collision_task = asyncio.create_task(_safe_collision_check(collision))
    try:
        result = await run_in_threadpool(
            do_turn,
            ctx=ctx,
            session=session,
            user_text=DISPATCH_PROPOSAL_SENTINEL,
            pubsub=pubsub,
            root=root,
            locks=locks,
            entry=entry,
        )
        final_text = await _corrected_dispatch_proposal_text(
            ctx=ctx,
            session=session,
            root=root,
            pubsub=pubsub,
            locks=locks,
            entry=entry,
            first_result_text=result.result_text,
        )
        parsed = parse_dispatch_proposal_yaml(
            final_text, allowed_new_workspace_teams=allowed_new_workspace_teams
        )
        if isinstance(parsed, DispatchProposalReady):
            return await _finalize_ready_proposal(
                parsed,
                scan_task=scan_task,
                collision_task=collision_task,
                allowed_new_workspace_teams=allowed_new_workspace_teams,
            )
        await scan_task
        await collision_task
        return parsed
    except ClaudeCliError as exc:
        logger.warning("%s propose claude failure: %s", ctx.label, exc)
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    finally:
        await _cancel_with_grace(scan_task)
        await _cancel_with_grace(collision_task)
        locks.release(entry)
