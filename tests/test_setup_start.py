from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from alan import default_profile_writer
from tests._setup_support import (
    EXAMPLE_ORIGIN,
    TOKEN_SENTINEL,
    WAIT_BUDGET,
    Sandbox,
    Scenario,
    SetupRun,
    all_output,
    git,
    make_sandbox,
    mutating_calls,
    plist_slot_source,
    preload_agent,
    prepare_for_start,
    run_setup,
    seed_existing_install,
    seed_state,
    snapshot_files,
)

UID = os.getuid()
TARGET = f"gui/{UID}/ai.alan"
STALE_SHA = "0" * 40
WRONG_SHA = "f" * 40
EXAMPLE_SLOT_SOURCE = "https://github.com/example-owner/example-repo.git"
UNUSABLE_ORIGIN = "https://gitlab.example/x/y.git"


def _ready(tmp_path: Path, origin: str = EXAMPLE_ORIGIN) -> Sandbox:
    sandbox = make_sandbox(tmp_path, origin=origin)
    prepare_for_start(sandbox)
    return sandbox


def _running(sandbox: Sandbox, served_sha: str = "@head") -> None:
    preload_agent(sandbox, served_sha=served_sha)
    sandbox.token_file.parent.mkdir(parents=True, exist_ok=True)
    sandbox.token_file.write_text(TOKEN_SENTINEL, encoding="utf-8")
    seed_existing_install(sandbox)


def _start(sandbox: Sandbox, scenario: Scenario | None = None) -> SetupRun:
    return run_setup(sandbox, "--only", "start", scenario=scenario)


def _launchctl_writes(run: SetupRun) -> list[tuple[str, ...]]:
    return [call.args for call in run.called("launchctl") if call.args[0] != "print"]


def _marks(sandbox: Sandbox) -> list[str]:
    return sandbox.state_file.read_text(encoding="utf-8").splitlines()


def test_the_secrets_file_is_created_empty_with_mode_0600(tmp_path: Path) -> None:
    sandbox = _ready(tmp_path)
    run = _start(sandbox)
    assert run.returncode == 0, run.output
    assert stat.S_IMODE(sandbox.secrets_file.stat().st_mode) == 0o600
    assert sandbox.secrets_file.read_text(encoding="utf-8") == ""
    assert "[done] secrets file created with mode 600" in run.stdout


def test_an_existing_secrets_file_keeps_its_contents_and_is_tightened_to_0600(
    tmp_path: Path,
) -> None:
    sandbox = _ready(tmp_path)
    sandbox.secrets_file.write_text("EXAMPLE_KEY=example-value\n", encoding="utf-8")
    sandbox.secrets_file.chmod(0o644)
    run = _start(sandbox)
    assert run.returncode == 0, run.output
    assert sandbox.secrets_file.read_text(encoding="utf-8") == "EXAMPLE_KEY=example-value\n"
    assert stat.S_IMODE(sandbox.secrets_file.stat().st_mode) == 0o600
    assert "[done] secrets file mode set to 600 (contents kept)" in run.stdout


def test_a_secrets_file_that_already_has_mode_0600_is_left_untouched(tmp_path: Path) -> None:
    sandbox = _ready(tmp_path)
    sandbox.secrets_file.write_text("EXAMPLE_KEY=example-value\n", encoding="utf-8")
    sandbox.secrets_file.chmod(0o600)
    before = snapshot_files(sandbox.secrets_file)
    run = _start(sandbox)
    assert "[already] secrets file with mode 600" in run.stdout
    assert snapshot_files(sandbox.secrets_file) == before


@pytest.mark.parametrize("target_exists", [False, True], ids=["dangling", "live"])
def test_a_secrets_file_that_is_a_symlink_is_refused_and_its_target_left_alone(
    tmp_path: Path, target_exists: bool
) -> None:
    sandbox = _ready(tmp_path)
    target = sandbox.elsewhere() / "linked-secrets.env"
    if target_exists:
        target.write_text("EXAMPLE_KEY=example-value\n", encoding="utf-8")
        target.chmod(0o644)
    sandbox.secrets_file.symlink_to(target)
    run = _start(sandbox)
    assert run.returncode == 1
    assert "refused at start: secrets-file:" in run.stderr
    assert sandbox.secrets_file.is_symlink()
    assert target.exists() is target_exists
    if target_exists:
        assert stat.S_IMODE(target.stat().st_mode) == 0o644
        assert target.read_text(encoding="utf-8") == "EXAMPLE_KEY=example-value\n"
    assert _launchctl_writes(run) == []
    assert not sandbox.plist.exists()


