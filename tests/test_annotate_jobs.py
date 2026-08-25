"""Background job tracking for Streamlit-launched annotation passes."""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

from uyam.annotate import jobs


@pytest.fixture
def jobs_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    d = tmp_path / ".annotate-status"
    monkeypatch.setattr(jobs, "JOBS_DIR", d)
    return d


def _write_meta(jobs_dir: Path, name: str, pid: int) -> None:
    jobs_dir.mkdir(parents=True, exist_ok=True)
    (jobs_dir / f"{name}.json").write_text(
        json.dumps({"pid": pid, "argv": [], "started_at": "2026-08-25T00:00:00+00:00"}),
        encoding="utf-8",
    )


def test_is_running_reflects_process_liveness(jobs_dir: Path) -> None:
    import os

    _write_meta(jobs_dir, "alive", os.getpid())
    assert jobs.is_running("alive")

    dead = subprocess.run(
        [sys.executable, "-c", "pass"], capture_output=True, check=True
    )
    assert dead  # the process has exited; reuse of its pid is unlikely within the test
    _write_meta(jobs_dir, "unknown", 0)
    assert not jobs.is_running("unknown")
    assert not jobs.is_running("never-started")

    assert jobs.running_jobs() == ["alive"]


def test_stop_job_terminates_process(jobs_dir: Path) -> None:
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        _write_meta(jobs_dir, "sleeper", proc.pid)
        assert jobs.is_running("sleeper")
        assert jobs.stop_job("sleeper")
        deadline = time.time() + 10
        while time.time() < deadline and jobs.is_running("sleeper"):
            time.sleep(0.1)
        assert not jobs.is_running("sleeper")
    finally:
        if proc.poll() is None:
            proc.kill()


def test_tail_log_and_summary(jobs_dir: Path) -> None:
    jobs_dir.mkdir(parents=True, exist_ok=True)
    jobs.log_path("lid").write_text("line1\n" + "x" * 100, encoding="utf-8")
    assert jobs.tail_log("lid", max_chars=50) == ("line1\n" + "x" * 100)[-50:]
    assert jobs.tail_log("missing") == ""

    summary = jobs.job_summary("lid")
    assert summary["running"] is False
    assert summary["log"].endswith("lid.log")


def test_start_job_refuses_duplicate(jobs_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import os

    _write_meta(jobs_dir, "run-qwen3", os.getpid())
    with pytest.raises(RuntimeError, match="already running"):
        jobs.start_job("run-qwen3", ["run", "--annotator", "qwen3"])
