from __future__ import annotations

from pathlib import Path

import pytest

from alan import prep_chat_link

_ARCHITECTURE = Path(__file__).resolve().parent.parent / "docs" / "architecture"
_RETIRED = (
    "PREP_CHAT_SCOPE_CLAUSE",
    "prep_chat_scope_clause",
    "build_no_impl",
    "explore_no_impl",
    "claude_cli_grants.configured_prep_chat_origin",
    "prep-chat read grant",
    "read grant",
    "fetch grant",
)


def _text(name: str) -> str:
    return " ".join((_ARCHITECTURE / name).read_text(encoding="utf-8").split())


@pytest.mark.parametrize(
    "fragment",
    [
        "## Reading a pasted preparation chat",
        "### Link grammar",
        "### Server-enforced repository scoping",
        "alan/prep_chat_link.py",
        "`alan.host_allowlist.allowed_hostnames()`",
        "`MAX_LINKS = 3`",
        f"`TRANSCRIPT_CHAR_CAP = {prep_chat_link.TRANSCRIPT_CHAR_CAP:_}`",
        "`truncated: K of N earlier messages omitted`",
        "`_chat_core.resolve_known_scope`",
        "`WorkspaceIndex.resolve_archived_prep_chat`",
        "`_chat_core.peek_session`",
        "`ChatMessage.prep_chat_reads`",
        "`other_repository`",
        "`not_found`",
        "`/workspaces/{id}/prep-chat`",
        "`/api/build-chat/sessions/{session}`",
        "`/api/explore-chat/sessions/{session}`",
    ],
)
def test_prep_chat_doc_describes_the_server_side_read_path(fragment: str) -> None:
    assert fragment in _text("prep_chat.md")


@pytest.mark.parametrize("heading", ["## Host allowlist", "## Non-goals"])
def test_prep_chat_doc_keeps_the_host_allowlist_anchors(heading: str) -> None:
    assert heading in (_ARCHITECTURE / "prep_chat.md").read_text(encoding="utf-8")


def test_prep_chat_doc_orders_the_host_allowlist_before_the_non_goals() -> None:
    text = (_ARCHITECTURE / "prep_chat.md").read_text(encoding="utf-8")
    assert text.index("## Host allowlist") < text.index("## Non-goals")


@pytest.mark.parametrize("name", ["build_chat.md", "explore_chat.md"])
def test_chat_docs_describe_the_static_attachment_clause(name: str) -> None:
    text = _text(name)
    assert "`PREP_CHAT_ATTACHMENT_CLAUSE`" in text
    assert "chat_prep_chat_attachment" in text
    assert "prep_chat_reads" in text or "<preparation_chat>" in text


def test_engine_config_doc_scopes_the_prep_chat_origin_to_the_host_allowlist() -> None:
    text = _text("engine_config.md")
    assert "read only by `alan.host_allowlist`" in text
    assert "prep-chat read grant" not in text


def test_the_architecture_readme_indexes_the_new_read_path() -> None:
    assert "server-side read path for a pasted preparation-chat link" in _text("README.md")


@pytest.mark.parametrize("path", sorted(_ARCHITECTURE.glob("*.md")), ids=lambda p: p.name)
def test_no_architecture_doc_describes_the_retired_fetch_route(path: Path) -> None:
    text = _text(path.name)
    assert not [name for name in _RETIRED if name in text]
