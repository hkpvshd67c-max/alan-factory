from __future__ import annotations

from pathlib import Path

import pytest

from tests._setup_support import (
    Sandbox,
    Scenario,
    SetupRun,
    git,
    make_sandbox,
    mutating_calls,
    preload_agent,
    run_setup,
    script_constant,
    seed_bound_service,
    seed_existing_install,
    seed_state,
)

BIND_STEPS = ["credential", "harden", "protect", "confirm"]
TEMPLATE = "template-org/template-repo"
COPY = "example-owner/template-repo"
ORG_COPY = "example-org/template-repo"


def _bootstrap(sandbox: Sandbox, *args: str, scenario: Scenario | None = None) -> SetupRun:
    return run_setup(sandbox, *args, scenario=scenario, entry="setup.sh")


def _copy_dest(sandbox: Sandbox) -> Path:
    return sandbox.home / "template-repo"


def _gh_writes(run: SetupRun) -> list[tuple[str, ...]]:
    return [call.args for call in mutating_calls(run) if call.tool == "gh"]


def test_the_bootstrap_creates_the_copy_and_hands_over_to_the_new_clone(tmp_path: Path) -> None:
    sandbox = make_sandbox(tmp_path)
    run = _bootstrap(sandbox, "--template", TEMPLATE)
    assert run.returncode == 0, run.output
    assert run.headers() == ["preflight", "install", "copy"]
    assert [call.args for call in run.called("gh", "repo")] == [
        ("repo", "view", COPY),
        ("repo", "create", COPY, "--template", TEMPLATE, "--private"),
        ("repo", "clone", COPY, str(_copy_dest(sandbox))),
    ]
    assert sandbox.handover_args() == ["--from", "build"]
    entry = (sandbox.stub_root / "handover.entry").read_text(encoding="utf-8").strip()
    assert entry == f"{_copy_dest(sandbox)}/scripts/setup_macos.sh"
    assert "handing over to the setup script inside your new clone" in run.stdout
    assert sandbox.state_file.read_text(encoding="utf-8").splitlines() == [
        "preflight",
        "install",
        "copy",
    ]


@pytest.mark.parametrize("entry", ["setup.sh", "scripts/setup_macos.sh"])
def test_both_entry_points_accept_the_claude_step_for_only(tmp_path: Path, entry: str) -> None:
    sandbox = make_sandbox(tmp_path, absent=frozenset(), finish_steps=True, ci_steps=True)
    seed_bound_service(sandbox)
    run = run_setup(sandbox, "--only", "claude", entry=entry)
    assert run.returncode == 0, run.output
    assert run.headers() == ["claude"]


def test_the_bootstrap_usage_error_lists_the_ci_steps_after_the_verifier(tmp_path: Path) -> None:
    sandbox = make_sandbox(tmp_path, finish_steps=True, sonar_step=True, ci_steps=True)
    run = run_setup(sandbox, "--only", "nonesuch", entry="setup.sh")
    assert run.returncode == 2
    assert (
        "(steps: preflight install copy build profile start credential harden protect confirm "
        "sonar verifier trigger claude updater latch report)"
    ) in run.stderr
    assert run.calls == ()


def test_the_bootstrap_runs_preflight_and_install_before_it_creates_anything_on_github(
    tmp_path: Path,
) -> None:
    sandbox = make_sandbox(tmp_path)
    run = _bootstrap(sandbox, "--template", TEMPLATE)
    tools = [(call.tool, call.args[0]) for call in mutating_calls(run)]
    assert tools.index(("brew", "install")) < tools.index(("gh", "repo"))
    assert run.calls.index(run.called("sysctl")[0]) < run.calls.index(run.called("gh", "repo")[0])