def test_the_log_directory_exists_before_the_service_is_loaded(tmp_path: Path) -> None:
    sandbox = _ready(tmp_path)
    assert not sandbox.logs_dir.exists()
    run = _start(sandbox)
    assert run.returncode == 0, run.output
    assert sandbox.bootstrap_snapshots()[0]["log_dir_exists"] is True


def test_the_rendered_service_clones_worker_slots_from_the_users_repo(tmp_path: Path) -> None:
    sandbox = _ready(tmp_path)
    run = _start(sandbox)
    assert run.returncode == 0, run.output
    assert plist_slot_source(sandbox.plist) == EXAMPLE_SLOT_SOURCE


def test_the_rendered_service_runs_this_checkouts_launcher(tmp_path: Path) -> None:
    sandbox = _ready(tmp_path)
    _start(sandbox)
    assert f"{sandbox.checkout}/deploy/run_alan.sh" in sandbox.plist.read_text(encoding="utf-8")


def _break_origin(sandbox: Sandbox, url: str = UNUSABLE_ORIGIN) -> None:
    git(sandbox.checkout, "remote", "set-url", "origin", url, home=sandbox.home)


def test_an_unusable_origin_exits_non_zero_and_leaves_no_service_file(tmp_path: Path) -> None:
    sandbox = _ready(tmp_path)
    _break_origin(sandbox)
    run = _start(sandbox)
    assert run.returncode == 1
    assert "refused at start: renderer:" in run.stderr
    assert "remote set-url origin" in run.stderr
    assert not sandbox.plist.exists()
    assert _launchctl_writes(run) == []
    assert mutating_calls(run) == []


def test_an_unusable_origin_never_echoes_its_credential(tmp_path: Path) -> None:
    sandbox = _ready(tmp_path)
    _break_origin(sandbox, f"https://user:{TOKEN_SENTINEL}@gitlab.example/x/y.git")
    run = _start(sandbox)
    assert run.returncode == 1
    assert TOKEN_SENTINEL not in all_output(run)


def test_an_unusable_origin_restores_the_previous_service_file_byte_identical(
    tmp_path: Path,
) -> None:
    sandbox = _ready(tmp_path)
    _break_origin(sandbox)
    sandbox.plist.parent.mkdir(parents=True)
    sandbox.plist.write_text("previous service file\n", encoding="utf-8")
    before = snapshot_files(sandbox.plist)
    run = _start(sandbox)
    assert run.returncode == 1
    assert snapshot_files(sandbox.plist) == before
    assert _launchctl_writes(run) == []


def test_an_unusable_origin_restores_a_symlinked_service_file_as_the_same_symlink(
    tmp_path: Path,
) -> None:
    sandbox = _ready(tmp_path)
    _break_origin(sandbox)
    target = sandbox.elsewhere() / "previous-service-file.plist"
    target.write_text("previous service file\n", encoding="utf-8")
    sandbox.plist.parent.mkdir(parents=True)
    sandbox.plist.symlink_to(target)
    before = snapshot_files(target)
    run = _start(sandbox)
    assert run.returncode == 1
    assert sandbox.plist.is_symlink()
    assert os.readlink(sandbox.plist) == str(target)
    assert snapshot_files(target) == before
    assert _launchctl_writes(run) == []


def test_a_rejected_plist_restores_a_dangling_service_symlink_as_the_same_symlink(
    tmp_path: Path,
) -> None:
    sandbox = _ready(tmp_path)
    target = sandbox.elsewhere() / "missing-service-file.plist"
    sandbox.plist.parent.mkdir(parents=True)
    sandbox.plist.symlink_to(target)
    run = _start(sandbox, Scenario(plutil_fails=True))
    assert run.returncode == 1
    assert "refused at start: renderer:" in run.stderr
    assert sandbox.plist.is_symlink()
    assert os.readlink(sandbox.plist) == str(target)
    assert not target.exists()


