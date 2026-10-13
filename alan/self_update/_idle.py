from __future__ import annotations

import math
import sqlite3
import subprocess
import time
from collections.abc import Callable
from pathlib import Path

from alan import process_runner

_BOUND_TIMEOUT_S = 30.0
_QUEUE_TIMEOUT_S = 5.0


def wait_bound_s(repo: Path) -> float | None:
    python = repo / ".venv" / "bin" / "python"
    script = repo / "scripts" / "never_live_wait_bound.py"
    try:
        result = process_runner.run_subprocess(
            [str(python), str(script)], cwd=None, timeout=_BOUND_TIMEOUT_S
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    try:
        bound = float(result.stdout.strip())
    except ValueError:
        return None
    if not math.isfinite(bound) or bound < 0:
        return None
    return bound


_BUSY_JOB_QUERY = (
    "SELECT 1 FROM jobs WHERE status = 'running' OR (status = 'pending' "
    "AND (eligible_at IS NULL OR eligible_at <= ?)) LIMIT 1"
)


def queue_is_idle(db_path: Path, now: float) -> bool:
    if not db_path.is_file():
        return True
    try:
        uri = f"{db_path.resolve().as_uri()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=_QUEUE_TIMEOUT_S)
        try:
            row = conn.execute(_BUSY_JOB_QUERY, (now,)).fetchone()
        finally:
            conn.close()
    except (sqlite3.Error, OSError):
        return False
    return row is None


def idle_probe(db_path: Path) -> Callable[[], bool]:
    return lambda: queue_is_idle(db_path, time.time())
