from __future__ import annotations

import html
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, TypeVar
from urllib.parse import parse_qs, urlsplit

import alan.build_chat as build_chat_mod
import alan.explore_chat as explore_chat_mod
import alan.prep_chat as prep_chat_mod
import alan.workspace as workspace_mod
from alan import _chat_core, engine_config
from alan.host_allowlist import allowed_hostnames
from alan.workspace_index import WorkspaceIndex

logger = logging.getLogger("alan.prep_chat_link")

MAX_LINKS = 3
TRANSCRIPT_CHAR_CAP = 40_000
_TITLE_MAX = 120

LinkKind = Literal["workspace", "build", "explore"]

_URL_RE = re.compile(
    r"https?://(?:\[[0-9A-Fa-f:.]+\]|[^\s<>\"'`()\[\]/?#]+)[^\s<>\"'`()\[\]]*",
    re.IGNORECASE,
)
_TRAILING_PUNCTUATION = ".,;:!?"
_WORKSPACE_PATH = re.compile(r"(?:/api)?/workspaces/(?P<id>[^/]+)(?:/prep-chat)?")
_API_SESSION_PATH = re.compile(r"/api/(?P<kind>build|explore)-chat/sessions/(?P<id>[^/]+)")
_PAGE_SESSION_PATH = re.compile(r"(?:/repos/[^/]+/[^/]+)?/(?P<kind>build|explore/[^/]+)")
_SESSION_KINDS: dict[str, LinkKind] = {"build": "build", "explore": "explore"}
_TASK_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_SESSION_ID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
)
_TAG_RE = re.compile(r"<(/?)(preparation_chat|brief|transcript|message)\b")

S = TypeVar("S", bound=_chat_core.ChatSession)


@dataclass(frozen=True)
class LinkRef:
    kind: LinkKind
    ident: str


@dataclass(frozen=True)
class LinkEnv:
    build_root: Path
    explore_root: Path
    workspaces_root: Path
    index: Callable[[], WorkspaceIndex]


@dataclass(frozen=True)
class Conversation:
    session: _chat_core.ChatSession
    brief: _chat_core.ChatBrief | None
    title: str | None


@dataclass(frozen=True)
class Attachment:
    reads: list[_chat_core.PrepChatRead]
    blocks: list[str]


def _page_ref(path: str, query: str) -> LinkRef | None:
    page = _PAGE_SESSION_PATH.fullmatch(path)
    session_ids = parse_qs(query).get("s")
    if page is None or not session_ids:
        return None
    return LinkRef(_SESSION_KINDS[page["kind"].split("/")[0]], session_ids[0])


def _ref_for(path: str, query: str) -> LinkRef | None:
    trimmed = path.rstrip("/")
    workspace = _WORKSPACE_PATH.fullmatch(trimmed)
    if workspace is not None:
        return LinkRef("workspace", workspace["id"])
    api = _API_SESSION_PATH.fullmatch(trimmed)
    if api is not None:
        return LinkRef(_SESSION_KINDS[api["kind"]], api["id"])
    return _page_ref(trimmed, query)


def _ref_from_url(raw: str, hosts: frozenset[str]) -> LinkRef | None:
    try:
        parts = urlsplit(raw)
    except ValueError:
        return None
    if parts.hostname not in hosts:
        return None
    return _ref_for(parts.path, parts.query)


def extract_refs(text: str) -> list[LinkRef]:
    hosts = allowed_hostnames()
    found = (
        _ref_from_url(match.group().rstrip(_TRAILING_PUNCTUATION), hosts)
        for match in _URL_RE.finditer(text)
    )
    return list(dict.fromkeys(ref for ref in found if ref is not None))


def _clean_title(title: str) -> str | None:
    return " ".join(title.split())[:_TITLE_MAX] or None


def _live_title(session: _chat_core.ChatSession) -> str | None:
    first = next((m.content for m in session.transcript if m.role == "user"), "")
    return _clean_title(next((line for line in first.splitlines() if line.strip()), ""))


def _workspace_path(task_id: str, env: LinkEnv) -> Path | None:
    if not _TASK_ID_RE.fullmatch(task_id):
        return None
    try:
        return workspace_mod.read(task_id, root=env.workspaces_root).path
    except workspace_mod.WorkspaceError:
        return None


def _archived(ws_path: Path, session_id: str) -> Conversation | None:
    try:
        archived = prep_chat_mod.read_archived(ws_path, session_id)
    except prep_chat_mod.PrepChatError:
        return None
    return Conversation(archived.session, archived.brief, _clean_title(archived.marker.title))


def _peek_live(
    cfg: _chat_core.ChatCoreConfig[S], session_id: str, root: Path
) -> Conversation | None:
    try:
        session = _chat_core.peek_session(cfg, session_id, root=root)
    except _chat_core.ChatCoreError:
        return None
    brief = _chat_core.load_brief(cfg, session_id, root=root)
    return Conversation(session, brief, _live_title(session))


def _live(ref: LinkRef, env: LinkEnv) -> Conversation | None:
    if ref.kind == "build":
        return _peek_live(build_chat_mod.CONFIG, ref.ident, env.build_root)
    return _peek_live(explore_chat_mod.CONFIG, ref.ident, env.explore_root)


