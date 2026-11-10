"""Gio's to-do reminders: definitions, done-state, and read-only hints.

Definitions live in config/reminders.yaml (tracked; agents append to it).
What Gio has ticked — and any notes added from the UI — lives in
data/reminders.json (untracked), so ticks survive app restarts and are the
same on every device that opens the app over Tailscale.

A reminder may name a `check`: a cheap, read-only detector that looks for
evidence the step happened (a backup file, archive manifests, a v2 export …)
and returns a hint. Hints never tick a box — only Gio does.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class Reminder:
    id: str
    title: str
    section: str = "General"
    details: str = ""
    command: str | None = None
    check: str | None = None
    custom: bool = False


def load_reminders(path: Path) -> list[Reminder]:
    if not path.exists():
        return []
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    out: list[Reminder] = []
    seen: set[str] = set()
    for item in raw.get("reminders") or []:
        rid = str(item["id"])
        if rid in seen:
            raise ValueError(f"duplicate reminder id {rid!r} in {path}")
        seen.add(rid)
        out.append(
            Reminder(
                id=rid,
                title=str(item["title"]),
                section=str(item.get("section") or "General"),
                details=str(item.get("details") or "").strip(),
                command=item.get("command"),
                check=item.get("check"),
            )
        )
    return out


class ReminderState:
    """data/reminders.json: {"done": {id: iso}, "custom": [{id, title, details, …}]}."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.done: dict[str, str] = {}
        self.custom: list[dict[str, Any]] = []
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8") or "{}")
            self.done = {str(k): str(v) for k, v in (data.get("done") or {}).items()}
            self.custom = list(data.get("custom") or [])

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps({"done": self.done, "custom": self.custom}, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        tmp.replace(self.path)

    def set_done(self, reminder_id: str, done: bool) -> None:
        if done:
            self.done.setdefault(reminder_id, datetime.now(UTC).isoformat(timespec="seconds"))
        else:
            self.done.pop(reminder_id, None)
        self._save()

    def add_custom(self, title: str, details: str = "", section: str = "My notes") -> str:
        title = title.strip()
        if not title:
            raise ValueError("a reminder needs a title")
        rid = f"custom-{uuid.uuid4().hex[:8]}"
        self.custom.append(
            {
                "id": rid,
                "title": title,
                "details": details.strip(),
                "section": section,
                "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
            }
        )
        self._save()
        return rid

    def delete_custom(self, reminder_id: str) -> None:
        self.custom = [c for c in self.custom if c["id"] != reminder_id]
        self.done.pop(reminder_id, None)
        self._save()

    def custom_reminders(self) -> list[Reminder]:
        return [
            Reminder(
                id=str(c["id"]),
                title=str(c["title"]),
                section=str(c.get("section") or "My notes"),
                details=str(c.get("details") or ""),
                custom=True,
            )
            for c in self.custom
        ]


# ---------------------------------------------------------------------------
# Read-only detectors: (data_dir) -> hint text, or None when nothing is found
# ---------------------------------------------------------------------------


def _manifests(data_dir: Path) -> list[dict[str, Any]]:
    out = []
    for path in (data_dir / "manifests").glob("*.json"):
        try:
            out.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    return out


def _annotation_query(data_dir: Path, sql: str) -> list[sqlite3.Row] | None:
    db = data_dir / "db" / "annotation.sqlite3"
    if not db.exists():
        return None
    try:
        conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True, timeout=5)
        conn.row_factory = sqlite3.Row
        try:
            return list(conn.execute(sql).fetchall())
        finally:
            conn.close()
    except sqlite3.Error:
        return None


def _check_db_backup(data_dir: Path) -> str | None:
    main = data_dir / "db" / "annotation.sqlite3"
    backups = [
        p for p in (data_dir / "db").glob("annotation*.sqlite3") if p.resolve() != main.resolve()
    ]
    if not backups:
        return None
    newest = max(backups, key=lambda p: p.stat().st_mtime)
    when = datetime.fromtimestamp(newest.stat().st_mtime, tz=UTC).date().isoformat()
    return f"Backup found: {newest.name} ({when})"


def _check_archive_runs(data_dir: Path) -> str | None:
    runs = [m for m in _manifests(data_dir) if m.get("source_type") == "archive"]
    if not runs:
        return None
    posts = sum(int(m.get("actual_submissions_stored") or 0) for m in runs)
    comments = sum(int(m.get("comments_stored") or 0) for m in runs)
    return f"{len(runs)} archive runs: {posts:,} posts, {comments:,} comments stored"


def _check_shreddit_recent(data_dir: Path) -> str | None:
    runs = [
        m
        for m in _manifests(data_dir)
        if m.get("source_type") == "shreddit"
        and str(m.get("started_at_utc") or "") >= "2026-11-08"
    ]
    if not runs:
        return None
    posts = sum(int(m.get("actual_submissions_stored") or 0) for m in runs)
    return f"{len(runs)} Shreddit runs since 2026-11-08: {posts:,} posts stored"


def _check_phatic_selected(data_dir: Path) -> str | None:
    rows = _annotation_query(
        data_dir, "SELECT COUNT(*) AS n FROM candidates WHERE exclusion_reason = 'phatic'"
    )
    if not rows or not rows[0]["n"]:
        return None
    return f"Selection has run with the phatic filter: {rows[0]['n']:,} items excluded"


def _check_low_conf_queue(data_dir: Path) -> str | None:
    rows = _annotation_query(
        data_dir,
        "SELECT SUM(completed = 0) AS pending, SUM(completed = 1) AS done "
        "FROM review_queue WHERE reason = 'low_confidence'",
    )
    if not rows or rows[0]["pending"] is None:
        return None
    return f"low_confidence queue: {rows[0]['pending']} pending, {rows[0]['done']} done"


def _check_gold_progress(data_dir: Path) -> str | None:
    rows = _annotation_query(
        data_dir,
        "SELECT SUM(completed = 1) AS done, COUNT(*) AS total "
        "FROM review_queue WHERE reason = 'gold'",
    )
    if not rows or not rows[0]["total"]:
        return None
    return f"Gold subset: {rows[0]['done']} / {rows[0]['total']} labeled"


def _check_export_v2(data_dir: Path) -> str | None:
    path = data_dir / "annotated" / "dataset-v2.jsonl"
    if not path.exists():
        return None
    when = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC).date().isoformat()
    return f"dataset-v2.jsonl exported {when}"


def _check_proxies_file(data_dir: Path) -> str | None:
    """proxies.txt sits at the repo root, next to data/."""
    from uyam.proxies_admin import read_file

    path = data_dir.parent / "proxies.txt"
    if not path.exists():
        return None
    parsed = read_file(path)
    when = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC).date().isoformat()
    providers = ", ".join(f"{p} ({n:,})" for p, n in parsed.providers.most_common(3))
    return f"proxies.txt: {len(parsed.urls):,} proxies — {providers or 'none'}; changed {when}"


CHECKS: dict[str, Callable[[Path], str | None]] = {
    "proxies_file": _check_proxies_file,
    "db_backup": _check_db_backup,
    "archive_runs": _check_archive_runs,
    "shreddit_recent": _check_shreddit_recent,
    "phatic_selected": _check_phatic_selected,
    "low_conf_queue": _check_low_conf_queue,
    "gold_progress": _check_gold_progress,
    "export_v2": _check_export_v2,
}


def run_check(name: str | None, data_dir: Path) -> str | None:
    """Hint for one reminder. Unknown names and detector errors yield None."""
    if not name or name not in CHECKS:
        return None
    try:
        return CHECKS[name](data_dir)
    except OSError:
        return None
