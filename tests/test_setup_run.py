from __future__ import annotations

import re
from pathlib import Path

import pytest

from alan import default_profile, default_profile_writer
from alan.default_profile import Source
from alan.engine_config_validation import Posture
from tests._setup_stubs import CLAUDE_INSTALLER_URL, HOMEBREW_INSTALLER_URL, TOOLS
from tests._setup_support import (
    EXAMPLE_ORIGIN,
    LEAK_PATTERNS,
    OWNER,
    PORTABLE_TOOLS,
    REPO,
    WAIT_BUDGET,
    Sandbox,
    Scenario,
    SetupRun,
    all_output,
    leaks,
    make_sandbox,
    mutating_calls,
    prepare_for_start,
    run_env,
    run_setup,
    script_constant,
    shared_shims,
    snapshot_files,
)

BIND_STEPS = ["credential", "harden", "protect", "confirm"]
STEPS = ["preflight", "install", "build", "profile", "start", *BIND_STEPS]
FORMULAE = ["git", "gh", "uv", "node@22", "gitleaks", "pre-commit", "tmux"]
COMPLETE_CLAIM = re.compile(r"setup (is |was |has )?complete", re.IGNORECASE)


FreshRun = tuple[Sandbox, SetupRun]
Snapshot = dict[Path, tuple[bytes, int]]
Rerun = tuple[SetupRun, SetupRun, Snapshot, Snapshot]


def _fresh(tmp_path: Path, scenario: Scenario | None = None) -> FreshRun:
    sandbox = make_sandbox(tmp_path)
    return sandbox, run_setup(sandbox, scenario=scenario)


def _watched(sandbox: Sandbox) -> Snapshot:
    return snapshot_files(
        sandbox.profile_file, sandbox.secrets_file, sandbox.state_file, sandbox.plist
    )


@pytest.fixture(scope="module")
def fresh(tmp_path_factory: pytest.TempPathFactory) -> FreshRun:
    return _fresh(tmp_path_factory.mktemp("fresh"))


@pytest.fixture(scope="module")
def rerun(tmp_path_factory: pytest.TempPathFactory) -> Rerun:
    sandbox, first = _fresh(tmp_path_factory.mktemp("rerun"))
    assert first.returncode == 0, first.output
    before = _watched(sandbox)
    second = run_setup(sandbox, scenario=Scenario(port_pids=(4242,)))
    return first, second, before, _watched(sandbox)


def test_a_fresh_run_from_inside_a_clone_runs_every_step_but_the_copy_in_order(
    fresh: FreshRun,
) -> None:
    _, run = fresh
    assert run.returncode == 0, run.output
    assert run.headers() == STEPS
    assert run.called("gh", "repo") == []


def test_a_fresh_run_makes_its_mutating_calls_in_step_order(fresh: FreshRun) -> None:
    _, run = fresh
    tools = [(call.tool, call.args[0]) for call in mutating_calls(run)]
    assert tools == [
        *[("brew", "install")] * len(FORMULAE),
        ("curl", "-fsSL"),
        ("uv", "sync"),
        ("npm", "ci"),
        ("npm", "run"),
        ("pre-commit", "install"),
        ("launchctl", "enable"),
        ("launchctl", "bootstrap"),
        ("launchctl", "kickstart"),
    ]


def test_the_closing_line_says_alan_is_up_and_no_longer_lists_the_repository_binding(
    fresh: FreshRun,
) -> None:
    _, run = fresh
    closing = run.stdout.strip().splitlines()[-2:]
    assert "alan-factory is up" in closing[0]
    assert "not ready to dispatch" in closing[0]
    assert "P1.9b" not in closing[1]
    assert "binding" not in closing[1]
    assert "P1.9c" not in closing[1]
    assert "SonarCloud" in closing[1]
    assert closing[1].endswith("and the final report.")


