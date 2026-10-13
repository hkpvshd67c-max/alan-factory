from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest

from tests._setup_ci_stubs import seed_run
from tests._setup_support import (
    BOT_LOGIN,
    BOUND_MARKS,
    ENGINE_SLUG,
    UPDATE_LABEL,
    Sandbox,
    Scenario,
    SetupRun,
    leaks,
    make_sandbox,
    mutating_calls,
    preload_agent,
    run_setup,
    seed_latch,
    seed_profile,
    seed_running_service,
    seed_secrets_env,
    seed_state,
)

ALL_MARKS = (
    *BOUND_MARKS,
    "credential",
    "harden",
    "protect",
    "confirm",
    "sonar",
    "verifier",
    "trigger",
    "claude",
    "updater",
    "latch",
)
LOGIN_LINE = f"ALAN_VERIFIER_LOGIN={BOT_LOGIN}\n"
GOOD = Scenario(reviewer_stored=True)
OTHER_ENGINE = "other-owner/other-repo"
ITEM_LINES = (
    "[ok] the owned preflight findings pass",
    "[ok] SonarCloud has reported and its latch matches the profile's project",
    "[ok] the verifier bot's credential is stored and the preflight confirms it differs",
    "[ok] the Claude check passed",
    f"[ok] {UPDATE_LABEL} is loaded from this checkout",
    "[ok] the service was restarted after the last change to its configuration",
    "[ok] alan-factory reports no dispatch requirement as not_ready or unknown",
)


def _ready(
    tmp_path: Path, *, block: bool = True, sonar_step: bool = True, required: bool = True
) -> Sandbox:
    sandbox = make_sandbox(
        tmp_path, absent=frozenset(), finish_steps=True, sonar_step=sonar_step, ci_steps=True
    )
    seed_running_service(sandbox, *ALL_MARKS)
    seed_profile(sandbox, organization="example-org" if block else None, quality_required=required)
    seed_latch(sandbox)
    seed_secrets_env(sandbox, "EXAMPLE_KEY=x\n" + LOGIN_LINE)
    preload_agent(sandbox, UPDATE_LABEL)
    return sandbox


def _report(sandbox: Sandbox, scenario: Scenario = GOOD) -> SetupRun:
    return run_setup(sandbox, "--only", "report", scenario=scenario)


def _resume(run: SetupRun) -> str:
    return run.stderr.strip().splitlines()[-1]


def _resume_at(step: str) -> str:
    return f"Resume with: scripts/setup_macos.sh --from {step}"


def _without_mark(sandbox: Sandbox, dropped: str) -> None:
    seed_state(sandbox, *(mark for mark in ALL_MARKS if mark != dropped))


def _without_latch(sandbox: Sandbox) -> None:
    sandbox.latch_file.unlink()


def _without_updater(sandbox: Sandbox) -> None:
    (sandbox.stub_root / "state" / "loaded" / f"{UPDATE_LABEL}.json").unlink()


def _without_login(sandbox: Sandbox) -> None:
    seed_secrets_env(sandbox, "EXAMPLE_KEY=x\n")


def _with_pending_restart(sandbox: Sandbox) -> None:
    sandbox.restart_marker.write_text("", encoding="utf-8")


def _without_claude(sandbox: Sandbox) -> None:
    _without_mark(sandbox, "claude")


def _unchanged(sandbox: Sandbox) -> None:
    assert sandbox.state_file.exists()