def _by_workspace(task_id: str, env: LinkEnv) -> Conversation | None:
    ws_path = _workspace_path(task_id, env)
    if ws_path is None:
        return None
    session_id = prep_chat_mod.find_archived_session_id(ws_path)
    if session_id is None:
        return None
    return _archived(ws_path, session_id)


def _by_session(ref: LinkRef, env: LinkEnv) -> Conversation | None:
    if not _SESSION_ID_RE.fullmatch(ref.ident):
        return None
    live = _live(ref, env)
    if live is not None:
        return live
    task_id = env.index().resolve_archived_prep_chat(ref.ident)
    ws_path = None if task_id is None else _workspace_path(task_id, env)
    if ws_path is None:
        return None
    return _archived(ws_path, ref.ident)


def _resolve(ref: LinkRef, env: LinkEnv) -> Conversation | None:
    if ref.kind == "workspace":
        return _by_workspace(ref.ident, env)
    return _by_session(ref, env)


def _resolve_safely(ref: LinkRef, env: LinkEnv) -> Conversation | None:
    try:
        return _resolve(ref, env)
    except Exception:  # noqa: BLE001
        logger.warning("prep_chat_link: resolving a %s link failed", ref.kind, exc_info=True)
        return None


def _scope(engine_key: str | None) -> engine_config.RepoIdentity | None:
    if engine_key is None:
        return engine_config.canonical_scope_key(None)
    return _chat_core.resolve_known_scope(engine_key)


def _same_repository(chat_engine_key: str | None, conversation_engine_key: str | None) -> bool:
    chat_scope = _scope(chat_engine_key)
    return chat_scope is not None and chat_scope == _scope(conversation_engine_key)


def _defang(text: str) -> str:
    return _TAG_RE.sub(lambda match: f"<\\{match[1]}{match[2]}", text)


def _render_message(message: _chat_core.ChatMessage) -> str:
    return f'<message role="{message.role}">\n{_defang(message.content)}\n</message>'


def _newest_within_cap(rendered: list[str]) -> list[str]:
    kept: list[str] = []
    total = 0
    for text in reversed(rendered):
        total += len(text)
        if kept and total > TRANSCRIPT_CHAR_CAP:
            break
        kept.append(text)
    kept.reverse()
    return kept


def _transcript_section(messages: list[_chat_core.ChatMessage]) -> str:
    rendered = [_render_message(message) for message in messages]
    kept = _newest_within_cap(rendered)
    lines = ["<transcript>"]
    if len(kept) < len(rendered):
        omitted = len(rendered) - len(kept)
        lines.append(f"truncated: {omitted} of {len(rendered)} earlier messages omitted")
    lines.extend(kept)
    lines.append("</transcript>")
    return "\n".join(lines)


def _open_tag(position: int, status: str, title: str | None = None) -> str:
    attributes = f'link="{position}" status="{status}"'
    if title is not None:
        attributes += f' title="{html.escape(title)}"'
    return f"<preparation_chat {attributes}>"


def _read_block(position: int, conversation: Conversation) -> str:
    lines = [_open_tag(position, "read", conversation.title)]
    if conversation.brief is not None:
        lines.append(f"<brief>\n{_defang(conversation.brief.content)}\n</brief>")
    lines.append(_transcript_section(conversation.session.transcript))
    lines.append("</preparation_chat>")
    return "\n".join(lines)


def _withheld_block(position: int, status: str, sentence: str) -> str:
    return f"{_open_tag(position, status)}{sentence}</preparation_chat>"


def _other_repository_block(position: int) -> str:
    return _withheld_block(
        position,
        "other_repository",
        "alan withheld this conversation because it is bound to a different repository "
        "than this chat.",
    )


def _not_found_block(position: int) -> str:
    return _withheld_block(
        position, "not_found", "alan could not resolve this link to a preparation conversation."
    )


def _outcome(
    position: int, ref: LinkRef, env: LinkEnv, engine_key: str | None
) -> tuple[_chat_core.PrepChatRead, str]:
    conversation = _resolve_safely(ref, env)
    if conversation is None:
        return _chat_core.PrepChatRead(status="not_found"), _not_found_block(position)
    if not _same_repository(engine_key, conversation.session.engine_key):
        return (
            _chat_core.PrepChatRead(status="other_repository"),
            _other_repository_block(position),
        )
    return (
        _chat_core.PrepChatRead(status="read", title=conversation.title),
        _read_block(position, conversation),
    )


def _is_self_link(ref: LinkRef, session_id: str) -> bool:
    return ref.kind != "workspace" and ref.ident == session_id


def attach(content: str, env: LinkEnv, *, engine_key: str | None, session_id: str) -> Attachment:
    refs = [ref for ref in extract_refs(content) if not _is_self_link(ref, session_id)]
    outcomes = [
        _outcome(position, ref, env, engine_key)
        for position, ref in enumerate(refs[:MAX_LINKS], start=1)
    ]
    return Attachment(
        reads=[read for read, _ in outcomes], blocks=[block for _, block in outcomes]
    )


def append_blocks(content: str, attachment: Attachment) -> str:
    if not attachment.blocks:
        return content
    return "\n\n".join([content, *attachment.blocks])