def test_a_run_that_stops_before_the_report_never_claims_to_be_complete_and_lists_it_as_pending(
    fresh: FreshRun,
) -> None:
    _, run = fresh
    assert COMPLETE_CLAIM.search(run.output) is None
    assert "the final report" in run.stdout.strip().splitlines()[-1]


def test_the_checklist_and_time_estimate_print_before_the_first_step(fresh: FreshRun) -> None:
    _, run = fresh
    first_step = run.stdout.index("== preflight ==")
    assert run.stdout.index("Steps you do by hand") < first_step
    assert run.stdout.index("Time: plan on") < first_step
    assert "Sign in to Claude yourself" in run.stdout


def test_the_checklist_names_the_token_creation_step_before_the_first_step(
    fresh: FreshRun,
) -> None:
    _, run = fresh
    checklist = run.stdout[: run.stdout.index("== preflight ==")]
    assert "Create a fine-grained GitHub token for your repository" in checklist
    assert "setup prints the exact permissions when it asks for the token" in checklist


def test_the_state_file_records_each_completed_step_in_order(fresh: FreshRun) -> None:
    sandbox, _ = fresh
    assert sandbox.state_file.read_text(encoding="utf-8").splitlines() == STEPS


def test_the_profile_is_written_and_the_state_recorded_before_the_first_service_load(
    fresh: FreshRun,
) -> None:
    sandbox, _ = fresh
    assert sandbox.bootstrap_snapshots() == [
        {"state_exists": True, "profile_exists": True, "log_dir_exists": True}
    ]


def test_the_installed_profile_resolves_through_the_service_validator(
    fresh: FreshRun, tmp_path: Path
) -> None:
    sandbox, _ = fresh
    resolved = default_profile.resolve(sandbox.alan_dir / "engines", tmp_path / "absent.yaml")
    assert resolved.source is Source.profile
    assert resolved.identity_fields() == {"owner": OWNER, "repo": REPO}
    assert resolved.profile.posture is Posture.propose
    assert resolved.profile.authoritative_check_apps == {"gate": 15368}


def test_a_second_run_makes_no_mutating_tool_call(rerun: Rerun) -> None:
    _, second, _, _ = rerun
    assert second.returncode == 0, second.output
    assert mutating_calls(second) == []
    assert second.called("sleep") == []


def test_a_second_run_reports_already_for_mutating_steps_and_ok_for_read_only_checks(
    rerun: Rerun,
) -> None:
    _, second, _, _ = rerun
    assert "[done]" not in second.stdout
    for already in (
        "[already] Homebrew",
        "[already] brew formula node@22",
        "[already] brew formula tmux",
        "[already] Claude Code CLI",
        "[already] GitHub sign-in",
        "[already] virtualenv, web build and pre-commit hook",
        "[already] default-engine profile",
        "[already] secrets file with mode 600",
        "[already] log directory",
        "[already] ai.alan is loaded from this checkout",
        "[already] transport credential for",
        "[already] secret scanning",
        "[already] push protection",
        "[already] collaborator-only interaction limits",
        "[already] pull-request creation limited to collaborators",
        "[already] branch protection on",
    ):
        assert already in second.stdout
    for ok in (
        "[ok] architecture",
        "[ok] port 8585 is held by this checkout's",
        "[ok] origin",
        "[ok] control token",
        "[ok] preflight author_token_valid",
        "[ok] preflight required_checks",
    ):
        assert ok in second.stdout


def test_a_second_run_leaves_the_profile_secrets_state_and_plist_untouched(
    rerun: Rerun,
) -> None:
    _, _, before, after = rerun
    assert len(before) == 4
    assert after == before


def test_a_second_run_repeats_the_pending_closing_line(rerun: Rerun) -> None:
    first, second, _, _ = rerun
    assert first.stdout.strip().splitlines()[-2:] == second.stdout.strip().splitlines()[-2:]


