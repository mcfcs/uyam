"""Fan-out collection: one process (and Chrome profile) per subreddit."""

from __future__ import annotations

import contextlib
import logging
import os
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from uyam.config import load_config, load_env
from uyam.dedup import DedupDatabase
from uyam.logging_config import configure_logging
from uyam.pipeline import run_collection
from uyam.scrape_status import (
    LOG_FILE,
    ScrapeStopRequested,
    register_worker_pid,
    unregister_worker_pid,
    write_status,
)
from uyam.sources.base import CollectionRequest

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _safe_profile_name(subreddit: str) -> str:
    cleaned = "".join(c for c in subreddit if c.isalnum() or c in "-_")
    return cleaned or "subreddit"


def collect_one_job(job: dict[str, Any]) -> dict[str, Any]:
    """Process entrypoint: collect one subreddit. Never raises to the parent."""
    sub = str(job.get("subreddit") or "")
    pid = os.getpid()
    register_worker_pid(pid)
    hmac = job.get("author_hmac_key")
    if hmac:
        os.environ["AUTHOR_HMAC_KEY"] = str(hmac)
    os.environ.pop("UYAM_LOG_TO_STDOUT_ONLY", None)
    log_level = str(job.get("log_level") or "INFO")
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        configure_logging(log_level, log_file=str(LOG_FILE))
    except OSError:
        configure_logging(log_level)

    load_env()
    if hmac:
        from uyam.privacy import reset_hmac_key_cache

        reset_hmac_key_cache()

    config_path = job.get("config_path")
    cfg = load_config(Path(config_path) if config_path else None)
    data_dir = Path(job["data_dir"])
    source_type = str(job.get("source_type") or job.get("source") or "fixture")
    reddit_source: Any = None
    db: DedupDatabase | None = None
    write_status("running", f"r/{sub} — collecting")

    result: dict[str, Any] = {
        "ok": False,
        "subreddit": sub,
        "submissions": 0,
        "comments": 0,
        "duplicates": 0,
        "errors": [],
        "pid": pid,
    }
    try:
        reddit_source = _build_source(job, cfg)
        db = DedupDatabase(data_dir / "db" / "collection.sqlite3")
        request = CollectionRequest(
            subreddit=sub,
            listing_type=str(job.get("listing_type") or "new"),
            collection_run_id=str(job["collection_run_id"]),
            limit=job.get("limit"),
            sort=job.get("sort"),
            time_filter=str(job.get("time_filter") or "all"),
            search_query=job.get("search_query"),
            max_comments_per_submission=job.get("max_comments_per_submission"),
            max_depth=job.get("max_depth"),
            include_deleted=bool(job.get("include_deleted")),
            comment_sort=str(job.get("comment_sort") or "confidence"),
            replace_more_limit=int(job.get("replace_more_limit") or 32),
            sampling_strategy=str(job.get("sampling_strategy") or "natural"),
            matched_query_or_keyword=job.get("matched_query_or_keyword"),
            max_seconds=job.get("max_seconds"),
            calendar_since=job.get("calendar_since"),
            calendar_until=job.get("calendar_until"),
            posts_per_day=job.get("posts_per_day"),
        )
        ctx = run_collection(
            reddit_source,
            request,
            data_dir=data_dir,
            db=db,
            source_type=source_type,
        )
        result.update(
            {
                "ok": True,
                "submissions": ctx.actual_submissions_stored,
                "comments": ctx.comments_stored,
                "duplicates": ctx.duplicates_skipped,
                "errors": list(ctx.errors),
            }
        )
        write_status(
            "running",
            f"r/{sub} — {ctx.actual_submissions_stored} posts, "
            f"{ctx.comments_stored} comments",
        )
    except ScrapeStopRequested as exc:
        result["ok"] = True
        result["errors"] = [getattr(exc, "reason", None) or "stopped_by_user"]
        logger.warning("worker_stopped", extra={"subreddit": sub, "reason": str(exc)})
    except Exception as exc:
        result["errors"] = [str(exc)]
        logger.error(
            "worker_failed",
            extra={"subreddit": sub, "error": str(exc)[:300]},
        )
    finally:
        close = getattr(reddit_source, "close", None) if reddit_source else None
        if callable(close):
            with contextlib.suppress(Exception):
                close()
        if db is not None:
            db.close()
        unregister_worker_pid(pid)
    return result


