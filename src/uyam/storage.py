"""JSONL storage and collection manifest writer for Uyám.

Raw normalized records are appended to:
  data/raw/<subreddit>/<YYYY-MM-DD>.jsonl

One manifest JSON file is written per collection run:
  data/manifests/<collection_run_id>.json

Files are never overwritten; JSONL files are appended to safely.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime
from pathlib import Path

from uyam.models import CollectionContext, CommentRecord, SubmissionRecord

logger = logging.getLogger(__name__)

AnyRecord = SubmissionRecord | CommentRecord


def raw_jsonl_path(data_dir: Path, subreddit: str) -> Path:
    """Return the JSONL path for today's records from a given subreddit."""
    date_str = datetime.now(UTC).strftime("%Y-%m-%d")
    path = data_dir / "raw" / subreddit / f"{date_str}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def record_identity(record: dict[str, object]) -> str | None:
    """Stable identity used to drop duplicate JSONL lines."""
    fullname = record.get("reddit_fullname")
    if isinstance(fullname, str) and fullname:
        return fullname
    reddit_id = record.get("reddit_id") or record.get("id")
    record_type = record.get("record_type") or ""
    if isinstance(reddit_id, str) and reddit_id:
        prefix = "t3_" if record_type == "submission" else "t1_"
        if reddit_id.startswith("t1_") or reddit_id.startswith("t3_"):
            return reddit_id
        return f"{prefix}{reddit_id}"
    return None


def scrub_author_field(data_dir: Path) -> dict[str, int]:
    """Remove the plaintext `author` key from every JSONL line under data/raw.

    Files written before 2026-09-08 stored the raw username next to
    author_hash. Each file is rewritten atomically (temp file + replace);
    unparseable lines (a crash-window partial line) are kept untouched.
    Idempotent: a second run rewrites nothing. Do not run during a scrape.
    """
    stats = {"files": 0, "files_rewritten": 0, "records_scrubbed": 0}
    raw_dir = data_dir / "raw"
    if not raw_dir.exists():
        return stats
    for path in sorted(raw_dir.glob("**/*.jsonl")):
        stats["files"] += 1
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        out_lines: list[str] = []
        changed = 0
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            try:
                parsed = json.loads(stripped)
            except json.JSONDecodeError:
                out_lines.append(line)
                continue
            if isinstance(parsed, dict) and "author" in parsed:
                parsed.pop("author")
                changed += 1
                out_lines.append(json.dumps(parsed, ensure_ascii=False))
            else:
                out_lines.append(line)
        if changed:
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_text("\n".join(out_lines) + "\n", encoding="utf-8")
            os.replace(tmp, path)
            stats["files_rewritten"] += 1
            stats["records_scrubbed"] += changed
    return stats


def load_jsonl_records(data_dir: Path, *, unique: bool = True) -> list[dict[str, object]]:
    """Load every JSONL line under data/raw. Incomplete last lines are skipped.

    When unique=True (default), keep the first occurrence of each reddit_fullname
    so crash-window duplicates and re-runs do not show up twice in the UI.
    """
    records: list[dict[str, object]] = []
    seen: set[str] = set()
    raw_dir = data_dir / "raw"
    if not raw_dir.exists():
        return records
    for filepath in sorted(raw_dir.glob("**/*.jsonl")):
        try:
            text = filepath.read_text(encoding="utf-8")
        except OSError:
            continue
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(parsed, dict):
                continue
            if unique:
                identity = record_identity(parsed)
                if identity:
                    if identity in seen:
                        continue
                    seen.add(identity)
            records.append(parsed)
    return records


def append_record(path: Path, record: AnyRecord) -> None:
    """Append one normalized record as a JSON line."""
    line = record.model_dump_json() + "\n"
    with path.open("a", encoding="utf-8") as fh:
        fh.write(line)
    logger.debug(
        "record_written",
        extra={
            "record_type": record.record_type,
            "reddit_fullname": record.reddit_fullname,
            "path": str(path),
        },
    )


def write_manifest(data_dir: Path, ctx: CollectionContext) -> Path:
    """Write the collection run manifest JSON and return the path."""
    manifests_dir = data_dir / "manifests"
    manifests_dir.mkdir(parents=True, exist_ok=True)
    path = manifests_dir / f"{ctx.collection_run_id}.json"

    payload = json.loads(ctx.model_dump_json())
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info(
        "manifest_written",
        extra={
            "collection_run_id": ctx.collection_run_id,
            "path": str(path),
        },
    )
    return path


def clear_collected_data(data_dir: Path) -> dict[str, int]:
    """Delete collected JSONL, manifests, and the SQLite index.

    Does not touch the Chrome profile or scrape-status control files.
    """
    deleted_jsonl = 0
    raw_dir = data_dir / "raw"
    if raw_dir.exists():
        for path in raw_dir.glob("**/*.jsonl"):
            path.unlink()
            deleted_jsonl += 1

    deleted_manifests = 0
    manifests_dir = data_dir / "manifests"
    if manifests_dir.exists():
        for path in manifests_dir.glob("*.json"):
            path.unlink()
            deleted_manifests += 1

    deleted_db = 0
    db_dir = data_dir / "db"
    if db_dir.exists():
        for path in db_dir.glob("collection.sqlite3*"):
            path.unlink()
            deleted_db += 1

    logger.warning(
        "collected_data_cleared",
        extra={
            "jsonl_files": deleted_jsonl,
            "manifests": deleted_manifests,
            "db_files": deleted_db,
        },
    )
    return {
        "jsonl_files": deleted_jsonl,
        "manifests": deleted_manifests,
        "db_files": deleted_db,
    }
