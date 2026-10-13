from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

from tests._setup_support import (
    CLAUDE_LABEL,
    SERVICE_LABEL,
    UPDATE_LABEL,
    WAIT_BUDGET,
    Sandbox,
    Scenario,
    SetupRun,
    loaded_labels,
    make_sandbox,
    mutating_calls,
    preload_agent,
    run_setup,
    snapshot_files,
)

UID = os.getuid()
LABELS = (SERVICE_LABEL, UPDATE_LABEL)
STATES = ("loaded-with-file", "file-only", "absent")
COMPLETE_CLAIM = re.compile(r"setup (is |was |has )?complete", re.IGNORECASE)


def _agent_file(checkout: Path, label: str) -> str:
    runner = "update_runner.sh" if label == UPDATE_LABEL else "run_alan.sh"
    return (
        "<array>\n"
        "    <string>/bin/bash</string>\n"
        f"    <string>{checkout}/deploy/{runner}</string>\n"
        "</array>\n"
    )


def _install_agents(sandbox: Sandbox, states: dict[str, str]) -> None:
    sandbox.plist.parent.mkdir(parents=True, exist_ok=True)
    for label, state in states.items():
        if state == "loaded-with-file":
            preload_agent(sandbox, label)
        if state != "absent":
            sandbox.plist_for(label).write_text(
                _agent_file(sandbox.checkout, label), encoding="utf-8"
            )


def _installed(tmp_path: Path) -> Sandbox:
    sandbox = make_sandbox(tmp_path)
    first = run_setup(sandbox)
    assert first.returncode == 0, first.output
    preload_agent(sandbox, UPDATE_LABEL)
    sandbox.plist_for(UPDATE_LABEL).write_text(
        _agent_file(sandbox.checkout, UPDATE_LABEL), encoding="utf-8"
    )
    return sandbox


def _bootouts(run: SetupRun) -> list[str]:
    return [call.args[1] for call in run.called("launchctl", "bootout")]


@pytest.mark.parametrize("update_state", STATES)
@pytest.mark.parametrize("service_state", STATES)
def test_uninstall_removes_whichever_of_the_two_agents_are_present(
    tmp_path: Path, service_state: str, update_state: str
) -> None:
    sandbox = make_sandbox(tmp_path)
    states = {SERVICE_LABEL: service_state, UPDATE_LABEL: update_state}
    _install_agents(sandbox, states)
    run = run_setup(sandbox, "--uninstall")
    assert run.returncode == 0, run.output
    assert _bootouts(run) == [
        f"gui/{UID}/{label}" for label in LABELS if states[label] == "loaded-with-file"
    ]
    assert [call for call in mutating_calls(run) if call.tool != "launchctl"] == []
    assert not sandbox.plist_for(SERVICE_LABEL).exists()
    assert not sandbox.plist_for(UPDATE_LABEL).exists()
    assert loaded_labels(sandbox) == []
    for label in LABELS:
        expected = "[already]" if states[label] == "absent" else "[done]"
        assert f"{expected} " in run.stdout
        line = next(line for line in run.stdout.splitlines() if label in line and "[" in line)
        assert line.startswith(f"  {expected}")


def test_uninstall_with_nothing_installed_is_not_an_error(tmp_path: Path) -> None:
    sandbox = make_sandbox(tmp_path)
    run = run_setup(sandbox, "--uninstall")
    assert run.returncode == 0, run.output
    assert mutating_calls(run) == []
    assert "[already] ai.alan is not installed" in run.stdout
    assert "[already] ai.alan.update is not installed" in run.stdout


def test_uninstall_keeps_the_alan_state_the_setup_state_and_the_clone(tmp_path: Path) -> None:
    sandbox = _installed(tmp_path)
    kept = (
        sandbox.profile_file,
        sandbox.secrets_file,
        sandbox.state_file,
        sandbox.queue_db,
        sandbox.token_file,
    )
    before = snapshot_files(*kept)
    run = run_setup(sandbox, "--uninstall")
    assert run.returncode == 0, run.output
    assert snapshot_files(*kept) == before
    assert (sandbox.checkout / ".git").is_dir()
    assert (sandbox.checkout / "scripts" / "setup_macos.sh").is_file()
    assert "Kept in place" in run.stdout


