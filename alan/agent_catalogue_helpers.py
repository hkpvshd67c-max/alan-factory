from __future__ import annotations

import re
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from typing import cast

from alan import prompt_templates
from alan.agent_catalogue_models import (
    CLI_DEFAULT_LABEL,
    AgentEntry,
    AgentScope,
    AgentTag,
    ConditionalDirOut,
    EnvEntryOut,
    FlagOut,
    ProcessOut,
    PromptSegmentOut,
    SessionOut,
    TimeoutsOut,
    ToolsOut,
    TurnLimitOut,
    ValueOut,
    ValueSourceKind,
)
from alan.agent_key import AgentKey
from alan.claude_cli import (
    _CHAT_UX_LENS,  # pyright: ignore[reportPrivateUsage]
    UNTRUSTED_CONTENT_NOTICE,
)
from alan.claude_cli_grants import (
    CHAT_ISSUE_SCOPE_CLAUSE,
    NO_TOOLS_BLOCK,
    PREP_CHAT_ATTACHMENT_CLAUSE,
)
from alan.engine_config import EngineConfig
from alan.headless_prompt import HEADLESS_EXECUTION_BLOCK
from alan.launch_spec import LaunchSpec, PromptSegment, flat_denied_tools
from alan.teams._base import (
    PLANNING_SCREEN_CAPTURE_INSTRUCTION,
    PLANNING_UX_LENS,
    TERSENESS_BLOCK,
    UX_LENS,
)
from alan.teams._declared_blocker import DECLARED_BLOCKER_GUIDANCE
from alan.teams._orchestrated import CONFIDENCE_REPORTING_BLOCK, FOUR_FIELD_REQUIREMENT

_SLOT_RE = re.compile("⟦slot:([A-Z][A-Z0-9_]*)⟧")
_SLOT_DESCRIPTIONS: dict[str, str] = {}
_SLOT_LOCK = threading.Lock()

LABEL_PROJECT_DOC_SLOT = "PROJECT_DOC_BLOCK"
PROJECT_DOC_SLOT_DESCRIPTION = (
    "Pointer to the project's planning doc, inserted only when the workspace belongs to "
    "a project that has one"
)

ENVIRONMENT_NOTE = (
    "Names and categories only; values are never shown. Every variable not listed here is "
    "inherited unchanged from the alan service process."
)


def runtime_slot(name: str, description: str) -> str:
    with _SLOT_LOCK:
        known = _SLOT_DESCRIPTIONS.setdefault(name, description)
        if known != description:
            raise ValueError(
                f"runtime slot {name!r} already declared with a different description"
            )
    return f"⟦slot:{name}⟧"


ROLE_OUTPUT_NONCE = runtime_slot(
    "ROLE_OUTPUT_NONCE",
    "A random nonce drawn fresh for every call, repeated in the start and end markers that "
    "fence each earlier role's report",
)


def readable_slots(text: str) -> str:
    return _SLOT_RE.sub(lambda match: "{{" + match.group(1) + "}}", text)


def slot_description(name: str) -> str:
    return _SLOT_DESCRIPTIONS.get(name, name)


def shared_blocks(extra: Sequence[tuple[str, str]] = ()) -> list[tuple[str, str]]:
    blocks = [
        *extra,
        ("terseness_block", TERSENESS_BLOCK),
        ("untrusted_content_notice", UNTRUSTED_CONTENT_NOTICE),
        ("confidence_reporting_block", CONFIDENCE_REPORTING_BLOCK),
        ("four_field_requirement", FOUR_FIELD_REQUIREMENT),
        ("ux_lens", UX_LENS),
        ("planning_ux_lens", PLANNING_UX_LENS),
        ("planning_screen_capture", PLANNING_SCREEN_CAPTURE_INSTRUCTION),
        ("declared_blocker_guidance", DECLARED_BLOCKER_GUIDANCE),
        ("headless_execution", HEADLESS_EXECUTION_BLOCK),
        ("no_tools", NO_TOOLS_BLOCK),
        ("verifier_anti_self_approval", prompt_templates.VERIFIER_ANTI_SELF_APPROVAL_CLAUSE),
        ("merge_gate_clause", prompt_templates.MERGE_GATE_CLAUSE),
        ("chat_ux_lens", _CHAT_UX_LENS),
        ("chat_issue_scope", CHAT_ISSUE_SCOPE_CLAUSE),
        ("chat_prep_chat_attachment", PREP_CHAT_ATTACHMENT_CLAUSE),
    ]
    return sorted(blocks, key=lambda item: -len(item[1]))


