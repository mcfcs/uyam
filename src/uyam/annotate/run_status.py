"""Live status of an annotation run, for the Streamlit app.

Read-only: the database is opened with SQLite's mode=ro, so the panel can
never block or change a running job. One call per config file
(config/annotation*.yaml) returns per-annotator progress inside the target
set, the recent rate, an ETA for what is left, whether the lane job is
alive, and a "stalled" flag when a lane is running but nothing has been
written for a while.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from uyam.annotate import jobs
from uyam.annotate.config import AnnotationConfig, load_annotation_config

STALL_MINUTES = 10.0


@dataclass
class ModelStatus:
    key: str
    model: str
    done: int
    failed: int
    pending: int
    last_write: datetime | None
    rate_per_min: float  # items stored in the recent window, per minute
    sec_per_item: float | None  # recent pace, else the model's historical mean


def discover_runs(config_dir: Path) -> list[Path]:
    """annotation*.yaml files whose database exists, the default config first."""
    paths = sorted(
        config_dir.glob("annotation*.yaml"), key=lambda p: (p.stem != "annotation", p.name)
    )
    out: list[Path] = []
    for path in paths:
        try:
            cfg = load_annotation_config(path)
        except (OSError, ValueError):
            continue
        if cfg.db_path.exists():
            out.append(path)
    return out


def _parse(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        value = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _lane_jobs(cfg: AnnotationConfig) -> list[str]:
    stem = cfg.db_path.stem
    prefix = "" if stem == "annotation" else f"{stem}-"
    endpoints = list(dict.fromkeys(a.endpoint for a in cfg.annotators))
    return [f"{prefix}run-all-{ep}" for ep in endpoints]


def _target_table(
    conn: sqlite3.Connection, cfg: AnnotationConfig
) -> tuple[str, dict[str, Any], int]:
    """SQL selecting the target set's fullnames, its parameters, and its size.

    A pinned target set (target_priority, the v3 fork) is read directly. Any
    other database uses the general ranking, which is slower.
    """
    from uyam.annotate.db import AnnotationDatabase

    target = int(cfg.pipeline.target_items)
    try:
        pinned = conn.execute("SELECT COUNT(*) FROM target_priority").fetchone()[0]
    except sqlite3.OperationalError:
        pinned = 0
    if pinned and pinned >= target:
        sql = (
            "SELECT reddit_fullname FROM target_priority "
            "ORDER BY priority DESC, reddit_fullname LIMIT :n"
        )
        return sql, {"n": target}, target
    params = {"pv": cfg.prompt_version, "n": target}
    cte = AnnotationDatabase._TARGET_CTE
    size = conn.execute(cte + " SELECT COUNT(*) FROM target", params).fetchone()[0]
    return cte + " SELECT reddit_fullname FROM target", params, int(size)


def run_status(
    cfg: AnnotationConfig,
    *,
    now: datetime | None = None,
    window_minutes: float = 15.0,
    job_running: Any = None,
) -> dict[str, Any]:
    """Progress, pace and ETA of the run that ``cfg`` describes (read-only)."""
    now = now or datetime.now(UTC)
    is_running = job_running or jobs.is_running
    conn = sqlite3.connect(f"file:{cfg.db_path.as_posix()}?mode=ro", uri=True, timeout=10)
    try:
        conn.execute("CREATE TEMP TABLE run_target (reddit_fullname TEXT PRIMARY KEY)")
        target_sql, params, target_n = _target_table(conn, cfg)
        conn.execute(f"INSERT INTO run_target {target_sql}", params)
        since = (now - timedelta(minutes=window_minutes)).isoformat()
        models: list[ModelStatus] = []
        for ann in cfg.annotators:
            done = conn.execute(
                "SELECT COUNT(DISTINCT a.reddit_fullname) FROM llm_annotations a "
                "JOIN run_target t ON t.reddit_fullname = a.reddit_fullname "
                "WHERE a.model_key = ? AND a.prompt_version = ? AND a.role = 'annotator'",
                (ann.key, cfg.prompt_version),
            ).fetchone()[0]
            failed = conn.execute(
                "SELECT COUNT(DISTINCT f.reddit_fullname) FROM llm_failures f "
                "JOIN run_target t ON t.reddit_fullname = f.reddit_fullname "
                "WHERE f.model_key = ? AND f.prompt_version = ? AND f.role = 'annotator' "
                "AND NOT EXISTS (SELECT 1 FROM llm_annotations a "
                "  WHERE a.reddit_fullname = f.reddit_fullname AND a.model_key = f.model_key "
                "    AND a.prompt_version = f.prompt_version AND a.role = 'annotator')",
                (ann.key, cfg.prompt_version),
            ).fetchone()[0]
            last = conn.execute(
                "SELECT MAX(created_at) FROM llm_annotations "
                "WHERE model_key = ? AND role = 'annotator'",
                (ann.key,),
            ).fetchone()[0]
            recent = conn.execute(
                "SELECT COUNT(*) FROM llm_annotations WHERE model_key = ? AND role = 'annotator' "
                "AND created_at >= ?",
                (ann.key, since),
            ).fetchone()[0]
            hist = conn.execute(
                "SELECT AVG(duration_ms) FROM (SELECT duration_ms FROM llm_annotations "
                "WHERE model_key = ? AND role = 'annotator' AND duration_ms IS NOT NULL "
                "ORDER BY created_at DESC LIMIT 500)",
                (ann.key,),
            ).fetchone()[0]
            rate = recent / window_minutes
            pace = 60.0 / rate if rate > 0 else (float(hist) / 1000.0 if hist else None)
            models.append(
                ModelStatus(
                    key=ann.key,
                    model=ann.model,
                    done=int(done),
                    failed=int(failed),
                    pending=max(0, target_n - int(done) - int(failed)),
                    last_write=_parse(last),
                    rate_per_min=round(rate, 1),
                    sec_per_item=round(pace, 2) if pace else None,
                )
            )
        complete = conn.execute(
            "SELECT COUNT(*) FROM (SELECT a.reddit_fullname FROM llm_annotations a "
            "JOIN run_target t ON t.reddit_fullname = a.reddit_fullname "
            f"WHERE a.prompt_version = ? AND a.role = 'annotator' AND a.model_key IN "
            f"({','.join('?' for _ in cfg.annotators)}) "
            "GROUP BY a.reddit_fullname HAVING COUNT(DISTINCT a.model_key) = ?)",
            (cfg.prompt_version, *cfg.annotator_keys, len(cfg.annotators)),
        ).fetchone()[0]
    finally:
        conn.close()

    lanes = {name: bool(is_running(name)) for name in _lane_jobs(cfg)}
    running = any(lanes.values())
    active = [m for m in models if m.rate_per_min > 0]
    current = max(active, key=lambda m: m.last_write or now).key if active else None
    remaining_s = sum(m.pending * (m.sec_per_item or 0.0) for m in models)
    unknown_pace = [m.key for m in models if m.pending and not m.sec_per_item]
    last_any = max((m.last_write for m in models if m.last_write), default=None)
    stalled = bool(
        running and (last_any is None or now - last_any > timedelta(minutes=STALL_MINUTES))
    )
    return {
        "config": str(cfg.source_path) if cfg.source_path else None,
        "db": cfg.db_path.name,
        "target": target_n,
        "complete": int(complete),
        "models": models,
        "lanes": lanes,
        "running": running,
        "current": current,
        "remaining_hours": round(remaining_s / 3600.0, 1) if not unknown_pace else None,
        "eta": (now + timedelta(seconds=remaining_s)) if running and not unknown_pace else None,
        "unknown_pace": unknown_pace,
        "last_write": last_any,
        "stalled": stalled,
        "window_minutes": window_minutes,
    }


def lane_log_tail(cfg: AnnotationConfig, max_chars: int = 1500) -> str:
    """The end of the first lane job's log that has one (empty if none)."""
    for name in _lane_jobs(cfg):
        text = jobs.tail_log(name, max_chars=max_chars)
        if text:
            return text
    return ""
