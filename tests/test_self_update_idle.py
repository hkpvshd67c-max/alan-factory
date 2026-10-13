from __future__ import annotations

import json
from pathlib import Path

import pytest

from alan import queue, self_update
from alan.self_update import _idle, _journal, _leftover, _run, _soak
from tests.self_update_apply_fakes import HEALTH_URL, ApplyScenario, build_apply_scenario
from tests.self_update_fixtures import link_executable, push_run

_NOW = 1_800_000_000.0
_BOUND_SCRIPT_PYTHON = '#!/bin/bash\nprintf \'%s\' "$(cat "$0.stdout")"\nexit "$(cat "$0.code")"\n'


def _seed_bound_script(repo: Path, *, stdout: str, returncode: int) -> None:
    python = repo / ".venv" / "bin" / "python"
    link_executable(python, _BOUND_SCRIPT_PYTHON)
    Path(f"{python}.stdout").write_text(stdout, encoding="utf-8")
    Path(f"{python}.code").write_text(str(returncode), encoding="utf-8")
    script = repo / "scripts" / "never_live_wait_bound.py"
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text("", encoding="utf-8")


def test_wait_bound_s_parses_the_scripts_stdout(tmp_path: Path) -> None:
    _seed_bound_script(tmp_path, stdout="1234", returncode=0)
    assert _idle.wait_bound_s(tmp_path) == 1234.0


def test_wait_bound_s_is_none_when_the_script_exits_nonzero(tmp_path: Path) -> None:
    _seed_bound_script(tmp_path, stdout="1234", returncode=1)
    assert _idle.wait_bound_s(tmp_path) is None


def test_wait_bound_s_is_none_when_the_output_is_not_a_number(tmp_path: Path) -> None:
    _seed_bound_script(tmp_path, stdout="not-a-number", returncode=0)
    assert _idle.wait_bound_s(tmp_path) is None


def test_wait_bound_s_is_none_when_the_venv_python_does_not_exist(tmp_path: Path) -> None:
    script = tmp_path / "scripts" / "never_live_wait_bound.py"
    script.parent.mkdir(parents=True)
    script.write_text("", encoding="utf-8")
    assert _idle.wait_bound_s(tmp_path) is None


def test_wait_bound_s_is_none_when_the_parsed_bound_is_negative(tmp_path: Path) -> None:
    _seed_bound_script(tmp_path, stdout="-1", returncode=0)
    assert _idle.wait_bound_s(tmp_path) is None


def test_wait_bound_s_is_none_when_the_parsed_bound_is_not_finite(tmp_path: Path) -> None:
    _seed_bound_script(tmp_path, stdout="inf", returncode=0)
    assert _idle.wait_bound_s(tmp_path) is None


def test_queue_is_idle_when_no_db_file_exists(tmp_path: Path) -> None:
    assert _idle.queue_is_idle(tmp_path / "queue.db", _NOW) is True


def test_queue_is_idle_when_no_job_is_pending_or_running(tmp_path: Path) -> None:
    db_path = tmp_path / "queue.db"
    q = queue.Queue(db_path=db_path)
    q.enqueue("task-1", "team")
    job = q.claim()
    assert job is not None
    q.complete(job.id)
    q.close()
    assert _idle.queue_is_idle(db_path, _NOW) is True


def test_queue_is_not_idle_when_a_job_is_pending(tmp_path: Path) -> None:
    db_path = tmp_path / "queue.db"
    q = queue.Queue(db_path=db_path)
    q.enqueue("task-1", "team")
    q.close()
    assert _idle.queue_is_idle(db_path, _NOW) is False


def test_queue_is_not_idle_when_a_job_is_running(tmp_path: Path) -> None:
    db_path = tmp_path / "queue.db"
    q = queue.Queue(db_path=db_path)
    q.enqueue("task-1", "team")
    q.claim()
    q.close()
    assert _idle.queue_is_idle(db_path, _NOW) is False


def test_queue_is_idle_when_the_only_pending_job_is_parked_until_later(tmp_path: Path) -> None:
    db_path = tmp_path / "queue.db"
    q = queue.Queue(db_path=db_path)
    q.enqueue("task-1", "deploy_verify", eligible_at=_NOW + 60)
    q.close()
    assert _idle.queue_is_idle(db_path, _NOW) is True


def test_queue_is_not_idle_when_a_parked_job_has_come_due(tmp_path: Path) -> None:
    db_path = tmp_path / "queue.db"
    q = queue.Queue(db_path=db_path)
    q.enqueue("task-1", "deploy_verify", eligible_at=_NOW)
    q.close()
    assert _idle.queue_is_idle(db_path, _NOW) is False


def test_queue_is_idle_treats_a_malformed_database_as_busy(tmp_path: Path) -> None:
    db_path = tmp_path / "queue.db"
    db_path.write_bytes(b"not a sqlite database")
    assert _idle.queue_is_idle(db_path, _NOW) is False


def test_queue_is_idle_handles_a_db_path_containing_a_hash_and_question_mark(
    tmp_path: Path,
) -> None:
    weird = tmp_path / "a#b?c"
    weird.mkdir()
    db_path = weird / "queue.db"
    q = queue.Queue(db_path=db_path)
    q.enqueue("task-1", "team")
    q.close()
    assert _idle.queue_is_idle(db_path, _NOW) is False


def test_idle_probe_wraps_queue_is_idle(tmp_path: Path) -> None:
    probe = _idle.idle_probe(tmp_path / "queue.db")
    assert probe() is True