def test_the_owner_flag_owns_the_copy_and_is_passed_on_to_the_handover(tmp_path: Path) -> None:
    sandbox = make_sandbox(tmp_path)
    run = _bootstrap(sandbox, "--template", TEMPLATE, "--owner", "example-org")
    assert run.returncode == 0, run.output
    assert _gh_writes(run)[0] == (
        "repo",
        "create",
        ORG_COPY,
        "--template",
        TEMPLATE,
        "--private",
    )
    assert run.called("gh", "api") == []
    assert sandbox.handover_args() == ["--from", "build", "--owner", "example-org"]


def test_the_gh_token_flag_is_forwarded_to_the_handover(tmp_path: Path) -> None:
    sandbox = make_sandbox(tmp_path)
    run = _bootstrap(sandbox, "--template", TEMPLATE, "--use-gh-token")
    assert run.returncode == 0, run.output
    assert sandbox.handover_args() == ["--from", "build", "--use-gh-token"]


def test_the_owner_and_the_gh_token_flags_are_both_forwarded_to_the_handover(
    tmp_path: Path,
) -> None:
    sandbox = make_sandbox(tmp_path)
    run = _bootstrap(
        sandbox, "--template", TEMPLATE, "--owner", "example-org", "--use-gh-token"
    )
    assert run.returncode == 0, run.output
    assert sandbox.handover_args() == [
        "--from",
        "build",
        "--owner",
        "example-org",
        "--use-gh-token",
    ]


def test_the_sonar_flag_is_forwarded_to_the_handover(tmp_path: Path) -> None:
    sandbox = make_sandbox(tmp_path)
    run = _bootstrap(sandbox, "--template", TEMPLATE, "--accept-sonar-user-token")
    assert run.returncode == 0, run.output
    assert sandbox.handover_args() == ["--from", "build", "--accept-sonar-user-token"]


def test_the_no_sonar_flag_is_forwarded_to_the_handover(tmp_path: Path) -> None:
    sandbox = make_sandbox(tmp_path)
    run = _bootstrap(sandbox, "--template", TEMPLATE, "--no-sonar")
    assert run.returncode == 0, run.output
    assert sandbox.handover_args() == ["--from", "build", "--no-sonar"]


def test_every_forwarded_flag_is_passed_to_the_handover_together(tmp_path: Path) -> None:
    sandbox = make_sandbox(tmp_path)
    run = _bootstrap(
        sandbox,
        "--template",
        TEMPLATE,
        "--owner",
        "example-org",
        "--use-gh-token",
        "--accept-sonar-user-token",
        "--no-sonar",
    )
    assert run.returncode == 0, run.output
    assert sandbox.handover_args() == [
        "--from",
        "build",
        "--owner",
        "example-org",
        "--use-gh-token",
        "--accept-sonar-user-token",
        "--no-sonar",
    ]


def test_a_handover_without_the_sonar_flags_does_not_invent_them(tmp_path: Path) -> None:
    sandbox = make_sandbox(tmp_path)
    run = _bootstrap(sandbox, "--template", TEMPLATE, "--use-gh-token")
    assert run.returncode == 0, run.output
    assert "--accept-sonar-user-token" not in (sandbox.handover_args() or [])
    assert "--no-sonar" not in (sandbox.handover_args() or [])


def test_the_template_named_in_the_setup_template_file_is_used_without_a_flag(
    tmp_path: Path,
) -> None:
    sandbox = make_sandbox(tmp_path)
    (sandbox.checkout / "setup.template").write_text(f"  {TEMPLATE}\n", encoding="utf-8")
    run = _bootstrap(sandbox)
    assert run.returncode == 0, run.output
    assert run.called("gh", "repo", "create")[0].args[3:] == ("--template", TEMPLATE, "--private")


def test_the_template_flag_overrides_the_setup_template_file(tmp_path: Path) -> None:
    sandbox = make_sandbox(tmp_path)
    (sandbox.checkout / "setup.template").write_text("file-org/file-repo\n", encoding="utf-8")
    run = _bootstrap(sandbox, "--template", TEMPLATE)
    assert run.called("gh", "repo", "create")[0].args[3] == "--template"
    assert run.called("gh", "repo", "create")[0].args[4] == TEMPLATE


