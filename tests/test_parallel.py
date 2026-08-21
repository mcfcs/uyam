"""Parallel collection jobs using the fixture source (no browser)."""

from __future__ import annotations

from pathlib import Path

import pytest

from uyam.pipeline import make_collection_run_id
from uyam.privacy import reset_hmac_key_cache
from uyam.storage import load_jsonl_records

TEST_KEY = "test-hmac-key-parallel"


@pytest.fixture(autouse=True)
def set_hmac_key(monkeypatch: pytest.MonkeyPatch) -> None:
    from uyam.scrape_status import clear_status, set_control

    monkeypatch.setenv("AUTHOR_HMAC_KEY", TEST_KEY)
    reset_hmac_key_cache()
    clear_status()
    set_control("run")
    yield
    reset_hmac_key_cache()
    clear_status()


def _job(data_dir: Path, subreddit: str, index: int) -> dict[str, object]:
    return {
        "source": "fixture",
        "source_type": "fixture",
        "subreddit": subreddit,
        "listing_type": "new",
        "collection_run_id": make_collection_run_id(),
        "limit": 10,
        "sort": None,
        "time_filter": "all",
        "search_query": None,
        "max_comments_per_submission": 100,
        "max_depth": None,
        "include_deleted": False,
        "comment_sort": "confidence",
        "replace_more_limit": 32,
        "sampling_strategy": "natural",
        "matched_query_or_keyword": None,
        "max_seconds": None,
        "data_dir": str(data_dir),
        "log_level": "WARNING",
        "lenient": False,
        "worker_index": index,
        "author_hmac_key": TEST_KEY,
    }


def test_collect_one_job_fixture(tmp_path: Path) -> None:
    from uyam.parallel import collect_one_job

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    row = collect_one_job(_job(data_dir, "Philippines", 0))
    assert row["ok"] is True
    assert row["submissions"] > 0
    recs = load_jsonl_records(data_dir)
    assert any(r.get("collected_at") or r.get("retrieved_at_utc") for r in recs)


def test_run_jobs_all_three_subreddits(tmp_path: Path) -> None:
    from uyam.parallel import run_jobs

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    subs = ["Philippines", "CasualPH", "OffMyChestPH"]
    jobs = [_job(data_dir, sub, i) for i, sub in enumerate(subs)]
    results = run_jobs(jobs, max_workers=3)
    assert {r["subreddit"] for r in results} == set(subs)
    assert all(r["ok"] for r in results)
    recs = load_jsonl_records(data_dir)
    assert {r.get("subreddit") for r in recs} == set(subs)


def test_second_pass_skips_duplicates(tmp_path: Path) -> None:
    from uyam.parallel import collect_one_job

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    first = collect_one_job(_job(data_dir, "Philippines", 0))
    second = collect_one_job(_job(data_dir, "Philippines", 1))
    assert first["submissions"] > 0
    assert second["submissions"] == 0
    assert second["duplicates"] > 0