FAILING_ITEMS: dict[str, tuple[Callable[[Sandbox], None], Scenario, str, str]] = {
    "owned-findings": (
        _unchanged,
        Scenario(reviewer_stored=True, preflight_status={"repo_clonable": "fail"}),
        "confirm",
        "a preflight finding this setup owns is not pass",
    ),
    "sonarcloud-not-latched": (
        _without_latch,
        GOOD,
        "latch",
        "SonarCloud has not reported for this install",
    ),
    "verifier-not-stored": (
        _unchanged,
        Scenario(reviewer_stored=False),
        "verifier",
        "the verifier bot's credential is not stored, or its login is not recorded",
    ),
    "verifier-login-not-recorded": (
        _without_login,
        GOOD,
        "verifier",
        "the verifier bot's credential is not stored, or its login is not recorded",
    ),
    "verifier-not-distinct": (
        _unchanged,
        Scenario(reviewer_stored=True, preflight_status={"reviewer_distinct_from_author": "fail"}),
        "verifier",
        "the preflight does not confirm that the verifier bot differs from the author (fail)",
    ),
    "verifier-distinctness-unknown": (
        _unchanged,
        Scenario(
            reviewer_stored=True, preflight_status={"reviewer_distinct_from_author": "unknown"}
        ),
        "verifier",
        "the preflight does not confirm that the verifier bot differs from the author (unknown)",
    ),
    "claude-check": (
        _without_claude,
        GOOD,
        "claude",
        "the Claude check has not passed",
    ),
    "updater": (
        _without_updater,
        GOOD,
        "updater",
        f"{UPDATE_LABEL} is not loaded from this checkout",
    ),
    "restart-pending": (
        _with_pending_restart,
        GOOD,
        "verifier",
        "the service has not been restarted since the last change to its configuration",
    ),
}


def test_every_mandatory_item_holds_and_the_report_says_complete_and_exits_zero(
    tmp_path: Path,
) -> None:
    sandbox = _ready(tmp_path)
    run = _report(sandbox)
    assert run.returncode == 0, run.output
    for line in ITEM_LINES:
        assert line in run.stdout
    assert run.stdout.strip().splitlines()[-1].startswith("setup is complete: every mandatory")
    assert "incomplete" not in run.output
    assert "Resume with" not in run.output
    assert "report" in sandbox.state_file.read_text(encoding="utf-8").splitlines()


def test_the_report_makes_no_mutating_call_and_leaks_nothing(tmp_path: Path) -> None:
    run = _report(_ready(tmp_path))
    assert mutating_calls(run) == []
    assert leaks(run.output) == {}
    assert run.called("gh") == []


@pytest.mark.parametrize("item", FAILING_ITEMS)
def test_an_item_that_does_not_hold_names_that_item_exits_one_and_resumes_at_its_step(
    tmp_path: Path, item: str
) -> None:
    mutate, scenario, step, text = FAILING_ITEMS[item]
    sandbox = _ready(tmp_path)
    mutate(sandbox)
    run = _report(sandbox, scenario)
    assert run.returncode == 1, run.output
    assert f"[failed] {text}" in run.stdout
    assert _resume(run) == _resume_at(step)
    assert "setup is complete" not in run.output
    assert step in run.stderr.split("setup: incomplete:")[1].split(" did not complete")[0]
    assert "report" not in sandbox.state_file.read_text(encoding="utf-8").splitlines()


def test_a_profile_without_a_sonarcloud_project_resumes_at_the_sonar_step(tmp_path: Path) -> None:
    sandbox = _ready(tmp_path, block=False)
    run = _report(sandbox)
    assert run.returncode == 1
    assert "[failed] the installed profile names no SonarCloud project" in run.stdout
    assert _resume(run) == _resume_at("sonar")
    assert "setup: incomplete: sonar, latch, report did not complete" in run.stderr
    assert "not connected" not in run.output


def test_a_profile_that_does_not_require_sonarcloud_and_names_no_project_passes_as_optional(
    tmp_path: Path,
) -> None:
    run = _report(_ready(tmp_path, block=False, required=False))
    assert run.returncode == 0, run.output
    assert "[ok] SonarCloud: not connected (optional)" in run.stdout
    assert "SonarCloud has reported" not in run.stdout
    assert run.stdout.strip().splitlines()[-1].startswith("setup is complete")
    assert "incomplete" not in run.output