@pytest.fixture
def scenario(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> ApplyScenario:
    return build_apply_scenario(monkeypatch, tmp_path)


def test_scheduled_apply_skips_everything_when_the_bound_script_fails(
    scenario: ApplyScenario, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(_idle, "wait_bound_s", lambda repo: None)
    result = _run.main_apply(
        scenario.repo,
        scheduled=True,
        extracted=True,
        plist_path=scenario.plist_path,
        health_url=HEALTH_URL,
        idle_probe=lambda: True,
        wait_bound_s=0.0,
        detached=True,
        queue_db_path=tmp_path / "queue.db",
    )
    assert result == 0
    assert scenario.commands.mutations() == []
    assert not self_update.status_path().exists()


def test_scheduled_apply_uses_the_computed_bound_and_the_real_queue_idle_probe(
    scenario: ApplyScenario, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(_soak, "run", lambda target, url: True)
    monkeypatch.setattr(_idle, "wait_bound_s", lambda repo: 42.0)
    queue_db = tmp_path / "queue.db"
    result = _run.main_apply(
        scenario.repo,
        scheduled=True,
        extracted=True,
        plist_path=scenario.plist_path,
        health_url=HEALTH_URL,
        idle_probe=lambda: True,
        wait_bound_s=0.0,
        detached=True,
        queue_db_path=queue_db,
    )
    assert result == 0
    stored = _journal.read()
    assert stored is not None
    assert stored.phase is _journal.Phase.LAST_GOOD


def test_scheduled_apply_treats_a_missing_queue_db_path_as_always_idle(
    scenario: ApplyScenario, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(_soak, "run", lambda target, url: True)
    monkeypatch.setattr(_idle, "wait_bound_s", lambda repo: 5.0)
    result = _run.main_apply(
        scenario.repo,
        scheduled=True,
        extracted=True,
        plist_path=scenario.plist_path,
        health_url=HEALTH_URL,
        idle_probe=lambda: True,
        wait_bound_s=0.0,
        detached=True,
        queue_db_path=None,
    )
    assert result == 0
    stored = _journal.read()
    assert stored is not None
    assert stored.phase is _journal.Phase.LAST_GOOD


def test_scheduled_apply_resolves_a_leftover_before_computing_any_bound(
    scenario: ApplyScenario, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    journal = _journal.Journal(
        _journal.Phase.ROLLBACK_REFUSED_DIRTY, scenario.target, scenario.previous, "dist.a", True
    )
    _journal.write(journal)
    bound_calls: list[Path] = []

    def fake_wait_bound_s(repo: Path) -> float:
        bound_calls.append(repo)
        return 5.0

    monkeypatch.setattr(_idle, "wait_bound_s", fake_wait_bound_s)
    leftover_calls: list[_journal.Journal] = []
    monkeypatch.setattr(_leftover, "resolve", lambda repo, j, **kw: leftover_calls.append(j))
    result = _run.main_apply(
        scenario.repo,
        scheduled=True,
        extracted=True,
        plist_path=scenario.plist_path,
        health_url=HEALTH_URL,
        idle_probe=lambda: True,
        wait_bound_s=0.0,
        detached=True,
        queue_db_path=tmp_path / "queue.db",
    )
    assert result == 0
    assert len(leftover_calls) == 1
    assert bound_calls == []


def test_scheduled_apply_ignores_a_force_flag_and_still_gates_on_ci(
    scenario: ApplyScenario, monkeypatch: pytest.MonkeyPatch
) -> None:
    scenario.base.set_runs(push_run(scenario.target, conclusion="failure"))
    monkeypatch.setattr(_idle, "wait_bound_s", lambda repo: 5.0)
    result = _run.main_apply(
        scenario.repo,
        scheduled=True,
        extracted=True,
        plist_path=scenario.plist_path,
        health_url=HEALTH_URL,
        idle_probe=lambda: True,
        wait_bound_s=0.0,
        detached=True,
        force=True,
        queue_db_path=None,
    )
    assert result == 0
    stored = json.loads(self_update.status_path().read_text(encoding="utf-8"))
    assert stored["verdict"] == "red"


def test_perform_forwards_force_through_to_the_real_apply(
    scenario: ApplyScenario, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(_soak, "run", lambda target, url: True)
    scenario.base.set_runs(push_run(scenario.target, conclusion="failure"))
    record = _run.perform(
        scenario.repo,
        scheduled=False,
        plist_path=scenario.plist_path,
        health_url=HEALTH_URL,
        idle_probe=lambda: True,
        wait_bound_s=0.0,
        detached=True,
        force=True,
    )
    assert record is not None
    assert record.verdict is self_update.Verdict.APPLIED


def test_trampoline_forwards_sha_force_and_queue_db_path(
    scenario: ApplyScenario, tmp_path: Path
) -> None:
    queue_db = tmp_path / "queue.db"
    result = _run.trampoline(
        scenario.repo,
        scheduled=True,
        plist_path=scenario.plist_path,
        health_url=HEALTH_URL,
        pin="a" * 40,
        force=True,
        queue_db_path=queue_db,
    )
    assert result == 0
    _python, argv = scenario.execs.calls[0]
    assert argv[argv.index("--queue-db-path") + 1] == str(queue_db)
    assert argv[argv.index("--sha") + 1] == "a" * 40
    assert "--force" in argv