def test_install_never_force_links_and_only_asks_claude_for_its_version(tmp_path: Path) -> None:
    sandbox = make_sandbox(tmp_path, absent=frozenset())
    run = run_setup(sandbox)
    assert run.returncode == 0, run.output
    assert run.called("brew", "link") == []
    assert [call.args for call in run.called("claude")] == [("--version",)]
    assert run.called("security") == []


def test_install_installs_the_seven_formulae_in_order(fresh: FreshRun) -> None:
    _, run = fresh
    assert [call.args[1] for call in run.called("brew", "install")] == FORMULAE


def test_install_skips_formulae_that_are_already_installed(tmp_path: Path) -> None:
    _, run = _fresh(tmp_path, Scenario(brew_installed=("git", "uv")))
    assert [call.args[1] for call in run.called("brew", "install")] == [
        "gh",
        "node@22",
        "gitleaks",
        "pre-commit",
        "tmux",
    ]
    assert "[already] brew formula git" in run.stdout


def test_install_adds_tmux_alone_when_every_other_formula_is_present(tmp_path: Path) -> None:
    others = tuple(formula for formula in FORMULAE if formula != "tmux")
    _, run = _fresh(tmp_path, Scenario(brew_installed=others))
    assert [call.args for call in run.called("brew", "install")] == [("install", "tmux")]
    assert "[done] brew formula tmux" in run.stdout
    assert "[already] brew formula gitleaks" in run.stdout


def test_install_leaves_a_preinstalled_tmux_alone(tmp_path: Path) -> None:
    _, run = _fresh(tmp_path, Scenario(brew_installed=("tmux",)))
    assert ("install", "tmux") not in [call.args for call in run.called("brew", "install")]
    assert "[already] brew formula tmux" in run.stdout
    assert "[done] brew formula tmux" not in run.stdout


def test_install_installs_nothing_when_every_formula_is_present(tmp_path: Path) -> None:
    _, run = _fresh(tmp_path, Scenario(brew_installed=tuple(FORMULAE)))
    assert run.called("brew", "install") == []


def test_the_node_22_keg_comes_first_on_path_from_the_homebrew_prefix(fresh: FreshRun) -> None:
    sandbox, run = fresh
    path = (sandbox.stub_root / "node.path").read_text(encoding="utf-8").split(":")
    assert path[0] == f"{sandbox.base}/homebrew/opt/node@22/bin"
    assert run.returncode == 0


def test_node_reporting_another_major_release_is_refused(tmp_path: Path) -> None:
    _, run = _fresh(tmp_path, Scenario(node_version="v20.11.0"))
    assert run.returncode == 1
    assert "refused at install: node:" in run.stderr


def test_a_failed_brew_install_stops_the_run_before_the_build(tmp_path: Path) -> None:
    sandbox, run = _fresh(tmp_path, Scenario(brew_install_fails=True))
    assert run.returncode != 0
    assert run.headers() == ["preflight", "install"]
    assert sandbox.state_file.read_text(encoding="utf-8").splitlines() == ["preflight"]


def test_homebrew_is_installed_when_missing_and_found_through_the_injected_prefix(
    tmp_path: Path,
) -> None:
    sandbox = make_sandbox(tmp_path, absent=frozenset({"claude", "brew"}))
    run = run_setup(sandbox)
    assert run.returncode == 0, run.output
    installers = [call for call in run.called("curl") if HOMEBREW_INSTALLER_URL in call.args]
    assert len(installers) == 1
    assert "[done] Homebrew" in run.stdout


def test_homebrew_installed_but_not_resolvable_exits_3_and_asks_for_a_new_terminal(
    tmp_path: Path,
) -> None:
    sandbox = make_sandbox(tmp_path, absent=frozenset({"claude", "brew"}))
    run = run_setup(sandbox, unset=("HOMEBREW_PREFIX",))
    assert run.returncode == 3
    assert "open a new terminal" in run.stderr
    assert run.called("brew") == []