def test_an_optional_profile_with_a_project_and_no_latch_warns_and_still_completes(
    tmp_path: Path,
) -> None:
    sandbox = _ready(tmp_path, required=False)
    _without_latch(sandbox)
    run = _report(sandbox)
    assert run.returncode == 0, run.output
    assert (
        "[warning] SonarCloud is connected but has not reported for this install" in run.stdout
    )
    assert "not connected" not in run.stdout
    assert run.stdout.strip().splitlines()[-1].startswith("setup is complete")


def test_the_resume_line_names_the_earliest_failed_step_in_step_order(tmp_path: Path) -> None:
    sandbox = _ready(tmp_path)
    _without_latch(sandbox)
    _without_updater(sandbox)
    _without_claude(sandbox)
    run = _report(sandbox, Scenario(reviewer_stored=False))
    assert run.returncode == 1
    assert "setup: incomplete: verifier, claude, updater, latch, report did not complete" in (
        run.stderr
    )
    assert _resume(run) == _resume_at("verifier")


@pytest.mark.parametrize("state", ["not_ready", "unknown"])
def test_a_not_ready_or_unknown_entry_for_any_engine_fails_naming_the_engine_and_requirement(
    tmp_path: Path, state: str
) -> None:
    extra = {
        "engine": OTHER_ENGINE,
        "requirement": "reviewer_credential",
        "state": state,
        "message": "could not be evaluated",
        "fix": "./setup.sh",
    }
    run = _report(_ready(tmp_path), Scenario(reviewer_stored=True, readiness_extra=(extra,)))
    assert run.returncode == 1
    assert (
        f"[failed] dispatch readiness: engine {OTHER_ENGINE} requirement reviewer_credential "
        f"is {state}: could not be evaluated"
    ) in run.stdout
    assert _resume(run) == _resume_at("verifier")
    assert "setup is complete" not in run.output


@pytest.mark.parametrize(
    ("requirement", "step"),
    [("code_quality", "latch"), ("default_engine", "profile"), ("something_new", "confirm")],
)
def test_a_readiness_entry_resumes_at_the_step_that_owns_its_requirement(
    tmp_path: Path, requirement: str, step: str
) -> None:
    extra = {
        "engine": ENGINE_SLUG,
        "requirement": requirement,
        "state": "not_ready",
        "message": "why",
        "fix": "./setup.sh",
    }
    run = _report(_ready(tmp_path), Scenario(reviewer_stored=True, readiness_extra=(extra,)))
    assert run.returncode == 1
    assert f"requirement {requirement} is not_ready: why" in run.stdout
    assert _resume(run) == _resume_at(step)


def test_an_expiry_warning_alone_does_not_fail_the_report(tmp_path: Path) -> None:
    run = _report(_ready(tmp_path), Scenario(reviewer_stored=True, readiness_warning=True))
    assert run.returncode == 0, run.output
    assert (
        f"[info] dispatch readiness: engine {ENGINE_SLUG} requirement reviewer_credential "
        "warns: expires soon"
    ) in run.stdout
    assert "setup is complete" in run.stdout


def test_a_service_started_before_the_profile_gained_its_sonarcloud_block_is_not_ready(
    tmp_path: Path,
) -> None:
    sandbox = _ready(tmp_path)
    path = sandbox.stub_root / "state" / "loaded" / "ai.alan.json"
    agent = json.loads(path.read_text(encoding="utf-8"))
    agent["view"] = {"profile": True, "code_quality": None}
    path.write_text(json.dumps(agent), encoding="utf-8")
    run = _report(sandbox)
    assert run.returncode == 1
    assert f"engine {ENGINE_SLUG} requirement code_quality is not_ready" in run.stdout
    assert _resume(run) == _resume_at("latch")


def test_a_service_whose_snapshot_of_the_latch_differs_from_the_profile_is_not_ready(
    tmp_path: Path,
) -> None:
    sandbox = _ready(tmp_path)
    seed_latch(sandbox, "other-org", "other-org_other")
    run = _report(sandbox)
    assert run.returncode == 1
    assert "the latch is for another project" in run.stdout