@dataclass(frozen=True)
class _Marker:
    start: int
    end: int
    segment: PromptSegmentOut


def _slot_markers(text: str) -> list[_Marker]:
    return [
        _Marker(
            match.start(),
            match.end(),
            PromptSegmentOut(
                kind="placeholder",
                label=match.group(1),
                description=slot_description(match.group(1)),
            ),
        )
        for match in _SLOT_RE.finditer(text)
    ]


def _block_markers(text: str, label: str, block: str) -> list[_Marker]:
    markers: list[_Marker] = []
    start = text.find(block)
    while start != -1:
        segment = PromptSegmentOut(kind="text", label=label, text=block, shared=True)
        markers.append(_Marker(start, start + len(block), segment))
        start = text.find(block, start + len(block))
    return markers


def _ordered_markers(text: str, extra: Sequence[tuple[str, str]]) -> list[_Marker]:
    found = _slot_markers(text)
    for label, block in shared_blocks(extra):
        found.extend(_block_markers(text, label, block))
    kept: list[_Marker] = []
    cursor = 0
    for marker in sorted(found, key=lambda item: (item.start, -(item.end - item.start))):
        if marker.start >= cursor:
            kept.append(marker)
            cursor = marker.end
    return kept


def _text_segment(label: str, text: str) -> list[PromptSegmentOut]:
    return [PromptSegmentOut(kind="text", label=label, text=text)] if text else []


def segments_from_text(
    text: str, label: str, extra: Sequence[tuple[str, str]] = ()
) -> list[PromptSegmentOut]:
    segments: list[PromptSegmentOut] = []
    cursor = 0
    for marker in _ordered_markers(text, extra):
        segments.extend(_text_segment(label, text[cursor : marker.start]))
        segments.append(marker.segment)
        cursor = marker.end
    segments.extend(_text_segment(label, text[cursor:]))
    return segments


def segments_from_spec(
    prompt: Sequence[PromptSegment], extra: Sequence[tuple[str, str]] = ()
) -> list[PromptSegmentOut]:
    return [
        segment
        for part in prompt
        for segment in segments_from_text(part.text, part.label, extra)
    ]


def project_doc_placeholder() -> PromptSegmentOut:
    return PromptSegmentOut(
        kind="placeholder",
        label=LABEL_PROJECT_DOC_SLOT,
        description=PROJECT_DOC_SLOT_DESCRIPTION,
        shared=True,
    )


@dataclass(frozen=True)
class ValueSource:
    kind: ValueSourceKind
    detail: str | None = None
    engine_role: str | None = None


@dataclass(frozen=True)
class ResolvedAgent:
    key: AgentKey
    name: str
    when_runs: str
    scope: AgentScope
    spec: LaunchSpec
    user_message: str
    model_source: ValueSource
    effort_source: ValueSource
    turn_limit: TurnLimitOut
    session: SessionOut
    working_directory: str
    stored_tools: tuple[str, ...] = ()
    tags: tuple[AgentTag, ...] = ()
    team: str | None = None
    role: str | None = None
    prompt_overridden: bool = False
    configured_model: str | None = None
    configured_effort: str | None = None
    engine_role_hint: str | None = None
    extra_flags: tuple[FlagOut, ...] = ()
    extra_shared_blocks: tuple[tuple[str, str], ...] = ()
    conditional_add_dirs: tuple[ConditionalDirOut, ...] = ()
    cwd_is_runtime: bool = True
    timeouts_detail: str | None = None
    process_detail: str | None = None
    environment_note: str | None = None


def with_flags_and_dirs(
    agent: ResolvedAgent,
    extra_flags: tuple[FlagOut, ...],
    conditional_add_dirs: tuple[ConditionalDirOut, ...],
) -> ResolvedAgent:
    return cast(  # type: ignore[redundant-cast]  # pyright: ignore[reportUnnecessaryCast]
        ResolvedAgent,
        replace(agent, extra_flags=extra_flags, conditional_add_dirs=conditional_add_dirs),
    )


@dataclass(frozen=True)
class ResolverGroup:
    id: str
    name: str
    key: AgentKey
    resolve: Callable[[EngineConfig], Sequence[ResolvedAgent]]