def test_a_rendered_file_that_is_not_a_valid_plist_is_rejected_and_the_previous_one_restored(
    tmp_path: Path,
) -> None:
    sandbox = _ready(tmp_path)
    sandbox.plist.parent.mkdir(parents=True)
    sandbox.plist.write_text("previous service file\n", encoding="utf-8")
    before = snapshot_files(sandbox.plist)
    run = _start(sandbox, Scenario(plutil_fails=True))
    assert run.returncode == 1
    assert "refused at start: renderer:" in run.stderr
    assert snapshot_files(sandbox.plist) == before
    assert _launchctl_writes(run) == []


def test_a_service_that_is_not_loaded_is_enabled_bootstrapped_then_kickstarted_with_p(
    tmp_path: Path,
) -> None:
    sandbox = _ready(tmp_path)
    run = _start(sandbox)
    assert run.returncode == 0, run.output
    assert _launchctl_writes(run) == [
        ("enable", TARGET),
        ("bootstrap", f"gui/{UID}", str(sandbox.plist)),
        ("kickstart", "-p", TARGET),
    ]


def test_a_service_loaded_here_healthy_and_at_head_is_left_alone(tmp_path: Path) -> None:
    sandbox = _ready(tmp_path)
    _running(sandbox)
    run = _start(sandbox)
    assert run.returncode == 0, run.output
    assert _launchctl_writes(run) == []
    assert not sandbox.plist.exists()
    assert f"[already] ai.alan is loaded from this checkout and serves commit {sandbox.head()}" in (
        run.stdout
    )


def test_a_service_reporting_a_different_sha_is_restarted_exactly_once(tmp_path: Path) -> None:
    sandbox = _ready(tmp_path)
    _running(sandbox, served_sha=STALE_SHA)
    run = _start(sandbox)
    assert run.returncode == 0, run.output
    assert _launchctl_writes(run) == [("kickstart", "-k", TARGET)]
    assert f"serving commit {sandbox.head()}" in run.stdout


def test_a_service_that_stays_on_the_wrong_sha_after_its_one_restart_fails_pointing_at_the_log(
    tmp_path: Path,
) -> None:
    sandbox = _ready(tmp_path)
    _running(sandbox, served_sha=STALE_SHA)
    run = _start(sandbox, Scenario(served_sha_after_restart=WRONG_SHA))
    assert run.returncode == 1
    assert _launchctl_writes(run) == [("kickstart", "-k", TARGET)]
    assert "refused at start: health:" in run.stderr
    assert WRONG_SHA in run.stderr
    assert sandbox.head() in run.stderr
    assert "alan.error.log" in run.stderr
    assert "start" not in _marks(sandbox)


def test_a_service_that_never_answers_times_out_and_points_at_the_log(tmp_path: Path) -> None:
    sandbox = _ready(tmp_path)
    run = _start(sandbox, Scenario(health_never=True))
    assert run.returncode == 1
    assert "refused at start: health:" in run.stderr
    assert "no healthy answer" in run.stderr
    assert "alan.error.log" in run.stderr
    assert len(run.called("sleep")) == WAIT_BUDGET
    assert "start" not in _marks(sandbox)


def test_a_fresh_load_that_serves_the_wrong_commit_fails_without_a_restart(
    tmp_path: Path,
) -> None:
    sandbox = _ready(tmp_path)
    run = _start(sandbox, Scenario(served_sha_after_bootstrap=WRONG_SHA))
    assert run.returncode == 1
    assert [args[:2] for args in _launchctl_writes(run)] == [
        ("enable", TARGET),
        ("bootstrap", f"gui/{UID}"),
        ("kickstart", "-p"),
    ]
    assert "start" not in _marks(sandbox)


@pytest.mark.parametrize(
    "body",
    ["not json at all", '{"ok": true, "served_sha": null}', '{"ok": false, "served_sha": "x"}'],
    ids=["garbage", "null-sha", "not-ok"],
)
def test_an_unusable_health_answer_counts_as_unhealthy(tmp_path: Path, body: str) -> None:
    sandbox = _ready(tmp_path)
    run = _start(sandbox, Scenario(health_body=body))
    assert run.returncode == 1
    assert "refused at start: health:" in run.stderr


def test_setup_prints_the_control_token_path_and_never_its_value(tmp_path: Path) -> None:
    sandbox = _ready(tmp_path)
    run = _start(sandbox)
    assert run.returncode == 0, run.output
    assert sandbox.token_file.read_text(encoding="utf-8") == TOKEN_SENTINEL
    assert f"control token is at {sandbox.token_file}" in run.stdout
    assert TOKEN_SENTINEL not in all_output(run)
    assert TOKEN_SENTINEL.lower() not in all_output(run).lower()