def test_a_service_that_does_not_answer_health_refuses_the_report(tmp_path: Path) -> None:
    run = _report(_ready(tmp_path), Scenario(reviewer_stored=True, health_never=True))
    assert run.returncode == 1
    assert "refused at report: health: the service did not answer /api/health" in run.stderr
    assert "setup is complete" not in run.output


@pytest.mark.parametrize("body", ['{"ok": true}', '{"dispatch_readiness": "none"}', "not json"])
def test_a_health_answer_without_a_readiness_list_refuses_the_report(
    tmp_path: Path, body: str
) -> None:
    run = _report(_ready(tmp_path), Scenario(reviewer_stored=True, health_body=body))
    assert run.returncode == 1
    assert "refused at report: health: the health answer could not be read" in run.stderr


def test_a_garbled_preflight_answer_refuses_the_report(tmp_path: Path) -> None:
    run = _report(_ready(tmp_path), Scenario(reviewer_stored=True, preflight_garbled=True))
    assert run.returncode == 1
    assert "refused at report: preflight:" in run.stderr


def test_a_full_run_from_confirm_ends_in_the_report_and_says_complete(tmp_path: Path) -> None:
    sandbox = _ready(tmp_path, sonar_step=False)
    seed_run(sandbox.stub_root, sandbox.ci_run_file)
    run = run_setup(sandbox, "--from", "confirm", scenario=GOOD)
    assert run.returncode == 0, run.output
    assert run.headers() == [
        "confirm",
        "verifier",
        "trigger",
        "claude",
        "updater",
        "latch",
        "report",
    ]
    assert run.stdout.strip().splitlines()[-1].startswith("setup is complete")
    assert "Still pending" not in run.stdout
    assert "not ready to dispatch" not in run.stdout
    assert {(call.tool, call.args[0]) for call in mutating_calls(run)} == {
        ("launchctl", "bootstrap"),
        ("launchctl", "bootout"),
    }


def test_an_earlier_step_that_failed_in_the_run_keeps_the_report_from_saying_complete(
    tmp_path: Path,
) -> None:
    sandbox = _ready(tmp_path, sonar_step=False)
    seed_run(sandbox.stub_root, sandbox.ci_run_file)
    stuck = Scenario(reviewer_stored=True, pr_policy="all", pr_policy_sticks=False)
    run = run_setup(sandbox, "--from", "harden", scenario=stuck)
    assert run.returncode == 1
    assert run.headers()[0] == "harden"
    assert run.headers()[-1] == "report"
    for line in ITEM_LINES:
        assert line in run.stdout
    assert "setup: incomplete: harden, report did not complete" in run.stderr
    assert _resume(run) == _resume_at("harden")
    assert "setup is complete" not in run.output


def test_a_failed_step_and_a_failed_report_item_resume_at_the_earlier_of_the_two(
    tmp_path: Path,
) -> None:
    sandbox = _ready(tmp_path, sonar_step=False)
    seed_run(sandbox.stub_root, sandbox.ci_run_file)
    _without_updater(sandbox)
    scenario = Scenario(reviewer_stored=True, update_bootstrap_fails=True)
    run = run_setup(sandbox, "--from", "verifier", scenario=scenario)
    assert run.returncode == 1
    assert "setup: incomplete: updater, report did not complete" in run.stderr
    assert _resume(run) == _resume_at("updater")


def test_a_run_that_does_not_include_the_report_still_ends_with_the_pending_closing_line(
    tmp_path: Path,
) -> None:
    sandbox = _ready(tmp_path)
    run = run_setup(sandbox, "--only", "updater", scenario=GOOD)
    assert run.returncode == 0, run.output
    assert run.stdout.strip().splitlines()[-1].startswith("setup: ran updater")
    assert "setup is complete" not in run.output