def _keg_only_npm(sandbox: Sandbox) -> Path:
    keg = sandbox.base / "homebrew" / "opt" / "node@22" / "bin"
    keg.mkdir(parents=True)
    npm = keg / "npm"
    npm.symlink_to(shared_shims() / "npm")
    return npm


def test_a_resumed_build_finds_npm_through_the_node_22_keg_from_the_homebrew_prefix(
    tmp_path: Path,
) -> None:
    sandbox = make_sandbox(tmp_path, absent=frozenset({"claude", "npm"}))
    _keg_only_npm(sandbox)
    run = run_setup(sandbox, "--only", "build")
    assert run.returncode == 0, run.output
    assert [call.args for call in run.called("npm")] == [("ci",), ("run", "build")]
    assert run.called("brew", "install") == []


def test_a_resumed_build_without_the_keg_fails_on_the_missing_npm(tmp_path: Path) -> None:
    sandbox = make_sandbox(tmp_path, absent=frozenset({"claude", "npm"}))
    run = run_setup(sandbox, "--only", "build")
    assert run.returncode != 0
    assert run.called("npm") == []


def test_a_full_run_does_not_look_the_homebrew_prefix_up_a_second_time(fresh: FreshRun) -> None:
    _, run = fresh
    assert run.returncode == 0
    assert len(run.called("brew", "shellenv")) == 1


def test_the_exit_3_message_names_the_shell_profile_line_and_the_prefix_override(
    tmp_path: Path,
) -> None:
    sandbox = make_sandbox(tmp_path, absent=frozenset({"claude", "brew"}))
    run = run_setup(sandbox, unset=("HOMEBREW_PREFIX",))
    assert run.returncode == 3
    assert "brew shellenv" in run.stderr
    assert "shell profile" in run.stderr
    assert "HOMEBREW_PREFIX" in run.stderr
    assert "/opt/homebrew" not in run.stderr


def test_the_claude_installer_runs_once_when_claude_is_missing(fresh: FreshRun) -> None:
    _, run = fresh
    installers = [call for call in run.called("curl") if CLAUDE_INSTALLER_URL in call.args]
    assert len(installers) == 1
    assert "[done] Claude Code CLI" in run.stdout


def test_a_preinstalled_claude_is_not_installed_again(tmp_path: Path) -> None:
    sandbox = make_sandbox(tmp_path, absent=frozenset())
    run = run_setup(sandbox)
    assert [call for call in run.called("curl") if CLAUDE_INSTALLER_URL in call.args] == []
    assert "[already] Claude Code CLI" in run.stdout


def test_an_installer_that_leaves_no_claude_behind_is_refused(tmp_path: Path) -> None:
    _, run = _fresh(tmp_path, Scenario(claude_installer_installs=False))
    assert run.returncode == 1
    assert "refused at install: claude-cli:" in run.stderr


def test_gh_sign_in_runs_only_when_gh_is_not_authenticated(fresh: FreshRun, tmp_path: Path) -> None:
    _, signed_out = _fresh(tmp_path, Scenario(gh_authed=False))
    assert [call.args for call in signed_out.called("gh", "auth", "login")] == [
        ("auth", "login", "--hostname", "github.com", "--git-protocol", "https", "--web")
    ]
    _, signed_in = fresh
    assert signed_in.called("gh", "auth", "login") == []
    assert "[already] GitHub sign-in" in signed_in.stdout


def test_build_runs_the_locked_sync_the_web_build_and_the_hook_install_once(
    fresh: FreshRun,
) -> None:
    sandbox, run = fresh
    assert [call.args for call in run.called("uv")] == [("sync", "--locked", "--extra", "dev")]
    assert [call.args for call in run.called("npm")] == [("ci",), ("run", "build")]
    assert {call.cwd for call in run.called("npm")} == {str(sandbox.checkout / "alan" / "web")}
    assert [call.args for call in run.called("pre-commit")] == [("install",)]
    assert "playwright" not in run.output.lower()


