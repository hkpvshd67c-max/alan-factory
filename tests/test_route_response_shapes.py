from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from alan import engine_config as engine_config_mod
from alan import operator_session, server
from alan import reconciler as _reconciler_mod
from alan.claude_cli import ChatTurnResult
from alan.finisher_rate import FinisherRate, FunnelCounts, LeakReasons
from alan.health import (
    HEALTH_META_KEY,
    HealthFinding,
    HealthState,
    save_finisher_rate,
    save_health_state,
    save_learning_metrics,
)
from alan.learning_metrics import LearningMetrics
from alan.routes._deps import require_control_token
from alan.workspace import create
from tests.conftest import FakeTmux

_ENGINE_KEY = "DejvMatikar/alan"
_BUILD_BODY = {"engine_key": _ENGINE_KEY}
_EXPLORE_BODY = {"consultant_key": "brainstorming", "engine_key": _ENGINE_KEY}

_QUALITY_METRICS_KEYS = {
    "build_success_rate",
    "in_flight_build_count",
    "unproven_clause_count",
    "first_pass_approval_rate",
    "mean_fix_cycles_to_merge",
    "sample_size",
    "computed_at",
    "format_error_rate",
    "format_error_sample",
    "format_error_low_data",
    "recovery_rate",
    "recovery_sample",
    "recovery_low_data",
    "rescue_success_rate",
    "rescue_success_rate_sample",
    "rescue_success_rate_low_data",
    "rescue_load_rate",
    "rescue_load_rate_sample",
    "rescue_load_rate_low_data",
}

_HEALTH_FINDING_KEYS = {
    "finding_id", "kind", "severity", "description", "workspace_id", "pr_number",
    "blocker_flags", "action", "in_flight", "repo", "engine_key", "observed_at",
}

_SWEEPER_KEYS = {
    "timestamp_utc", "surface_count", "blocked_count", "recent_actions", "next_eligible_utc",
}

_REPO_RATE_FIELDS = {"outstanding_findings", "in_flight"}

_WINDOWED_METRICS = {
    "outcome", "autonomous", "build_success", "first_pass_approval", "unproven_clauses",
    "format_error", "correction_recovery", "rescue_success", "rescue_load",
}


@pytest.fixture(autouse=True)
def _stub_seed_triage_sweep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        _reconciler_mod, "seed_triage_sweep", lambda *, queue, workspaces_root: None
    )


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(server.app) as c:
        yield c


@pytest.fixture
def bc_client(ws_root: Path, tmp_path: Path) -> Iterator[TestClient]:
    bc_root = tmp_path / "bc-sessions"
    server.app.dependency_overrides[server._build_chat_sessions_root_dep] = lambda: bc_root
    server.app.dependency_overrides[server._workspaces_root_dep] = lambda: ws_root
    try:
        with TestClient(server.app) as c:
            yield c
    finally:
        server.app.dependency_overrides.clear()


@pytest.fixture
def ec_client(ws_root: Path, tmp_path: Path) -> Iterator[TestClient]:
    ec_root = tmp_path / "ec-sessions"
    server.app.dependency_overrides[server._explore_chat_sessions_root_dep] = lambda: ec_root
    server.app.dependency_overrides[server._workspaces_root_dep] = lambda: ws_root
    try:
        with TestClient(server.app) as c:
            yield c
    finally:
        server.app.dependency_overrides.clear()


@pytest.fixture
def stub_run(monkeypatch):
    calls: list[dict[str, Any]] = []

    def _set(
        factory: Callable[[dict[str, Any], int], ChatTurnResult]
    ) -> list[dict[str, Any]]:
        def fake(**kwargs: Any) -> ChatTurnResult:
            calls.append(kwargs)
            return factory(kwargs, len(calls))

        monkeypatch.setattr("alan.routes._chat_router.run_chat_turn", fake)
        return calls

    return _set


def test_health_response_exact_key_set(client: TestClient) -> None:
    res = client.get("/api/health")
    assert res.status_code == 200
    assert set(res.json().keys()) == {
        "ok",
        "queue_paused",
        "paused_until",
        "health_state",
        "last_computed_at",
        "dispatch_loops_alive",
        "dispatch_loops_configured",
        "finisher_rate",
        "quality_metrics",
        "digest_overdue",
        "sweeper",
        "served_sha",
        "spend_cap_stop",
        "dispatch_readiness",
    }


def test_health_response_omits_phantom_frontend_fields(client: TestClient) -> None:
    data = client.get("/api/health").json()
    assert "resolved_findings" not in data
    assert "dismissed_findings" not in data
    assert "remediations" not in data


