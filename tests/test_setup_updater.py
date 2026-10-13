from __future__ import annotations

import os
import re
from pathlib import Path

from tests._setup_api_stubs import sha256
from tests._setup_support import (
    BOT_SENTINEL,
    UPDATE_LABEL,
    Sandbox,
    Scenario,
    SetupRun,
    loaded_labels,
    make_sandbox,
    mutating_calls,
    preload_agent,
    run_setup,
    snapshot_files,
    stored_secrets,
)

UID = os.getuid()
DOMAIN = f"gui/{UID}"
TARGET = f"{DOMAIN}/{UPDATE_LABEL}"
FULL_STEPS = [
    "preflight",
    "install",
    "build",
    "profile",
    "start",
    "credential",
    "harden",
    "protect",
    "confirm",
    "verifier",
    "updater",
]
TYPED = BOT_SENTINEL + "\n"
RESUME_UPDATER = "Resume with: scripts/setup_macos.sh --from updater"
RESUME_VERIFIER = "Resume with: scripts/setup_macos.sh --from verifier"


def _sandbox(tmp_path: Path) -> Sandbox:
    return make_sandbox(tmp_path, finish_steps=True)


def _updater(sandbox: Sandbox, scenario: Scenario | None = None) -> SetupRun:
    return run_setup(sandbox, "--only", "updater", scenario=scenario)


def _marks(sandbox: Sandbox) -> list[str]:
    if not sandbox.state_file.exists():
        return []
    return sandbox.state_file.read_text(encoding="utf-8").splitlines()


def _launchctl_writes(run: SetupRun) -> list[tuple[str, ...]]:
    return [call.args for call in run.called("launchctl") if call.args[0] != "print"]


def test_the_updater_is_rendered_through_the_renderer_and_loaded_into_the_gui_domain(
    tmp_path: Path,
) -> None:
    sandbox = _sandbox(tmp_path)
    run = _updater(sandbox)
    assert run.returncode == 0, run.output
    plist = sandbox.plist_for(UPDATE_LABEL)
    assert _launchctl_writes(run) == [("enable", TARGET), ("bootstrap", DOMAIN, str(plist))]
    text = plist.read_text(encoding="utf-8")
    assert f"{sandbox.checkout}/deploy/update_runner.sh" in text
    assert "__ALAN_" not in text
    assert loaded_labels(sandbox) == [UPDATE_LABEL]
    assert "[done] rendered the ai.alan.update launch configuration" in run.stdout
    assert "[done] loaded ai.alan.update" in run.stdout
    assert "updater" in _marks(sandbox)


def test_only_the_update_agent_is_touched(tmp_path: Path) -> None:
    sandbox = _sandbox(tmp_path)
    run = _updater(sandbox)
    assert {call.args[1] for call in mutating_calls(run) if call.tool == "launchctl"} == {
        TARGET,
        DOMAIN,
    }
    assert not sandbox.plist.exists()
    assert run.called("curl") == []
    assert run.called("gh") == []


def test_a_rerun_with_the_updater_loaded_from_this_checkout_reports_already_and_writes_nothing(
    tmp_path: Path,
) -> None:
    sandbox = _sandbox(tmp_path)
    assert _updater(sandbox).returncode == 0
    before = snapshot_files(sandbox.plist_for(UPDATE_LABEL))
    second = _updater(sandbox)
    assert second.returncode == 0, second.output
    assert f"[already] {UPDATE_LABEL} is loaded from this checkout" in second.stdout
    assert _launchctl_writes(second) == []
    assert mutating_calls(second) == []
    assert snapshot_files(sandbox.plist_for(UPDATE_LABEL)) == before


def test_an_updater_loaded_by_someone_else_from_this_checkout_is_left_alone(
    tmp_path: Path,
) -> None:
    sandbox = _sandbox(tmp_path)
    preload_agent(sandbox, UPDATE_LABEL)
    run = _updater(sandbox)
    assert run.returncode == 0, run.output
    assert "[already]" in run.stdout
    assert _launchctl_writes(run) == []
    assert not sandbox.plist_for(UPDATE_LABEL).exists()