def test_build_reruns_when_the_web_build_is_missing(tmp_path: Path) -> None:
    sandbox, _ = _fresh(tmp_path)
    (sandbox.checkout / "alan" / "web" / "dist" / "index.html").unlink()
    rerun = run_setup(sandbox, "--only", "build")
    assert rerun.returncode == 0, rerun.output
    assert [call.args for call in rerun.called("npm")] == [("ci",), ("run", "build")]
    assert "[done] virtualenv, web build and pre-commit hook" in rerun.stdout


def test_a_failed_build_stops_before_the_profile_and_records_no_build_mark(
    tmp_path: Path,
) -> None:
    sandbox, run = _fresh(tmp_path, Scenario(uv_fails=True))
    assert run.returncode != 0
    assert run.headers() == ["preflight", "install", "build"]
    assert "build" not in sandbox.state_file.read_text(encoding="utf-8").splitlines()
    assert not sandbox.profile_file.exists()


def _built(tmp_path: Path, origin: str | None = EXAMPLE_ORIGIN) -> Sandbox:
    sandbox = make_sandbox(tmp_path, origin=origin)
    built = run_setup(sandbox, "--only", "build")
    assert built.returncode == 0, built.output
    return sandbox


def test_the_template_clone_refusal_names_the_owner_and_writes_no_profile(
    tmp_path: Path,
) -> None:
    sandbox = _built(tmp_path, "https://github.com/template-owner/example-repo.git")
    run = run_setup(sandbox, "--only", "profile")
    assert run.returncode == 1
    assert "refused at profile: template-clone:" in run.stderr
    assert "template-owner" in run.stderr
    assert not sandbox.profile_file.exists()


def test_the_owner_flag_accepts_an_organization_origin(tmp_path: Path) -> None:
    sandbox = _built(tmp_path, "https://github.com/example-org/example-repo.git")
    run = run_setup(sandbox, "--only", "profile", "--owner", "example-org")
    assert run.returncode == 0, run.output
    assert "belongs to --owner" in run.stdout
    assert default_profile.resolve(
        sandbox.alan_dir / "engines", tmp_path / "absent.yaml"
    ).identity_fields() == {"owner": "example-org", "repo": "example-repo"}


def test_an_owner_flag_naming_another_organization_does_not_rescue_a_template_clone(
    tmp_path: Path,
) -> None:
    sandbox = _built(tmp_path, "https://github.com/template-owner/example-repo.git")
    run = run_setup(sandbox, "--only", "profile", "--owner", "someone-else")
    assert run.returncode == 1
    assert not sandbox.profile_file.exists()


def test_the_owner_comparison_ignores_case(tmp_path: Path) -> None:
    sandbox = _built(tmp_path, "https://github.com/Example-Owner/Example-Repo.git")
    run = run_setup(sandbox, "--only", "profile")
    assert run.returncode == 0, run.output
    assert default_profile.resolve(
        sandbox.alan_dir / "engines", tmp_path / "absent.yaml"
    ).identity_fields() == {"owner": "Example-Owner", "repo": "Example-Repo"}


@pytest.mark.parametrize(
    "origin", ["https://gitlab.example/x/y.git", "not a url", "https://GitHub.com/a/b.git"]
)
def test_an_origin_that_is_not_a_github_repository_is_refused_without_a_profile(
    tmp_path: Path, origin: str
) -> None:
    sandbox = _built(tmp_path, origin)
    run = run_setup(sandbox, "--only", "profile")
    assert run.returncode == 1
    assert "template-clone" in run.stderr
    assert not sandbox.profile_file.exists()


def test_a_missing_origin_is_refused_without_a_profile(tmp_path: Path) -> None:
    sandbox = _built(tmp_path, origin=None)
    run = run_setup(sandbox, "--only", "profile")
    assert run.returncode == 1
    assert not sandbox.profile_file.exists()


