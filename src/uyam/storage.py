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