def test_a_missing_control_token_times_out_and_points_at_the_log(tmp_path: Path) -> None:
    sandbox = _ready(tmp_path)
    run = _start(sandbox, Scenario(no_token=True))
    assert run.returncode == 1
    assert "refused at start: control-token:" in run.stderr
    assert "alan.error.log" in run.stderr
    assert len(run.called("sleep")) == WAIT_BUDGET
    assert "start" not in _marks(sandbox)


def test_success_requires_health_ok_at_the_clones_head_and_records_the_start_mark(
    tmp_path: Path,
) -> None:
    sandbox = _ready(tmp_path)
    run = _start(sandbox)
    assert f"[ok] health ok, serving commit {sandbox.head()}" in run.stdout
    assert _marks(sandbox) == ["build", "profile", "start"]


def test_success_seeds_the_deploy_marker_with_the_served_head(tmp_path: Path) -> None:
    sandbox = _ready(tmp_path)
    _start(sandbox)
    marker = sandbox.alan_dir / "deploy" / "restarted_sha"
    assert marker.read_text(encoding="utf-8") == f"{sandbox.head()}\n"


def test_a_service_serving_another_commit_seeds_no_deploy_marker(tmp_path: Path) -> None:
    sandbox = _ready(tmp_path)
    run = _start(sandbox, Scenario(served_sha_after_bootstrap=WRONG_SHA))
    assert run.returncode == 1
    assert not (sandbox.alan_dir / "deploy" / "restarted_sha").exists()


def test_a_load_failure_exits_non_zero_and_records_no_start_mark(tmp_path: Path) -> None:
    sandbox = _ready(tmp_path)
    run = _start(sandbox, Scenario(bootstrap_fails=True))
    assert run.returncode != 0
    assert "start" not in _marks(sandbox)


@pytest.mark.parametrize(
    ("with_state", "with_profile"),
    [(False, False), (True, False), (False, True)],
    ids=["neither", "state-only", "profile-only"],
)
def test_start_refuses_before_any_launchctl_write_without_the_state_file_and_the_profile(
    tmp_path: Path, with_state: bool, with_profile: bool
) -> None:
    sandbox = make_sandbox(tmp_path)
    if with_state:
        seed_state(sandbox, "preflight")
    if with_profile:
        default_profile_writer.write_profile(sandbox.alan_dir / "engines", "example-owner", "x")
    run = _start(sandbox)
    assert run.returncode == 1
    assert "refused at start: profile-before-boot:" in run.stderr
    assert _launchctl_writes(run) == []
    assert not sandbox.plist.exists()
    assert not sandbox.secrets_file.exists()


def test_start_needs_the_virtualenv_the_build_creates(tmp_path: Path) -> None:
    sandbox = make_sandbox(tmp_path)
    seed_state(sandbox, "preflight")
    engines = sandbox.alan_dir / "engines"
    default_profile_writer.write_profile(engines, "example-owner", "example-repo")
    run = _start(sandbox)
    assert run.returncode == 1
    assert "refused at start: venv:" in run.stderr
    assert _launchctl_writes(run) == []


def test_a_run_that_fails_after_the_load_resumes_without_the_existing_install_refusal(
    tmp_path: Path,
) -> None:
    sandbox = _ready(tmp_path)
    failed = _start(sandbox, Scenario(health_never=True))
    assert failed.returncode == 1
    assert sandbox.queue_db.exists()
    assert sandbox.state_file.exists()
    resumed = _start(sandbox)
    assert resumed.returncode == 0, resumed.output
    assert "existing-install" not in resumed.output
    assert _marks(sandbox)[-1] == "start"


def test_the_template_clone_refusal_does_not_run_under_only_start(tmp_path: Path) -> None:
    sandbox = make_sandbox(tmp_path, origin="https://github.com/template-owner/example-repo.git")
    assert run_setup(sandbox, "--only", "build").returncode == 0
    default_profile_writer.write_profile(
        sandbox.alan_dir / "engines", "template-owner", "example-repo"
    )
    run = _start(sandbox)
    assert run.returncode == 0, run.output
    assert run.called("gh") == []
    assert "template-clone" not in run.output
