from __future__ import annotations

import pytest

from alan import claude_cli
from alan.claude_cli import run_agent_streaming
from alan.claude_cli_grants import (
    credential_deny_rules,
    disallowed_tools_args,
    grant_args,
)
from alan.engine_config import default_engine_config
from alan.teams import _base as base
from alan.teams._base import WorkerOverrides
from tests.test_claude_cli_streaming import _lines, _patch_popen, _result_event


def test_grant_args_emits_allowed_tools_then_add_dirs() -> None:
    assert grant_args(
        allowed_tools=["Read"], add_dirs=["/live/.alan"], deny_all_tools=False
    ) == ["--allowedTools", "Read", "--add-dir", "/live/.alan"]


def test_grant_args_empty_when_nothing_granted() -> None:
    assert grant_args(allowed_tools=None, add_dirs=None, deny_all_tools=False) == []


def test_grant_args_deny_all_tools_suppresses_every_grant() -> None:
    assert (
        grant_args(
            allowed_tools=["Read"], add_dirs=["/live/.alan"], deny_all_tools=True
        )
        == []
    )


def test_disallowed_tools_args_deny_all_overrides_permission_mode() -> None:
    for mode in ("auto", "acceptEdits"):
        assert disallowed_tools_args(deny_all_tools=True, permission_mode=mode) == [
            "--disallowedTools",
            "*",
        ]


def test_disallowed_tools_args_blocks_write_tools_outside_accept_edits() -> None:
    args = disallowed_tools_args(deny_all_tools=False, permission_mode="auto")
    assert args[:2] == ["--disallowedTools", "Write Edit NotebookEdit"]
    assert args[2:] == credential_deny_rules()


def test_disallowed_tools_args_carry_only_credential_rules_under_accept_edits() -> None:
    args = disallowed_tools_args(deny_all_tools=False, permission_mode="acceptEdits")
    assert args == ["--disallowedTools", *credential_deny_rules()]


def test_build_cmd_add_dirs_emitted_as_discrete_argv_tokens() -> None:
    cmd = claude_cli._build_cmd(  # noqa: SLF001
        "sys",
        output_format="json",
        max_turns=1,
        add_dirs=["/live/.alan", "/live/other"],
    )
    idx = cmd.index("--add-dir")
    assert cmd[idx + 1] == "/live/.alan"
    assert cmd[idx + 2] == "/live/other"


def test_build_cmd_omits_add_dir_flag_by_default() -> None:
    cmd = claude_cli._build_cmd(  # noqa: SLF001
        "sys",
        output_format="json",
        max_turns=1,
    )
    assert "--add-dir" not in cmd


def test_build_cmd_deny_all_tools_skips_add_dirs() -> None:
    cmd = claude_cli._build_cmd(  # noqa: SLF001
        "sys",
        output_format="json",
        max_turns=1,
        add_dirs=["/live/.alan"],
        deny_all_tools=True,
    )
    assert "--add-dir" not in cmd


def test_streaming_omits_add_dir_flag_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cmds: list[list[str]] = []
    _patch_popen(
        monkeypatch,
        stdout_lines=_lines(_result_event("ok")),
        capture_cmds=cmds,
    )
    run_agent_streaming("sys", "user")
    assert "--add-dir" not in cmds[0]


def test_streaming_appends_add_dirs_when_provided(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cmds: list[list[str]] = []
    _patch_popen(
        monkeypatch,
        stdout_lines=_lines(_result_event("ok")),
        capture_cmds=cmds,
    )
    run_agent_streaming("sys", "user", add_dirs=["/live/.alan"])
    cmd = cmds[0]
    assert cmd[cmd.index("--add-dir") + 1] == "/live/.alan"


def test_overrides_to_kwargs_omits_add_dirs_when_unset() -> None:
    kwargs = base._overrides_to_kwargs(WorkerOverrides())  # noqa: SLF001
    assert "add_dirs" not in kwargs


def test_overrides_to_kwargs_passes_add_dirs_through() -> None:
    kwargs = base._overrides_to_kwargs(  # noqa: SLF001
        WorkerOverrides(add_dirs=["/live/.alan"])
    )
    assert kwargs["add_dirs"] == ["/live/.alan"]


def test_default_team_tools_grants_maintenance_read_probes() -> None:
    cfg = default_engine_config()
    assert cfg.team_tools["maintenance"] == [
        "Bash(gh run list:*)",
        "Bash(ps:*)",
        "Bash(stat:*)",
        "Bash(find:*)",
    ]
