"""Shared scrape status + pause/stop control files.

The Chrome window is the interactive captcha UI. These files are a mirror for
Streamlit: a screenshot, JSON state, and a control channel (run/pause/stop).
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[2]
STATUS_DIR = _REPO_ROOT / "data" / ".scrape-status"
STATUS_FILE = STATUS_DIR / "status.json"
SCREENSHOT_FILE = STATUS_DIR / "captcha.png"
CONTROL_FILE = STATUS_DIR / "control.json"
RUN_FILE = STATUS_DIR / "run.json"
LOG_FILE = STATUS_DIR / "collect.log"
WORKERS_DIR = STATUS_DIR / "workers"

_VALID_COMMANDS = frozenset({"run", "pause", "stop"})


class ScrapeStopRequested(RuntimeError):
    """Raised when the operator hits Stop, or when a time limit expires."""

    def __init__(
        self,
        message: str = "Stopped by operator",
        *,
        reason: str = "stopped_by_user",
    ) -> None:
        super().__init__(message)
        self.reason = reason


def status_dir() -> Path:
    STATUS_DIR.mkdir(parents=True, exist_ok=True)
    return STATUS_DIR


def write_status(
    state: str,
    message: str,
    *,
    url: str = "",
    title: str = "",
) -> None:
    status_dir()
    existing: dict[str, Any] = {}
    if STATUS_FILE.exists():
        with contextlib.suppress(OSError, json.JSONDecodeError):
            loaded = json.loads(STATUS_FILE.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                existing = loaded
    payload = {
        **existing,
        "state": state,
        "message": message,
        "url": url,
        "title": title,
        "screenshot": str(SCREENSHOT_FILE) if SCREENSHOT_FILE.exists() else "",
        "updated_at": datetime.now(UTC).isoformat(),
    }
    STATUS_FILE.write_text(json.dumps(payload), encoding="utf-8")


def read_status() -> dict[str, Any] | None:
    if not STATUS_FILE.exists():
        return None
    try:
        data = json.loads(STATUS_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def set_control(command: str) -> None:
    cmd = command.strip().lower()
    if cmd not in _VALID_COMMANDS:
        raise ValueError(f"Unknown control command: {command!r}")
    status_dir()
    CONTROL_FILE.write_text(
        json.dumps({"command": cmd, "updated_at": datetime.now(UTC).isoformat()}),
        encoding="utf-8",
    )
    if cmd == "pause":
        write_status("paused", "Scraper paused — hit Resume to continue.")
    elif cmd == "stop":
        write_status("stopped", "Stop requested.")
    elif cmd == "run":
        write_status("running", "Scraper running.")


def get_control() -> str:
    if not CONTROL_FILE.exists():
        return "run"
    try:
        data = json.loads(CONTROL_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "run"
    cmd = str(data.get("command") or "run").lower()
    return cmd if cmd in _VALID_COMMANDS else "run"


def _deadline_unix() -> float | None:
    meta = read_run_meta()
    if not meta:
        return None
    raw = meta.get("deadline_at")
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt.timestamp()


def _raise_if_deadline() -> None:
    deadline = _deadline_unix()
    if deadline is None:
        return
    remaining = deadline - time.time()
    if remaining > 0:
        return
    logger.warning("scrape_time_limit_reached")
    write_status("stopped", "Time limit reached.")
    raise ScrapeStopRequested("Time limit reached", reason="time_limit_reached")


def check_control() -> None:
    """Block while paused; raise ScrapeStopRequested on stop or time limit."""
    paused_logged = False
    while get_control() == "pause":
        if not paused_logged:
            logger.warning("scrape_paused")
            write_status("paused", "Scraper paused — hit Resume to continue.")
            paused_logged = True
        _raise_if_deadline()
        time.sleep(0.4)
    if get_control() == "stop":
        logger.warning("scrape_stop_requested")
        write_status("stopped", "Scraper stopped by operator.")
        raise ScrapeStopRequested("Stopped by operator")
    _raise_if_deadline()


def write_run_meta(
    *,
    pid: int,
    argv: list[str] | None = None,
    max_seconds: float | None = None,
) -> None:
    status_dir()
    deadline_at: str | None = None
    if max_seconds is not None and max_seconds > 0:
        deadline_at = datetime.fromtimestamp(
            time.time() + float(max_seconds), tz=UTC
        ).isoformat()
    RUN_FILE.write_text(
        json.dumps(
            {
                "pid": pid,
                "argv": argv or [],
                "started_at": datetime.now(UTC).isoformat(),
                "max_seconds": max_seconds,
                "deadline_at": deadline_at,
            }
        ),
        encoding="utf-8",
    )


def read_run_meta() -> dict[str, Any] | None:
    if not RUN_FILE.exists():
        return None
    try:
        data = json.loads(RUN_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _win_process_alive(pid: int) -> bool:
    """True if pid is still running. os.kill(pid, 0) is invalid on Windows."""
    import ctypes
    from ctypes import wintypes

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    STILL_ACTIVE = 259
    handle = ctypes.windll.kernel32.OpenProcess(
        PROCESS_QUERY_LIMITED_INFORMATION, False, pid
    )
    if not handle:
        return False
    try:
        code = wintypes.DWORD()
        ok = ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        return bool(ok) and code.value == STILL_ACTIVE
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)


def process_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        if sys.platform == "win32":
            return _win_process_alive(pid)
        os.kill(pid, 0)
    except OSError:
        return False
    except Exception:
        return False
    return True


def scrape_is_running() -> bool:
    meta = read_run_meta()
    if meta:
        try:
            pid = int(meta.get("pid") or 0)
        except (TypeError, ValueError):
            pid = 0
        if process_alive(pid):
            return True
    return any(process_alive(pid) for pid in list_worker_pids())


def _terminate_pid(pid: int) -> None:
    if pid <= 0 or pid == os.getpid() or not process_alive(pid):
        return
    with contextlib.suppress(OSError):
        os.kill(pid, 15)


def request_stop_and_kill() -> None:
    set_control("stop")
    meta = read_run_meta()
    if meta:
        with contextlib.suppress(TypeError, ValueError):
            _terminate_pid(int(meta.get("pid") or 0))
    for pid in list_worker_pids():
        _terminate_pid(pid)


def reset_worker_pids() -> None:
    status_dir()
    WORKERS_DIR.mkdir(parents=True, exist_ok=True)
    for path in WORKERS_DIR.iterdir():
        with contextlib.suppress(OSError):
            if path.is_file():
                path.unlink()


def register_worker_pid(pid: int) -> None:
    WORKERS_DIR.mkdir(parents=True, exist_ok=True)
    (WORKERS_DIR / str(pid)).write_text("", encoding="utf-8")


def unregister_worker_pid(pid: int) -> None:
    path = WORKERS_DIR / str(pid)
    with contextlib.suppress(OSError):
        if path.exists():
            path.unlink()


def list_worker_pids() -> list[int]:
    if not WORKERS_DIR.exists():
        return []
    pids: list[int] = []
    for path in WORKERS_DIR.iterdir():
        try:
            pids.append(int(path.name))
        except ValueError:
            continue
    return pids


def clear_status() -> None:
    for path in (STATUS_FILE, SCREENSHOT_FILE, CONTROL_FILE, RUN_FILE):
        with contextlib.suppress(OSError):
            if path.exists():
                path.unlink()
    reset_worker_pids()