def _build_source(job: dict[str, Any], cfg: Any) -> Any:
    source = str(job.get("source") or "fixture")
    if source == "fixture":
        from uyam.sources.fixture import FixtureRedditSource

        return FixtureRedditSource(lenient_validation=bool(job.get("lenient")))
    if source in ("public", "shreddit"):
        from uyam.privacy import ensure_hmac_key
        from uyam.sources.proxy_pool import ProxyPool
        from uyam.sources.shreddit import ShredditBrowserSource

        ensure_hmac_key()
        proxies_path = Path(job["proxies_path"])
        pool = ProxyPool.from_file(proxies_path).rotated(int(job.get("worker_index") or 0))
        if pool.is_empty():
            raise RuntimeError(f"shreddit requires proxies in {proxies_path}")
        sub = str(job.get("subreddit") or "sub")
        profile_dir = (
            _REPO_ROOT / "data" / ".browser-profiles" / _safe_profile_name(sub)
        )
        headless = job.get("headless")
        captcha_wait = job.get("captcha_wait")
        return ShredditBrowserSource(
            proxy_pool=pool,
            headless=cfg.shreddit.headless if headless is None else bool(headless),
            timeout_seconds=cfg.shreddit.timeout_seconds,
            min_interval_seconds=cfg.shreddit.min_interval_seconds,
            max_scrolls=cfg.shreddit.max_scrolls,
            more_comments_clicks=cfg.shreddit.more_comments_clicks,
            scroll_wait_ms=cfg.shreddit.scroll_wait_ms,
            expand_wait_ms=cfg.shreddit.expand_wait_ms,
            use_system_chrome=cfg.shreddit.use_system_chrome,
            captcha_wait_seconds=(
                cfg.shreddit.captcha_wait_seconds
                if captcha_wait is None
                else float(captcha_wait)
            ),
            profile_dir=profile_dir,
        )

    from uyam.privacy import ensure_hmac_key
    from uyam.sources.praw_source import PrawRedditSource
    from uyam.sources.proxy_pool import ProxyPool

    ensure_hmac_key()
    pool = ProxyPool.from_file(Path(job["proxies_path"])).rotated(
        int(job.get("worker_index") or 0)
    )
    return PrawRedditSource(proxy_url=pool.current())


def run_jobs(jobs: list[dict[str, Any]], *, max_workers: int) -> list[dict[str, Any]]:
    """Run collection jobs. workers<=1 stays in-process; otherwise one process each."""
    if not jobs:
        return []
    workers = max(1, min(int(max_workers), len(jobs)))
    if workers == 1:
        return [collect_one_job(job) for job in jobs]

    logger.info(
        "parallel_collect_start",
        extra={"jobs": len(jobs), "workers": workers},
    )
    results: list[dict[str, Any] | None] = [None] * len(jobs)
    with ProcessPoolExecutor(max_workers=workers) as pool:
        future_map = {pool.submit(collect_one_job, job): i for i, job in enumerate(jobs)}
        for fut in as_completed(future_map):
            idx = future_map[fut]
            sub = str(jobs[idx].get("subreddit") or "")
            try:
                results[idx] = fut.result()
            except Exception as exc:
                results[idx] = {
                    "ok": False,
                    "subreddit": sub,
                    "submissions": 0,
                    "comments": 0,
                    "duplicates": 0,
                    "errors": [str(exc)],
                }
    return [r or {"ok": False, "subreddit": "", "errors": ["missing result"]} for r in results]