def test_without_any_template_setup_refuses_before_any_mutating_call(tmp_path: Path) -> None:
    sandbox = make_sandbox(tmp_path)
    run = _bootstrap(sandbox)
    assert run.returncode == 1
    assert "refused at copy: template:" in run.stderr
    assert "--template <owner/repo>" in run.stderr
    assert mutating_calls(run) == []
    assert run.called("gh") == []
    assert run.headers() == []


@pytest.mark.parametrize(
    "template", ["not-a-slug", "owner/..", "owner/.hidden", "owner-/repo", "a/b/c", "owner/re po"]
)
def test_a_malformed_template_is_refused_before_any_mutating_call(
    tmp_path: Path, template: str
) -> None:
    sandbox = make_sandbox(tmp_path)
    run = _bootstrap(sandbox, "--template", template)
    assert run.returncode == 1
    assert "the template must be written <owner>/<repo>" in run.stderr
    assert mutating_calls(run) == []


def test_an_empty_setup_template_file_is_refused(tmp_path: Path) -> None:
    sandbox = make_sandbox(tmp_path)
    (sandbox.checkout / "setup.template").write_text("\n", encoding="utf-8")
    run = _bootstrap(sandbox)
    assert run.returncode == 1
    assert mutating_calls(run) == []


def test_a_clone_without_a_head_is_retried_until_it_has_one(tmp_path: Path) -> None:
    sandbox = make_sandbox(tmp_path)
    run = _bootstrap(
        sandbox, "--template", TEMPLATE, scenario=Scenario(clone_empty_attempts=2)
    )
    assert run.returncode == 0, run.output
    assert len(run.called("gh", "repo", "clone")) == 3
    assert len(run.called("sleep")) == 2
    assert "has no commits yet (attempt 2 of" in run.stdout
    assert sandbox.handover_args() == ["--from", "build"]
    assert (_copy_dest(sandbox) / "scripts" / "setup_macos.sh").exists()


def test_a_clone_that_stays_empty_exits_non_zero_after_the_retry_limit(tmp_path: Path) -> None:
    sandbox = make_sandbox(tmp_path)
    run = _bootstrap(
        sandbox, "--template", TEMPLATE, scenario=Scenario(clone_empty_attempts=999)
    )
    assert run.returncode == 1
    assert "refused at copy: clone:" in run.stderr
    assert len(run.called("gh", "repo", "clone")) == script_constant("CLONE_ATTEMPTS")
    assert sandbox.handover_args() is None
    assert not _copy_dest(sandbox).exists()
    assert "copy" not in sandbox.state_file.read_text(encoding="utf-8").splitlines()


def test_a_failing_gh_clone_is_reported_as_a_clone_failure_not_as_an_empty_repository(
    tmp_path: Path,
) -> None:
    sandbox = make_sandbox(tmp_path)
    run = _bootstrap(
        sandbox, "--template", TEMPLATE, scenario=Scenario(clone_failing_attempts=999)
    )
    assert run.returncode == 1
    assert "refused at copy: clone:" in run.stderr
    assert f"gh repo clone {COPY} failed on every one of" in run.stderr
    assert "no HEAD" not in run.stderr
    assert "no commits yet" not in run.stdout
    assert f"gh repo clone {COPY} failed (attempt 2 of" in run.stdout
    assert len(run.called("gh", "repo", "clone")) == script_constant("CLONE_ATTEMPTS")
    assert sandbox.handover_args() is None


def test_a_partial_directory_left_by_a_failed_clone_is_removed_and_the_retry_succeeds(
    tmp_path: Path,
) -> None:
    sandbox = make_sandbox(tmp_path)
    run = _bootstrap(
        sandbox, "--template", TEMPLATE, scenario=Scenario(clone_failing_attempts=2)
    )
    assert run.returncode == 0, run.output
    assert len(run.called("gh", "repo", "clone")) == 3
    assert (_copy_dest(sandbox) / ".git").is_dir()
    assert sandbox.handover_args() == ["--from", "build"]


