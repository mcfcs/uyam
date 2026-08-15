"""Collection pipeline orchestration for Uyám.

Wires together: source → deduplication → JSONL storage → manifest.
The pipeline accepts any RedditSource and never imports fixture/PRAW modules.
"""

from __future__ import annotations

import logging
import subprocess
import uuid
from datetime import UTC, datetime
from pathlib import Path

from uyam import __version__
from uyam.dedup import DedupDatabase
from uyam.models import CollectionContext
from uyam.sources.base import CollectionRequest, RedditSource
from uyam.storage import append_record, raw_jsonl_path, write_manifest

logger = logging.getLogger(__name__)


def _git_commit() -> str | None:
    """Return the current Git commit hash, or None if not in a Git repository."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass
    return None


def run_collection(
    source: RedditSource,
    request: CollectionRequest,
    *,
    data_dir: Path,
    db: DedupDatabase,
    source_type: str,
) -> CollectionContext:
    """Execute one collection pass for a single subreddit.

    Returns the completed CollectionContext (also written as a manifest).
    """
    run_id = request.collection_run_id
    started_at = datetime.now(UTC)

    ctx = CollectionContext(
        collection_run_id=run_id,
        source_type=source_type,
        started_at_utc=started_at,
        subreddit=request.subreddit,
        listing_type=request.listing_type,
        sort_method=request.sort,
        search_query=request.search_query,
        requested_limit=request.limit,
        collector_version=__version__,
        collector_git_commit=_git_commit(),
        sampling_strategy=request.sampling_strategy,  # type: ignore[arg-type]
        matched_query_or_keyword=request.matched_query_or_keyword,
    )

    logger.info(
        "collection_started",
        extra={
            "collection_run_id": run_id,
            "source": source_type,
            "subreddit": request.subreddit,
            "listing_type": request.listing_type,
        },
    )

    db.register_run(run_id, source_type, request.subreddit)
    jsonl_path = raw_jsonl_path(data_dir, request.subreddit)

    submissions_seen = 0
    submissions_stored = 0
    comments_seen = 0
    comments_stored = 0
    duplicates_skipped = 0
    validation_failures = 0

    try:
        for submission in source.iter_submissions(request):
            submissions_seen += 1

            if db.is_seen(submission.reddit_fullname):
                duplicates_skipped += 1
                logger.debug(
                    "duplicate_skipped",
                    extra={
                        "reddit_fullname": submission.reddit_fullname,
                        "record_type": "submission",
                        "collection_run_id": run_id,
                    },
                )
                continue

            append_record(jsonl_path, submission)
            stored = db.mark_seen(
                reddit_fullname=submission.reddit_fullname,
                record_type="submission",
                reddit_id=submission.reddit_id,
                subreddit=submission.subreddit,
                collection_run_id=run_id,
                jsonl_path=str(jsonl_path),
            )
            if stored:
                submissions_stored += 1
                logger.info(
                    "submission_stored",
                    extra={
                        "reddit_fullname": submission.reddit_fullname,
                        "subreddit": submission.subreddit,
                        "collection_run_id": run_id,
                    },
                )
            else:
                duplicates_skipped += 1

            if request.max_comments_per_submission == 0:
                continue

            for comment in source.iter_comments(submission, request):
                comments_seen += 1

                if db.is_seen(comment.reddit_fullname):
                    duplicates_skipped += 1
                    continue

                append_record(jsonl_path, comment)
                stored_comment = db.mark_seen(
                    reddit_fullname=comment.reddit_fullname,
                    record_type="comment",
                    reddit_id=comment.reddit_id,
                    subreddit=comment.subreddit,
                    collection_run_id=run_id,
                    jsonl_path=str(jsonl_path),
                )
                if stored_comment:
                    comments_stored += 1
                    logger.info(
                        "comment_stored",
                        extra={
                            "reddit_fullname": comment.reddit_fullname,
                            "submission_id": comment.submission_id,
                            "collection_run_id": run_id,
                        },
                    )
                else:
                    duplicates_skipped += 1

    except Exception as exc:
        ctx.errors.append(str(exc))
        logger.error(
            "collection_error",
            extra={"collection_run_id": run_id, "error": str(exc)},
        )
        raise

    finally:
        finished_at = datetime.now(UTC)

        # Update context with final counts
        ctx = ctx.model_copy(
            update={
                "finished_at_utc": finished_at,
                "actual_submissions_seen": submissions_seen,
                "actual_submissions_stored": submissions_stored,
                "comments_seen": comments_seen,
                "comments_stored": comments_stored,
                "duplicates_skipped": duplicates_skipped,
                "validation_failures": validation_failures,
            }
        )

        manifest_path = write_manifest(data_dir, ctx)
        db.finish_run(
            run_id,
            submissions_seen=submissions_seen,
            submissions_stored=submissions_stored,
            comments_seen=comments_seen,
            comments_stored=comments_stored,
            duplicates_skipped=duplicates_skipped,
            validation_failures=validation_failures,
            manifest_path=str(manifest_path),
        )

        logger.info(
            "collection_finished",
            extra={
                "collection_run_id": run_id,
                "submissions_stored": submissions_stored,
                "comments_stored": comments_stored,
                "duplicates_skipped": duplicates_skipped,
            },
        )

    return ctx


def make_collection_run_id() -> str:
    return str(uuid.uuid4())