def test_uninstall_prints_the_manual_cleanup_list(tmp_path: Path) -> None:
    sandbox = make_sandbox(tmp_path)
    run = run_setup(sandbox, "--uninstall")
    assert "the SonarCloud project for your repository" in run.stdout
    assert "the GitHub apps installed on your repository" in run.stdout
    assert "the verifier bot account and its access token" in run.stdout
    assert "the transport token you created for your repository" in run.stdout
    assert "Revoke" in run.stdout
    assert "alan-factory's copy stays in" in run.stdout


def test_uninstall_removes_the_updater_agent_the_updater_step_installed(tmp_path: Path) -> None:
    sandbox = make_sandbox(tmp_path, finish_steps=True)
    installed = run_setup(sandbox, "--only", "updater")
    assert installed.returncode == 0, installed.output
    assert loaded_labels(sandbox) == [UPDATE_LABEL]
    assert sandbox.plist_for(UPDATE_LABEL).is_file()
    run = run_setup(sandbox, "--uninstall")
    assert run.returncode == 0, run.output
    assert _bootouts(run) == [f"gui/{UID}/{UPDATE_LABEL}"]
    assert loaded_labels(sandbox) == []
    assert not sandbox.plist_for(UPDATE_LABEL).exists()
    assert f"[done] removed {UPDATE_LABEL}" in run.stdout
    assert f"[already] {SERVICE_LABEL} is not installed" in run.stdout


def test_uninstall_keeps_the_stored_reviewer_credential_and_the_login_line(
    tmp_path: Path,
) -> None:
    sandbox = make_sandbox(tmp_path, finish_steps=True)
    sandbox.alan_dir.mkdir(parents=True)
    sandbox.secrets_file.write_text("ALAN_VERIFIER_LOGIN=example-verifier\n", encoding="utf-8")
    before = snapshot_files(sandbox.secrets_file)
    run = run_setup(sandbox, "--uninstall")
    assert run.returncode == 0, run.output
    assert snapshot_files(sandbox.secrets_file) == before
    assert "the bot's token copy in alan-factory's secret store" in run.stdout
    assert "ALAN_VERIFIER_LOGIN line" in run.stdout
    assert "both stay until you delete them" in run.stdout


def test_uninstall_only_boots_out_and_removes_and_never_says_setup_is_complete(
    tmp_path: Path,
) -> None:
    sandbox = _installed(tmp_path)
    run = run_setup(sandbox, "--uninstall")
    assert {call.tool for call in mutating_calls(run)} == {"launchctl"}
    assert {call.args[0] for call in mutating_calls(run)} == {"bootout"}
    assert COMPLETE_CLAIM.search(run.output) is None


def test_uninstall_boots_out_a_leftover_claude_check_agent_and_deletes_its_stray_file(
    tmp_path: Path,
) -> None:
    sandbox = make_sandbox(tmp_path)
    preload_agent(sandbox, CLAUDE_LABEL)
    stray = sandbox.plist_for(CLAUDE_LABEL)
    stray.parent.mkdir(parents=True, exist_ok=True)
    stray.write_text("stale\n", encoding="utf-8")
    run = run_setup(sandbox, "--uninstall")
    assert run.returncode == 0, run.output
    assert _bootouts(run) == [f"gui/{UID}/{CLAUDE_LABEL}"]
    assert not stray.exists()
    assert loaded_labels(sandbox) == []
    assert COMPLETE_CLAIM.search(run.output) is None


def test_uninstall_without_a_claude_check_agent_boots_out_nothing_for_it(tmp_path: Path) -> None:
    sandbox = _installed(tmp_path)
    run = run_setup(sandbox, "--uninstall")
    assert f"gui/{UID}/{CLAUDE_LABEL}" not in _bootouts(run)