def test_health_state_and_finding_exact_key_sets(client: TestClient) -> None:
    finding = HealthFinding(
        finding_id="f1", kind="build_task", severity="high", description="d",
        workspace_id=None, pr_number=None, blocker_flags=[], action="dispatch_remediation",
    )
    state = HealthState(
        verdict="degraded", blocked_count=1, in_flight_count=0,
        findings=[finding], computed_at=datetime.now(UTC).isoformat(),
    )
    save_health_state(server.app.state.queue, state)
    health_state = client.get("/api/health").json()["health_state"]
    assert set(health_state.keys()) == {
        "verdict", "blocked_count", "in_flight_count", "computed_at", "findings",
    }
    assert "resolvable_findings" not in health_state
    assert set(health_state["findings"][0].keys()) == _HEALTH_FINDING_KEYS


def test_finisher_rate_exact_key_set(client: TestClient) -> None:
    fr = FinisherRate(
        rate=0.6,
        counts=FunnelCounts(10, 9, 8, 7, 6, 6, autonomous=5),
        leak_reasons=LeakReasons(crash_reasons={}, convergence_kinds={}),
        low_data=False,
        window_days=30,
        sample_size=10,
        floor_alert=False,
        autonomous_rate=0.5,
    )
    save_finisher_rate(server.app.state.queue, fr)
    finisher_rate = client.get("/api/health").json()["finisher_rate"]
    assert set(finisher_rate.keys()) == {
        "rate", "autonomous_rate", "counts", "leak_reasons", "low_data",
        "window_days", "sample_size", "floor_alert",
    }


def test_quality_metrics_exact_key_set_no_lm_blob(client: TestClient) -> None:
    quality_metrics = client.get("/api/health").json()["quality_metrics"]
    assert set(quality_metrics.keys()) == _QUALITY_METRICS_KEYS


def test_quality_metrics_exact_key_set_with_lm_blob(client: TestClient) -> None:
    save_learning_metrics(
        server.app.state.queue,
        LearningMetrics(
            first_pass_approval_rate=0.8, mean_fix_cycles_to_merge=1.5, sample_size=10
        ),
        datetime.now(UTC),
    )
    quality_metrics = client.get("/api/health").json()["quality_metrics"]
    assert set(quality_metrics.keys()) == _QUALITY_METRICS_KEYS


def test_sweeper_exact_key_set_when_no_audit_file(client: TestClient) -> None:
    sweeper = client.get("/api/health").json()["sweeper"]
    assert set(sweeper.keys()) == _SWEEPER_KEYS


def test_sweeper_exact_key_set_when_populated(tmp_path: Path, client: TestClient) -> None:
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    (logs_dir / "triage_audit.jsonl").write_text(
        json.dumps({
            "action": "sweep_complete",
            "timestamp_utc": "2026-07-11T09:00:00+00:00",
            "task_id": "t",
            "inv_id": "inv_001",
            "surface_count": 4,
            "blocked_count": 1,
        }) + "\n",
        encoding="utf-8",
    )
    sweeper = client.get("/api/health").json()["sweeper"]
    assert set(sweeper.keys()) == _SWEEPER_KEYS


def test_health_repos_envelope_and_row_exact_key_sets(tmp_path: Path) -> None:
    engine_config_mod.register(
        engine_config_mod.default_engine_config(), default=True, validate_env=False
    )
    server.app.dependency_overrides[server._workspaces_root_dep] = lambda: tmp_path
    try:
        with TestClient(server.app) as client:
            server.app.state.queue.set_meta(
                HEALTH_META_KEY,
                json.dumps({
                    "verdict": "healthy",
                    "blocked_count": 0,
                    "in_flight_count": 0,
                    "findings": [],
                    "computed_at": datetime.now(UTC).isoformat(),
                }),
            )
            res = client.get("/api/health/repos")
    finally:
        server.app.dependency_overrides.clear()
    data = res.json()
    assert set(data.keys()) == {
        "computed_at", "metrics_computed_at", "unattributed", "fleet", "rows",
    }
    assert set(data["fleet"].keys()) == {*_WINDOWED_METRICS, "funnel"}
    row = data["rows"][0]
    assert set(row.keys()) == {"repo", "windows", *_REPO_RATE_FIELDS}
    for rate_key in _REPO_RATE_FIELDS:
        assert set(row[rate_key].keys()) == {"value", "sample", "low_data"}
    assert type(row["in_flight"]["value"]) is int
    assert set(row["windows"].keys()) == {*_WINDOWED_METRICS, "funnel"}
    metric = row["windows"]["outcome"]
    assert set(metric.keys()) == {"d7", "d30", "verdict", "higher_is_better"}
    assert set(metric["d30"].keys()) == {"value", "sample", "covered_days"}