def test_a_failed_load_is_recorded_with_a_resume_line_and_is_not_marked(
    tmp_path: Path,
) -> None:
    sandbox = _sandbox(tmp_path)
    run = _updater(sandbox, Scenario(update_bootstrap_fails=True))
    assert run.returncode == 1
    assert f"[failed] launchctl could not load {UPDATE_LABEL}" in run.stdout
    assert run.stderr.strip().splitlines()[-1] == RESUME_UPDATER
    assert "setup: incomplete: updater did not complete" in run.stderr
    assert "updater" not in _marks(sandbox)
    assert loaded_labels(sandbox) == []


def test_a_failed_load_is_retried_by_the_next_run(tmp_path: Path) -> None:
    sandbox = _sandbox(tmp_path)
    assert _updater(sandbox, Scenario(update_bootstrap_fails=True)).returncode == 1
    fixed = _updater(sandbox)
    assert fixed.returncode == 0, fixed.output
    assert loaded_labels(sandbox) == [UPDATE_LABEL]
    assert "updater" in _marks(sandbox)


def test_a_renderer_that_fails_is_recorded_and_nothing_is_loaded(tmp_path: Path) -> None:
    sandbox = _sandbox(tmp_path)
    (sandbox.checkout / "deploy" / "ai.alan.update.plist").unlink()
    run = _updater(sandbox)
    assert run.returncode == 1
    assert "[failed] deploy/render_update_plist.sh failed" in run.stdout
    assert run.stderr.strip().splitlines()[-1] == RESUME_UPDATER
    assert _launchctl_writes(run) == []
    assert not sandbox.plist_for(UPDATE_LABEL).exists()


def test_a_rendered_file_that_is_not_a_valid_plist_is_recorded_and_not_loaded(
    tmp_path: Path,
) -> None:
    sandbox = _sandbox(tmp_path)
    run = _updater(sandbox, Scenario(plutil_fails=True))
    assert run.returncode == 1
    assert "[failed] the rendered updater file is not a valid plist" in run.stdout
    assert _launchctl_writes(run) == []
    assert run.stderr.strip().splitlines()[-1] == RESUME_UPDATER


def test_a_full_run_ends_with_the_verifier_stored_the_updater_loaded_and_a_shorter_pending_list(
    tmp_path: Path,
) -> None:
    sandbox = _sandbox(tmp_path)
    run = run_setup(sandbox, stdin=TYPED)
    assert run.returncode == 0, run.output
    assert run.headers() == FULL_STEPS
    assert _marks(sandbox) == FULL_STEPS
    assert [row["purpose"] for row in stored_secrets(sandbox)] == ["reviewer"]
    assert stored_secrets(sandbox)[0]["sha256"] == sha256(BOT_SENTINEL)
    assert loaded_labels(sandbox) == ["ai.alan", UPDATE_LABEL]
    closing = run.stdout.strip().splitlines()[-2:]
    assert "alan-factory is up" in closing[0]
    assert closing[1] == "Still pending: SonarCloud, the Claude check and the final report."


def test_a_full_run_that_fails_the_verifier_still_installs_the_updater(tmp_path: Path) -> None:
    sandbox = _sandbox(tmp_path)
    run = run_setup(sandbox, scenario=Scenario(bot_identity_kind="fine-grained"), stdin=TYPED)
    assert run.returncode == 1
    assert run.headers() == FULL_STEPS
    assert "verifier" not in _marks(sandbox)
    assert "updater" in _marks(sandbox)
    assert run.stderr.strip().splitlines()[-1] == RESUME_VERIFIER
    assert run.stdout.strip().splitlines()[-1] == (
        "Still pending: SonarCloud, the verifier bot, the Claude check and the final report."
    )


def test_a_second_full_run_changes_nothing(tmp_path: Path) -> None:
    sandbox = _sandbox(tmp_path)
    assert run_setup(sandbox, stdin=TYPED).returncode == 0
    watched = snapshot_files(
        sandbox.profile_file,
        sandbox.secrets_file,
        sandbox.state_file,
        sandbox.plist,
        sandbox.plist_for(UPDATE_LABEL),
    )
    second = run_setup(sandbox, scenario=Scenario(port_pids=(4242,)), stdin=None)
    assert second.returncode == 0, second.output
    assert mutating_calls(second) == []
    assert second.called("sleep") == []
    assert snapshot_files(*watched) == watched
    assert re.search(r"\[already\] ai\.alan\.update is loaded", second.stdout)
    assert "[already] reviewer credential and verifier login" in second.stdout
