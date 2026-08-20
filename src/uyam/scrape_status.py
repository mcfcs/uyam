"""Shared scrape-status files so the website can show a live captcha preview.

The Chrome window is the interactive captcha UI. These files are a mirror for
Streamlit: a screenshot + JSON state. Nothing here solves the challenge.
"""

from __future__ import annotations

import contextlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[2]
STATUS_DIR = _REPO_ROOT / "data" / ".scrape-status"
STATUS_FILE = STATUS_DIR / "status.json"
SCREENSHOT_FILE = STATUS_DIR / "captcha.png"


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
    payload = {
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


def clear_status() -> None:
    with contextlib.suppress(OSError):
        if STATUS_FILE.exists():
            STATUS_FILE.unlink()
    with contextlib.suppress(OSError):
        if SCREENSHOT_FILE.exists():
            SCREENSHOT_FILE.unlink()