def value_out(value: str | None, source: ValueSource) -> ValueOut:
    if value is None:
        detail = source.detail or "alan passes no value; the CLI default applies"
        return ValueOut(
            value=None, display=CLI_DEFAULT_LABEL, source="cli_default", source_detail=detail
        )
    return ValueOut(
        value=value, display=value, source=source.kind, source_detail=source.detail
    )


def prompt_segments(resolved: ResolvedAgent) -> list[PromptSegmentOut]:
    spec = resolved.spec
    segments = segments_from_spec(spec.prompt, resolved.extra_shared_blocks)
    if spec.layout == "agent" and not spec.deny_all_tools:
        segments.append(project_doc_placeholder())
    return segments


def _tools_out(resolved: ResolvedAgent) -> ToolsOut:
    spec = resolved.spec
    return ToolsOut(
        stored=list(resolved.stored_tools),
        effective=list(spec.allowed_tools),
        denied=list(flat_denied_tools(spec.denied)),
    )


def _timeouts_out(resolved: ResolvedAgent) -> TimeoutsOut:
    process = resolved.spec.process
    return TimeoutsOut(
        idle_seconds=process.idle_timeout_seconds,
        absolute_seconds=process.abs_timeout_seconds,
        detail=resolved.timeouts_detail,
    )


def _process_out(resolved: ResolvedAgent) -> ProcessOut:
    process = resolved.spec.process
    return ProcessOut(
        priority_delta=process.priority_delta,
        detached=process.detached,
        detail=resolved.process_detail,
    )


def to_entry(resolved: ResolvedAgent, flags: Sequence[FlagOut]) -> AgentEntry:
    spec = resolved.spec
    return AgentEntry(
        id=resolved.key.id,
        name=resolved.name,
        kind=resolved.key.kind,
        tags=list(resolved.tags),
        scope=resolved.scope,
        team=resolved.team,
        role=resolved.role,
        when_runs=resolved.when_runs,
        model=value_out(spec.model, resolved.model_source),
        effort=value_out(spec.effort, resolved.effort_source),
        prompt=prompt_segments(resolved),
        prompt_overridden=resolved.prompt_overridden,
        user_message=segments_from_text(
            resolved.user_message, "user_message", resolved.extra_shared_blocks
        ),
        turn_limit=resolved.turn_limit,
        permission_mode=spec.permission_mode,
        tools=_tools_out(resolved),
        add_dirs=[readable_slots(path) for path in spec.add_dirs],
        conditional_add_dirs=list(resolved.conditional_add_dirs),
        session=resolved.session,
        working_directory=resolved.working_directory,
        timeouts=_timeouts_out(resolved),
        environment=[EnvEntryOut(name=e.name, category=e.category) for e in spec.process.env],
        environment_note=resolved.environment_note or ENVIRONMENT_NOTE,
        process=_process_out(resolved),
        flags=list(flags),
    )


def error_entry(key: AgentKey, name: str, error: str) -> AgentEntry:
    return AgentEntry(
        id=key.id,
        name=name,
        kind=key.kind,
        scope="machine",
        when_runs="",
        model=ValueOut(
            value=None, display=CLI_DEFAULT_LABEL, source="cli_default", source_detail=None
        ),
        effort=ValueOut(
            value=None, display=CLI_DEFAULT_LABEL, source="cli_default", source_detail=None
        ),
        turn_limit=TurnLimitOut(max_turns=None),
        tools=ToolsOut(),
        session=SessionOut(behaviour="none", detail=""),
        working_directory="",
        timeouts=TimeoutsOut(),
        process=ProcessOut(),
        error=error,
    )


__all__ = [
    "ENVIRONMENT_NOTE",
    "LABEL_PROJECT_DOC_SLOT",
    "PROJECT_DOC_SLOT_DESCRIPTION",
    "ROLE_OUTPUT_NONCE",
    "ResolvedAgent",
    "ResolverGroup",
    "ValueSource",
    "error_entry",
    "project_doc_placeholder",
    "readable_slots",
    "prompt_segments",
    "runtime_slot",
    "segments_from_spec",
    "segments_from_text",
    "shared_blocks",
    "slot_description",
    "to_entry",
    "value_out",
    "with_flags_and_dirs",
]