def test_a_gh_failure_refuses_the_profile_step_without_writing_a_profile(
    tmp_path: Path,
) -> None:
    sandbox = _built(tmp_path)
    run = run_setup(sandbox, "--only", "profile", scenario=Scenario(gh_api_fails=True))
    assert run.returncode == 1
    assert "signed-in GitHub user" in run.stderr
    assert not sandbox.profile_file.exists()


def test_the_profile_step_needs_the_virtualenv_the_build_creates(tmp_path: Path) -> None:
    sandbox = make_sandbox(tmp_path)
    run = run_setup(sandbox, "--only", "profile")
    assert run.returncode == 1
    assert "refused at profile: venv:" in run.stderr
    assert not sandbox.profile_file.exists()


def test_a_profile_for_a_different_repo_is_refused_and_left_byte_identical(
    tmp_path: Path,
) -> None:
    sandbox = _built(tmp_path)
    default_profile_writer.write_profile(sandbox.alan_dir / "engines", "other-owner", "other-repo")
    before = snapshot_files(sandbox.profile_file)
    run = run_setup(sandbox, "--only", "profile")
    assert run.returncode == 1
    assert "different repository" in run.stderr
    assert "refused at profile: profile-writer:" in run.stderr
    assert snapshot_files(sandbox.profile_file) == before


def test_a_drifted_profile_for_this_repo_is_kept_with_a_warning(tmp_path: Path) -> None:
    sandbox = _built(tmp_path)
    assert run_setup(sandbox, "--only", "profile").returncode == 0
    drifted = sandbox.profile_file.read_text(encoding="utf-8").replace("propose", "autonomous")
    sandbox.profile_file.write_text(drifted, encoding="utf-8")
    before = snapshot_files(sandbox.profile_file)
    run = run_setup(sandbox, "--only", "profile")
    assert run.returncode == 0, run.output
    assert "[already] default-engine profile" in run.stdout
    assert "kept the installed profile" in run.stderr
    assert "posture" in run.stderr
    assert snapshot_files(sandbox.profile_file) == before


def test_the_profile_step_reports_done_then_already(tmp_path: Path) -> None:
    sandbox = _built(tmp_path)
    first = run_setup(sandbox, "--only", "profile")
    second = run_setup(sandbox, "--only", "profile")
    assert "[done] default-engine profile" in first.stdout
    assert "[already] default-engine profile" in second.stdout


def test_no_output_or_recorded_call_carries_a_credential_or_identity_pattern(rerun: Rerun) -> None:
    first, second, _, _ = rerun
    assert leaks(all_output(first) + all_output(second)) == {}


@pytest.mark.parametrize("name", sorted(LEAK_PATTERNS))
def test_each_leak_pattern_detects_a_planted_value(name: str) -> None:
    planted = {
        "token-prefix": "gh" + "p_abc123",
        "personal-email": "someone@" + "gm" + "ail.com",
        "tailnet-host": "box-1.tail1234." + "ts" + ".net",
        "machine-name": "someone-mac" + "book-pro",
    }
    assert name in leaks(f"prefix {planted[name]} suffix")


def test_the_run_never_calls_security_or_signs_in_to_claude(tmp_path: Path) -> None:
    sandbox = make_sandbox(tmp_path, absent=frozenset())
    run = run_setup(sandbox)
    assert run.called("security") == []
    assert run.called("claude", "login") == []
    assert run.called("claude", "setup-token") == []
    assert {call.args for call in run.called("claude")} == {("--version",)}


def test_the_scratch_path_holds_only_stubs_and_the_fixed_portable_tools(tmp_path: Path) -> None:
    sandbox = make_sandbox(tmp_path)
    assert run_env(sandbox)["PATH"] == str(sandbox.bin_dir)
    listed = {path.name for path in sandbox.bin_dir.iterdir()}
    assert listed == {*PORTABLE_TOOLS, *TOOLS} - {"claude"}


