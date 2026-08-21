"""Shared scrape status + pause/stop control files.

The Chrome window is the interactive captcha UI. These files are a mirror for
Streamlit: a screenshot, JSON state, and a control channel (run/pause/stop).
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
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

_VALID_COMMANDS = frozenset({"run", "pause", "stop"})


class ScrapeStopRequested(RuntimeError):
    """Raised when the operator hits Stop."""


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


def check_control() -> None:
    """Block while paused; raise ScrapeStopRequested on stop."""
    paused_logged = False
    while get_control() == "pause":
        if not paused_logged:
            logger.warning("scrape_paused")
            write_status("paused", "Scraper paused — hit Resume to continue.")
            paused_logged = True
        time.sleep(0.4)
    if get_control() == "stop":
        logger.warning("scrape_stop_requested")
        write_status("stopped", "Scraper stopped by operator.")
        raise ScrapeStopRequested("Stopped by operator")


def write_run_meta(*, pid: int, argv: list[str] | None = None) -> None:
    status_dir()
    RUN_FILE.write_text(
        json.dumps(
            {
                "pid": pid,
                "argv": argv or [],
                "started_at": datetime.now(UTC).isoformat(),
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


def process_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def scrape_is_running() -> bool:
    meta = read_run_meta()
    if not meta:
        return False
    try:
        pid = int(meta.get("pid") or 0)
    except (TypeError, ValueError):
        return False
    return process_alive(pid)


def request_stop_and_kill() -> None:
    set_control("stop")
    meta = read_run_meta()
    if not meta:
        return
    try:
        pid = int(meta.get("pid") or 0)
    except (TypeError, ValueError):
        return
    if pid and process_alive(pid) and pid != os.getpid():
        with contextlib.suppress(OSError):
            os.kill(pid, 15)


def clear_status() -> None:
    for path in (STATUS_FILE, SCREENSHOT_FILE, CONTROL_FILE, RUN_FILE):
        with contextlib.suppress(OSError):
            if path.exists():
                path.unlink()