def test_uninstall_leaves_launch_agents_it_does_not_own_alone(tmp_path: Path) -> None:
    sandbox = _installed(tmp_path)
    other = sandbox.plist_for("com.example.other")
    other.write_text("other plist\n", encoding="utf-8")
    before = snapshot_files(other)
    run_setup(sandbox, "--uninstall")
    assert snapshot_files(other) == before


def test_a_later_setup_run_is_not_refused_as_a_foreign_install(tmp_path: Path) -> None:
    sandbox = _installed(tmp_path)
    assert run_setup(sandbox, "--uninstall").returncode == 0
    later = run_setup(sandbox, "--only", "preflight")
    assert later.returncode == 0, later.output
    assert "existing-install" not in later.output


def test_a_full_setup_run_after_uninstall_starts_the_service_again(tmp_path: Path) -> None:
    sandbox = _installed(tmp_path)
    assert run_setup(sandbox, "--uninstall").returncode == 0
    again = run_setup(sandbox)
    assert again.returncode == 0, again.output
    assert loaded_labels(sandbox) == [SERVICE_LABEL]
    assert "existing-install" not in again.output


def test_an_agent_that_stays_loaded_is_reported_after_a_bounded_wait(tmp_path: Path) -> None:
    sandbox = make_sandbox(tmp_path)
    _install_agents(sandbox, {SERVICE_LABEL: "loaded-with-file", UPDATE_LABEL: "absent"})
    run = run_setup(sandbox, "--uninstall", scenario=Scenario(bootout_keeps_loaded=True))
    assert run.returncode == 1
    assert "refused at uninstall: unload:" in run.stderr
    assert len(run.called("sleep")) == WAIT_BUDGET
    assert sandbox.plist_for(SERVICE_LABEL).exists()


@pytest.mark.parametrize("label", LABELS)
def test_an_unloaded_agent_file_that_points_at_another_checkout_is_refused_and_kept(
    tmp_path: Path, label: str
) -> None:
    sandbox = make_sandbox(tmp_path)
    sandbox.plist.parent.mkdir(parents=True)
    other = sandbox.elsewhere()
    sandbox.plist_for(label).write_text(_agent_file(other, label), encoding="utf-8")
    before = snapshot_files(sandbox.plist_for(label))
    run = run_setup(sandbox, "--uninstall")
    assert run.returncode == 1
    assert "refused at uninstall: foreign-agent-file:" in run.stderr
    assert snapshot_files(sandbox.plist_for(label)) == before
    assert mutating_calls(run) == []


@pytest.mark.parametrize("label", LABELS)
def test_an_unloaded_agent_file_setup_cannot_read_a_checkout_from_is_refused_and_kept(
    tmp_path: Path, label: str
) -> None:
    sandbox = make_sandbox(tmp_path)
    sandbox.plist.parent.mkdir(parents=True)
    sandbox.plist_for(label).write_text(f"{label} plist\n", encoding="utf-8")
    run = run_setup(sandbox, "--uninstall")
    assert run.returncode == 1
    assert "refused at uninstall: foreign-agent-file:" in run.stderr
    assert sandbox.plist_for(label).read_text(encoding="utf-8") == f"{label} plist\n"


def test_an_unloaded_agent_file_naming_this_checkout_through_a_symlink_is_removed(
    tmp_path: Path,
) -> None:
    sandbox = make_sandbox(tmp_path)
    sandbox.plist.parent.mkdir(parents=True)
    sandbox.plist.write_text(_agent_file(sandbox.alias(), SERVICE_LABEL), encoding="utf-8")
    run = run_setup(sandbox, "--uninstall")
    assert run.returncode == 0, run.output
    assert not sandbox.plist.exists()
    assert f"[done] removed {SERVICE_LABEL}" in run.stdout


def test_a_dangling_agent_symlink_is_refused_and_kept(tmp_path: Path) -> None:
    sandbox = make_sandbox(tmp_path)
    sandbox.plist.parent.mkdir(parents=True)
    sandbox.plist.symlink_to(sandbox.elsewhere() / "missing.plist")
    run = run_setup(sandbox, "--uninstall")
    assert run.returncode == 1
    assert "refused at uninstall: foreign-agent-file:" in run.stderr
    assert sandbox.plist.is_symlink()