def test_a_failed_clone_leaves_no_partial_directory_for_the_next_run_to_trip_over(
    tmp_path: Path,
) -> None:
    sandbox = make_sandbox(tmp_path)
    failed = _bootstrap(
        sandbox, "--template", TEMPLATE, scenario=Scenario(clone_failing_attempts=999)
    )
    assert failed.returncode == 1
    assert not _copy_dest(sandbox).exists()
    again = _bootstrap(sandbox, "--template", TEMPLATE, scenario=Scenario(repos_existing=(COPY,)))
    assert again.returncode == 0, again.output
    assert "copy-destination" not in again.stderr


def test_an_existing_github_repository_is_not_created_again(tmp_path: Path) -> None:
    sandbox = make_sandbox(tmp_path)
    run = _bootstrap(
        sandbox, "--template", TEMPLATE, scenario=Scenario(repos_existing=(COPY,))
    )
    assert run.returncode == 0, run.output
    assert run.called("gh", "repo", "create") == []
    assert f"[already] GitHub repository {COPY} exists" in run.stdout


def test_a_second_bootstrap_reuses_the_clone_and_makes_no_mutating_call(tmp_path: Path) -> None:
    sandbox = make_sandbox(tmp_path)
    assert _bootstrap(sandbox, "--template", TEMPLATE).returncode == 0
    second = _bootstrap(sandbox, "--template", TEMPLATE, scenario=Scenario(repos_existing=(COPY,)))
    assert second.returncode == 0, second.output
    assert mutating_calls(second) == []
    assert f"[already] your copy {COPY} is cloned" in second.stdout
    assert sandbox.handover_args() == ["--from", "build"]


def test_a_second_bootstrap_is_not_refused_when_the_service_runs_from_the_copy(
    tmp_path: Path,
) -> None:
    sandbox = make_sandbox(tmp_path)
    assert _bootstrap(sandbox, "--template", TEMPLATE).returncode == 0
    preload_agent(sandbox, checkout=_copy_dest(sandbox))
    seed_existing_install(sandbox)
    second = _bootstrap(
        sandbox,
        "--template",
        TEMPLATE,
        scenario=Scenario(repos_existing=(COPY,), port_pids=(4242,)),
    )
    assert second.returncode == 0, second.output
    assert "existing-install" not in second.stderr
    assert "held by this checkout's ai.alan service" in second.stdout


def test_a_destination_that_is_not_a_clone_of_the_copy_is_refused_and_left_alone(
    tmp_path: Path,
) -> None:
    sandbox = make_sandbox(tmp_path)
    _copy_dest(sandbox).mkdir()
    (_copy_dest(sandbox) / "keep.txt").write_text("mine\n", encoding="utf-8")
    run = _bootstrap(sandbox, "--template", TEMPLATE)
    assert run.returncode == 1
    assert "refused at copy: copy-destination:" in run.stderr
    assert (_copy_dest(sandbox) / "keep.txt").read_text(encoding="utf-8") == "mine\n"
    assert run.called("gh", "repo", "create") == []
    assert run.called("gh", "repo", "clone") == []


def test_only_copy_creates_the_copy_without_handing_over(tmp_path: Path) -> None:
    sandbox = make_sandbox(tmp_path)
    run = _bootstrap(sandbox, "--only", "copy", "--template", TEMPLATE)
    assert run.returncode == 0, run.output
    assert run.headers() == ["copy"]
    assert sandbox.handover_args() is None
    assert "did not start alan-factory" in run.stdout
    assert _copy_dest(sandbox).is_dir()


