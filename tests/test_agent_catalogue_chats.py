from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from alan import agent_catalogue, agent_key, engine_config
from alan.agent_catalogue_flags import agent_flags
from alan.agent_catalogue_helpers import ResolvedAgent, to_entry
from alan.agent_catalogue_models import AgentEntry
from alan.agent_catalogue_standalone_chats import CHAT_MAX_TURNS, CHAT_MODEL, GROUPS
from alan.claude_cli import (
    _DEFAULT_CHAT_ALLOWED_TOOLS,
    BUILD_SUMMARIZE_SENTINEL,
    CONSULTANTS,
    DISPATCH_PROPOSAL_SENTINEL,
    EXPLORE_SUMMARIZE_SENTINEL,
    ConsultantInfo,
)
from alan.engine_config import EngineConfig, RoleConfig, default_engine_config
from tests.agent_catalogue_standalone_drivers import STANDALONE_DRIVERS, parity_failures
from tests.conftest import register_test_engine

_EXPLORE_KEYS = [agent_key.explore_chat(c.key) for c in CONSULTANTS]
_CHAT_KEYS = [agent_key.BUILD_CHAT, *_EXPLORE_KEYS]

_VARIANTS: dict[str, Callable[[], EngineConfig]] = {
    "default": default_engine_config,
    "customised": lambda: default_engine_config().model_copy(
        update={
            "roles": {
                **default_engine_config().roles,
                "judge": RoleConfig(model="haiku", effort="low"),
                "implementer": RoleConfig(model="haiku", effort="low"),
            }
        }
    ),
}


def _resolved(cfg: EngineConfig) -> dict[str, ResolvedAgent]:
    resolved, errors = agent_catalogue.resolve_all(cfg, GROUPS)
    assert errors == []
    return {agent.key.id: agent for agent in resolved}


def _entry(key: agent_key.AgentKey, cfg: EngineConfig | None = None) -> AgentEntry:
    cfg = cfg or default_engine_config()
    agent = _resolved(cfg)[key.id]
    return to_entry(agent, agent_flags(agent, cfg))


def _labels(entry: AgentEntry, kind: str) -> set[str]:
    return {segment.label for segment in entry.prompt if segment.kind == kind}


def test_the_chat_groups_cover_build_chat_and_every_consultant() -> None:
    assert [group.key for group in GROUPS] == _CHAT_KEYS
    assert [group.id for group in GROUPS] == [key.id for key in _CHAT_KEYS]


@pytest.mark.parametrize("variant", sorted(_VARIANTS))
@pytest.mark.parametrize("key", _CHAT_KEYS, ids=lambda key: key.id)
def test_chats_run_on_the_chat_turn_defaults_whatever_the_engine_roles_say(
    variant: str, key: agent_key.AgentKey
) -> None:
    entry = _entry(key, _VARIANTS[variant]())
    assert (entry.model.value, entry.model.source) == (CHAT_MODEL, "builtin")
    assert (entry.effort.value, entry.effort.source) == ("xhigh", "builtin")
    assert entry.turn_limit.max_turns == CHAT_MAX_TURNS == 80
    assert entry.permission_mode == "auto"
    assert entry.flags == []


@pytest.mark.parametrize("key", _CHAT_KEYS, ids=lambda key: key.id)
def test_chat_specs_do_not_move_when_the_engine_roles_change(key: agent_key.AgentKey) -> None:
    default = _resolved(_VARIANTS["default"]())[key.id]
    customised = _resolved(_VARIANTS["customised"]())[key.id]
    assert default.spec == customised.spec


@pytest.mark.parametrize("key", _CHAT_KEYS, ids=lambda key: key.id)
def test_chats_get_the_default_read_only_chat_tools_and_no_write_tools(
    key: agent_key.AgentKey,
) -> None:
    entry = _entry(key)
    assert entry.tools.effective == list(_DEFAULT_CHAT_ALLOWED_TOOLS)
    assert {"Write", "Edit", "NotebookEdit"} <= set(entry.tools.denied)
    assert entry.add_dirs == []