def test_nothing_in_a_run_touches_a_homebrew_location(fresh: FreshRun) -> None:
    sandbox, run = fresh
    assert "/opt/homebrew" not in all_output(run)
    assert "/opt/homebrew" not in (sandbox.stub_root / "node.path").read_text(encoding="utf-8")
    assert "/opt/homebrew" not in run_env(sandbox)["PATH"]


def test_an_unstubbed_tool_fails_the_run(tmp_path: Path) -> None:
    sandbox = make_sandbox(tmp_path, absent=frozenset({"claude", "gh"}))
    run = run_setup(sandbox, scenario=Scenario(gh_authed=False))
    assert run.returncode != 0
    assert "gh" in run.stderr
    assert "not found" in run.stderr


def test_every_wait_is_bounded_by_an_attempt_count_and_sleep_is_stubbed(tmp_path: Path) -> None:
    _, run = _fresh(tmp_path, Scenario(health_never=True))
    assert run.returncode == 1
    assert len(run.called("sleep")) == WAIT_BUDGET


@pytest.mark.parametrize("override", [None, "", "abc", "0", "07", "-3"])
def test_a_wait_without_a_positive_override_polls_its_full_default_budget(
    tmp_path: Path, override: str | None
) -> None:
    sandbox = make_sandbox(tmp_path)
    prepare_for_start(sandbox)
    run = run_setup(
        sandbox,
        "--only",
        "start",
        scenario=Scenario(no_token=True),
        extra_env={} if override is None else {"SETUP_TOKEN_ATTEMPTS": override},
        unset=("SETUP_TOKEN_ATTEMPTS",) if override is None else (),
    )
    assert run.returncode == 1
    assert "refused at start: control-token:" in run.stderr
    assert len(run.called("sleep")) == script_constant("TOKEN_ATTEMPTS")


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (("--bogus",), "unknown flag: --bogus"),
        (("--from", "build", "--only", "start"), "cannot be combined"),
        (("--from", "nowhere"), "unknown step for --from: nowhere"),
        (("--only", "copy"), "unknown step for --only: copy"),
        (("--from",), "--from needs a value"),
        (("--owner", "--uninstall"), "--owner needs a value, not another flag"),
        (("--owner", "bad owner"), "--owner must be a GitHub user or organization name"),
        (("--template", "example-owner/example-repo"), "only accepted by ./setup.sh"),
        (("--uninstall", "--from", "build"), "--uninstall cannot be combined"),
    ],
)
def test_bad_arguments_exit_2_before_any_tool_runs(
    tmp_path: Path, args: tuple[str, ...], message: str
) -> None:
    sandbox = make_sandbox(tmp_path)
    run = run_setup(sandbox, *args)
    assert run.returncode == 2
    assert message in run.stderr
    assert run.calls == ()


def test_an_unset_home_is_refused_before_any_tool_runs(tmp_path: Path) -> None:
    sandbox = make_sandbox(tmp_path)
    run = run_setup(sandbox, unset=("HOME",))
    assert run.returncode == 1
    assert "HOME is unset" in run.stderr
    assert run.calls == ()


def test_from_starts_at_the_named_step_and_skips_the_earlier_ones(tmp_path: Path) -> None:
    sandbox = make_sandbox(tmp_path)
    run = run_setup(sandbox, "--from", "build")
    assert run.returncode == 0, run.output
    assert run.headers() == ["build", "profile", "start", *BIND_STEPS]
    assert run.called("sysctl") == []
    assert [call.args for call in run.called("brew")] == [("shellenv",), ("--prefix",)]


def test_only_runs_exactly_the_named_step_and_does_not_claim_alan_is_up(tmp_path: Path) -> None:
    sandbox = make_sandbox(tmp_path)
    run = run_setup(sandbox, "--only", "preflight")
    assert run.returncode == 0, run.output
    assert run.headers() == ["preflight"]
    assert "did not start alan-factory" in run.stdout
    assert "alan-factory is up" not in run.stdout
    assert sandbox.state_file.read_text(encoding="utf-8").splitlines() == ["preflight"]
