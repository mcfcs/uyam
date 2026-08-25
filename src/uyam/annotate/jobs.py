"""Background job management for annotation passes launched from Streamlit.

Long passes (LID model download, GPU sentiment, hours-long LLM runs) run as
detached `python -m uyam.cli annotate ...` subprocesses that survive Streamlit
restarts — the same pattern the collector uses. Every job gets its own meta
JSON and log file under data/.annotate-status/.

Killing a job mid-item is safe: annotations are keyed UNIQUE per
(item, model, prompt_version), so a re-run resumes exactly where it stopped.
"""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from uyam.scrape_status import process_alive

_REPO_ROOT = Path(__file__).resolve().parents[3]
JOBS_DIR = _REPO_ROOT / "data" / ".annotate-status"


def _meta_path(name: str) -> Path:
    return JOBS_DIR / f"{name}.json"


def log_path(name: str) -> Path:
    return JOBS_DIR / f"{name}.log"


def _popen_kwargs() -> dict[str, Any]:
    """Detach from Streamlit so a restart does not kill an hours-long run."""
    kwargs: dict[str, Any] = {}
    if sys.platform == "win32":
        flags = subprocess.CREATE_NEW_PROCESS_GROUP
        breakaway = getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0x01000000)
        kwargs["creationflags"] = flags | breakaway
    else:
        kwargs["start_new_session"] = True
    return kwargs


def read_meta(name: str) -> dict[str, Any] | None:
    path = _meta_path(name)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def is_running(name: str) -> bool:
    meta = read_meta(name)
    if not meta:
        return False
    try:
        return process_alive(int(meta.get("pid") or 0))
    except (TypeError, ValueError):
        return False


def running_jobs() -> list[str]:
    if not JOBS_DIR.exists():
        return []
    return [p.stem for p in JOBS_DIR.glob("*.json") if is_running(p.stem)]


def start_job(name: str, annotate_args: list[str]) -> int:
    """Spawn `python -m uyam.cli annotate <args>` detached. Returns the pid."""
    if is_running(name):
        raise RuntimeError(f"Job {name!r} is already running")
    JOBS_DIR.mkdir(parents=True, exist_ok=True)

    cmd = [sys.executable, "-m", "uyam.cli", "annotate", *annotate_args]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(_REPO_ROOT / "src") + os.pathsep + env.get("PYTHONPATH", "")
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"

    log_handle = log_path(name).open("w", encoding="utf-8")
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(_REPO_ROOT),
            env=env,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            **_popen_kwargs(),
        )
    finally:
        log_handle.close()

    _meta_path(name).write_text(
        json.dumps(
            {
                "pid": proc.pid,
                "argv": cmd,
                "started_at": datetime.now(UTC).isoformat(),
            }
        ),
        encoding="utf-8",
    )
    return proc.pid


def stop_job(name: str) -> bool:
    """Terminate a running job. Resume-safe by design. Returns True if signaled."""
    meta = read_meta(name)
    if not meta:
        return False
    try:
        pid = int(meta.get("pid") or 0)
    except (TypeError, ValueError):
        return False
    if pid <= 0 or pid == os.getpid() or not process_alive(pid):
        return False
    with contextlib.suppress(OSError):
        os.kill(pid, 15)
    return True


def tail_log(name: str, max_chars: int = 4000) -> str:
    path = log_path(name)
    if not path.exists():
        return ""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return text[-max_chars:]


def job_summary(name: str) -> dict[str, Any]:
    meta = read_meta(name) or {}
    return {
        "name": name,
        "running": is_running(name),
        "pid": meta.get("pid"),
        "started_at": meta.get("started_at"),
        "log": str(log_path(name)),
    }