@pytest.mark.parametrize("key", _CHAT_KEYS, ids=lambda key: key.id)
def test_chats_resume_one_session_per_message_under_the_chat_turn_timeout(
    key: agent_key.AgentKey,
) -> None:
    entry = _entry(key)
    assert entry.session.behaviour == "per_message"
    assert (entry.timeouts.idle_seconds, entry.timeouts.absolute_seconds) == (600.0, 14400.0)
    assert entry.process.detached is False
    assert entry.process.priority_delta is None
    assert entry.kind == "chat"
    assert entry.scope == "repo"


def test_the_build_chat_prompt_labels_the_repository_and_the_optional_shop_stats() -> None:
    entry = _entry(agent_key.BUILD_CHAT)
    placeholders = {s.label: s for s in entry.prompt if s.kind == "placeholder"}
    assert set(placeholders) == {"CHAT_REPO_LABEL", "BUILD_CHAT_SHOP_STATS"}
    assert "bound to" in str(placeholders["CHAT_REPO_LABEL"].description)
    assert "only when" in str(placeholders["BUILD_CHAT_SHOP_STATS"].description)
    assert entry.prompt[-1].label == "BUILD_CHAT_SHOP_STATS"


def test_the_build_chat_prompt_labels_the_shared_blocks() -> None:
    entry = _entry(agent_key.BUILD_CHAT)
    shared = {s.label for s in entry.prompt if s.shared}
    assert shared == {
        "untrusted_content_notice",
        "chat_ux_lens",
        "chat_issue_scope",
        "chat_prep_chat_attachment",
    }


@pytest.mark.parametrize("consultant", CONSULTANTS, ids=lambda c: c.key)
def test_an_explore_chat_prompt_carries_the_ux_lens_only_for_the_consultants_that_have_it(
    consultant: ConsultantInfo,
) -> None:
    entry = _entry(agent_key.explore_chat(consultant.key))
    shared = {s.label for s in entry.prompt if s.shared}
    assert ("chat_ux_lens" in shared) is consultant.carries_ux_lens
    assert {"untrusted_content_notice", "chat_issue_scope", "chat_prep_chat_attachment"} <= shared
    assert _labels(entry, "placeholder") <= {"CHAT_REPO_LABEL"}
    assert entry.tags == ["consultant"]
    assert entry.name == f"Explore chat / {consultant.label}"


@pytest.mark.parametrize(
    ("key", "sentinels"),
    [
        (agent_key.BUILD_CHAT, (BUILD_SUMMARIZE_SENTINEL, DISPATCH_PROPOSAL_SENTINEL)),
        (_EXPLORE_KEYS[0], (EXPLORE_SUMMARIZE_SENTINEL, DISPATCH_PROPOSAL_SENTINEL)),
    ],
    ids=["build", "explore"],
)
def test_the_user_message_is_one_labelled_placeholder_naming_the_fixed_commands(
    key: agent_key.AgentKey, sentinels: tuple[str, ...]
) -> None:
    (segment,) = _entry(key).user_message
    assert segment.kind == "placeholder"
    assert all(sentinel in str(segment.description) for sentinel in sentinels)
    assert "correction message" in str(segment.description)


def test_the_working_directory_is_alans_own_checkout_for_the_install_repository() -> None:
    entry = _entry(agent_key.BUILD_CHAT)
    assert "no per-chat checkout" in entry.working_directory


def test_the_working_directory_is_a_per_chat_clone_of_another_viewed_repository() -> None:
    identity = register_test_engine("acme/widget")
    cfg = engine_config.lookup_by_repo(identity)
    assert cfg is not None
    entry = _entry(agent_key.BUILD_CHAT, cfg)
    assert entry.working_directory.startswith("A per-chat clone of acme/widget")


@pytest.mark.parametrize("variant", sorted(_VARIANTS))
def test_the_catalogue_matches_what_the_real_chat_turns_launch(
    variant: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    chat_drivers = [d for d in STANDALONE_DRIVERS if d.name in {"build chat", "explore chats"}]
    failures = parity_failures(_VARIANTS[variant](), monkeypatch, tmp_path, GROUPS, chat_drivers)
    assert failures == []


def test_a_chat_entry_serialises_and_reads_back() -> None:
    entry = _entry(agent_key.BUILD_CHAT)
    assert AgentEntry.model_validate_json(entry.model_dump_json()) == entry