def test_build_chat_message_send_returns_exact_ack_body(
    bc_client: TestClient, stub_run: Callable[..., list[dict[str, Any]]]
) -> None:
    stub_run(lambda kw, n: ChatTurnResult(result_text="ok", claude_session_id=kw["session_id"]))
    sid = bc_client.post("/api/build-chat/sessions", json=_BUILD_BODY).json()["session_id"]
    res = bc_client.post(f"/api/build-chat/sessions/{sid}/messages", json={"content": "hi"})
    assert res.status_code == 202
    assert res.json() == {"status": "accepted", "prep_chat_reads": []}


def test_explore_chat_message_send_returns_exact_ack_body(
    ec_client: TestClient, stub_run: Callable[..., list[dict[str, Any]]]
) -> None:
    stub_run(lambda kw, n: ChatTurnResult(result_text="ok", claude_session_id=kw["session_id"]))
    sid = ec_client.post("/api/explore-chat/sessions", json=_EXPLORE_BODY).json()["session_id"]
    res = ec_client.post(f"/api/explore-chat/sessions/{sid}/messages", json={"content": "hi"})
    assert res.status_code == 202
    assert res.json() == {"status": "accepted", "prep_chat_reads": []}


def _flatten_routes(routes: list[Any]) -> list[Any]:
    flat: list[Any] = []
    for route in routes:
        original_router = getattr(route, "original_router", None)
        if original_router is not None:
            flat.extend(_flatten_routes(original_router.routes))
        else:
            flat.append(route)
    return flat


def test_control_token_requirement_invisible_in_schema_but_reachable_in_dependant_graph() -> None:
    schema_text = server.openapi_json()
    assert "X-Alan-Control-Token" not in schema_text

    def _reaches_control_token(dependant: Any) -> bool:
        if dependant.call is require_control_token:
            return True
        return any(_reaches_control_token(d) for d in dependant.dependencies)

    gated_paths: set[str] = set()
    for route in _flatten_routes(server.app.routes):
        dependant = getattr(route, "dependant", None)
        if dependant is not None and _reaches_control_token(dependant):
            gated_paths.add(route.path)

    assert "/api/queue/resume" in gated_paths
    assert "/api/operator/session" in gated_paths
    assert "/api/operator/attach-ticket" in gated_paths
    assert "/api/operator/status" not in gated_paths


_OPERATOR_SESSION_KEYS = {"scope", "target", "started_at", "update_warning", "exited"}


def test_operator_status_exact_key_set_without_a_session(
    client: TestClient, fake_tmux: FakeTmux, monkeypatch: pytest.MonkeyPatch
) -> None:
    del fake_tmux
    monkeypatch.setattr(operator_session, "availability", lambda: None)
    body = client.get("/api/operator/status").json()
    assert set(body) == {"session", "unavailable"}
    assert body == {"session": None, "unavailable": None}


def test_operator_status_exact_key_set_when_unavailable(
    client: TestClient, fake_tmux: FakeTmux, monkeypatch: pytest.MonkeyPatch
) -> None:
    del fake_tmux
    monkeypatch.setattr(
        operator_session,
        "availability",
        lambda: operator_session.Unavailable(reason="tmux_missing", detail="no tmux"),
    )
    body = client.get("/api/operator/status").json()
    assert set(body["unavailable"]) == {"reason", "detail"}


def test_operator_status_exact_key_set_with_a_live_session(
    client: TestClient, fake_tmux: FakeTmux
) -> None:
    fake_tmux.live = True
    body = client.get("/api/operator/status").json()
    assert set(body["session"]) == _OPERATOR_SESSION_KEYS


def test_workspace_detail_response_carries_the_operator_link_flag(
    client: TestClient, tmp_path: Path
) -> None:
    ws_root = tmp_path / "shape-workspaces"
    server.app.dependency_overrides[server._workspaces_root_dep] = lambda: ws_root  # noqa: SLF001
    try:
        ws = create("Shape WS", "d", root=ws_root)
        body = client.get(f"/api/workspaces/{ws.task_id}").json()
    finally:
        server.app.dependency_overrides.clear()
    assert body["operator_link"] is False