def test_from_build_skips_the_copy_and_needs_no_template(tmp_path: Path) -> None:
    sandbox = make_sandbox(tmp_path)
    run = _bootstrap(sandbox, "--from", "build")
    assert run.returncode == 0, run.output
    assert run.headers() == ["build", "profile", "start", *BIND_STEPS]
    assert run.called("gh", "repo") == []
    assert sandbox.handover_args() is None


def test_an_unknown_signed_in_user_without_an_owner_flag_refuses_the_copy(tmp_path: Path) -> None:
    sandbox = make_sandbox(tmp_path)
    run = _bootstrap(
        sandbox, "--template", TEMPLATE, scenario=Scenario(gh_api_fails=True)
    )
    assert run.returncode == 1
    assert "refused at copy: copy-owner:" in run.stderr
    assert _gh_writes(run) == []


@pytest.mark.parametrize("template", ["example-owner/template-repo", "Example-Owner/Template-Repo"])
def test_a_copy_that_would_be_the_template_itself_is_refused_before_any_write(
    tmp_path: Path, template: str
) -> None:
    sandbox = make_sandbox(tmp_path)
    run = _bootstrap(sandbox, "--template", template)
    assert run.returncode == 1
    assert "refused at copy: copy-is-template:" in run.stderr
    assert "--owner <org>" in run.stderr
    assert _gh_writes(run) == []
    assert not _copy_dest(sandbox).exists()


def test_a_clone_without_a_setup_script_cannot_be_handed_over_to(tmp_path: Path) -> None:
    sandbox = make_sandbox(tmp_path)
    run = _bootstrap(
        sandbox, "--template", TEMPLATE, scenario=Scenario(clone_omits_setup_script=True)
    )
    assert run.returncode == 1
    assert "refused at copy: handover:" in run.stderr
    assert sandbox.handover_args() is None


def test_uninstall_through_the_bootstrap_entry_needs_no_template(tmp_path: Path) -> None:
    sandbox = make_sandbox(tmp_path)
    seed_state(sandbox, "preflight")
    run = _bootstrap(sandbox, "--uninstall")
    assert run.returncode == 0, run.output
    assert run.called("gh") == []


def test_a_leftover_clone_without_a_head_is_replaced_by_a_fresh_one(tmp_path: Path) -> None:
    sandbox = make_sandbox(tmp_path)
    stuck = Scenario(clone_empty_attempts=999)
    assert _bootstrap(sandbox, "--template", TEMPLATE, scenario=stuck).returncode == 1
    leftover = _copy_dest(sandbox)
    leftover.mkdir()
    git(leftover, "init", "-q", home=sandbox.home)
    git(
        leftover,
        "remote",
        "add",
        "origin",
        f"https://github.com/{COPY}.git",
        home=sandbox.home,
    )
    run = _bootstrap(sandbox, "--template", TEMPLATE, scenario=Scenario(repos_existing=(COPY,)))
    assert run.returncode == 0, run.output
    assert len(run.called("gh", "repo", "clone")) == 1
    assert sandbox.handover_args() == ["--from", "build"]


def test_the_handover_tells_the_new_clone_not_to_print_the_checklist_again(
    tmp_path: Path,
) -> None:
    sandbox = make_sandbox(tmp_path)
    run = _bootstrap(sandbox, "--template", TEMPLATE)
    assert run.returncode == 0, run.output
    assert run.stdout.count("what will run, and what only you can do") == 1
    handed = (sandbox.stub_root / "handover.env").read_text(encoding="utf-8").strip()
    assert handed == "1"


def test_a_handed_over_run_skips_the_checklist_and_still_runs_its_steps(tmp_path: Path) -> None:
    sandbox = make_sandbox(tmp_path)
    run = run_setup(sandbox, "--only", "preflight", extra_env={"SETUP_HANDED_OVER": "1"})
    assert run.returncode == 0, run.output
    assert "what will run, and what only you can do" not in run.stdout
    assert run.headers() == ["preflight"]
