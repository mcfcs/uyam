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


def test_browser_profile_is_per_job(tmp_path: Path) -> None:
    from uyam.parallel import browser_profile_dir

    first = browser_profile_dir("Philippines", 0, root=tmp_path)
    second = browser_profile_dir("Philippines", 1, root=tmp_path)
    other = browser_profile_dir("CasualPH", 0, root=tmp_path)
    assert first != second
    assert first.name == "Philippines-0"
    assert second.name == "Philippines-1"
    assert other.name == "CasualPH-0"


def test_oversample_job_is_a_search_not_a_calendar_walk() -> None:
    from uyam.parallel import make_oversample_job

    base = {
        "subreddit": "Philippines",
        "listing_type": "new",
        "calendar_since": "2026-11-08",
        "calendar_until": "2026-11-10",
        "posts_per_day": 40,
        "sampling_strategy": "natural",
        "worker_index": 0,
        "limit": 100,
    }
    job = make_oversample_job(
        base,
        subreddit="CasualPH",
        keyword="edi wow",
        worker_index=4,
        collection_run_id="run-over",
    )
    assert job["subreddit"] == "CasualPH"
    assert job["listing_type"] == "search"
    assert job["search_query"] == "edi wow"
    assert job["sampling_strategy"] == "keyword_oversampled"
    assert job["matched_query_or_keyword"] == "edi wow"
    assert job["worker_index"] == 4
    assert job["collection_run_id"] == "run-over"
    assert job["calendar_since"] is None
    assert job["calendar_until"] is None
    assert job["posts_per_day"] is None
    assert job["limit"] == 100


def test_second_pass_skips_duplicates(tmp_path: Path) -> None:
    from uyam.parallel import collect_one_job

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    first = collect_one_job(_job(data_dir, "Philippines", 0))
    second = collect_one_job(_job(data_dir, "Philippines", 1))
    assert first["submissions"] > 0
    assert second["submissions"] == 0
    assert second["duplicates"] > 0
