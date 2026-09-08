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
    monkeypatch.setattr(jobs, "_PROCS", {})
    return d


def _write_meta(jobs_dir: Path, name: str, pid: int, parent: str | None = None) -> None:
    jobs_dir.mkdir(parents=True, exist_ok=True)
    (jobs_dir / f"{name}.json").write_text(
        json.dumps(
            {
                "pid": pid,
                "argv": [],
                "started_at": "2026-08-25T00:00:00+00:00",
                "parent": parent,
            }
        ),
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


def test_tail_log_reads_only_the_tail_of_big_logs(jobs_dir: Path) -> None:
    jobs_dir.mkdir(parents=True, exist_ok=True)
    body = "".join(f"line {i} — emoji 🙂\n" for i in range(5000))
    jobs.log_path("big").write_bytes(body.encode("utf-8"))  # no CRLF translation
    tail = jobs.tail_log("big", max_chars=200)
    assert tail == body[-200:]
    assert tail.endswith("line 4999 — emoji 🙂\n")


def test_start_job_refuses_duplicate(jobs_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import os

    _write_meta(jobs_dir, "run-qwen3", os.getpid())
    with pytest.raises(RuntimeError, match="already running"):
        jobs.start_job("run-qwen3", ["run", "--annotator", "qwen3"])


def test_child_jobs_and_stop_job_tree(jobs_dir: Path) -> None:
    children = []
    try:
        for name in ("run-all-local", "run-all-remote"):
            proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
            children.append(proc)
            _write_meta(jobs_dir, name, proc.pid, parent="pipeline")
        parent = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        children.append(parent)
        _write_meta(jobs_dir, "pipeline", parent.pid)
        _write_meta(jobs_dir, "lid", 0)  # unrelated, not running

        assert jobs.child_jobs("pipeline") == ["run-all-local", "run-all-remote"]
        assert jobs.child_jobs("nobody") == []

        stopped = jobs.stop_job_tree("pipeline")
        assert stopped == ["run-all-local", "run-all-remote", "pipeline"]
        deadline = time.time() + 10
        while time.time() < deadline and any(
            jobs.is_running(n) for n in ("run-all-local", "run-all-remote", "pipeline")
        ):
            time.sleep(0.1)
        assert not jobs.is_running("pipeline")
        assert not jobs.is_running("run-all-local")
    finally:
        for proc in children:
            if proc.poll() is None:
                proc.kill()


def test_wait_job_returns_exit_code_for_own_children(
    jobs_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """start_job + wait_job round-trip on a real (tiny) child process."""
    monkeypatch.setattr(jobs, "_REPO_ROOT", jobs_dir.parent)

    real_popen = subprocess.Popen

    def fake_popen(cmd: list[str], **kwargs: object) -> subprocess.Popen[bytes]:
        # Replace `python -m uyam.cli annotate ...` with a trivial exit-3 script.
        assert cmd[1:4] == ["-m", "uyam.cli", "annotate"]
        return real_popen([sys.executable, "-c", "import sys; sys.exit(3)"])

    monkeypatch.setattr(jobs.subprocess, "Popen", fake_popen)
    pid = jobs.start_job("quick", ["status"], parent="pipeline")
    assert pid > 0
    assert jobs.read_meta("quick")["parent"] == "pipeline"
    assert jobs.wait_job("quick", poll_seconds=0.05) == 3
    # An adopted job (not started here) only reports liveness: returns None once gone.
    _write_meta(jobs_dir, "ghost", 0)
    assert jobs.wait_job("ghost", poll_seconds=0.05) is None
