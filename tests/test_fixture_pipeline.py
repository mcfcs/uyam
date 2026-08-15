"""End-to-end pipeline tests using the fixture source."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from uyam.dedup import DedupDatabase
from uyam.pipeline import make_collection_run_id, run_collection
from uyam.privacy import reset_hmac_key_cache
from uyam.sources.base import CollectionRequest
from uyam.sources.fixture import FixtureRedditSource

FIXTURE_PATH = Path(__file__).parent.parent / "fixtures" / "sample_posts.json"
TEST_KEY = "test-hmac-key-pipeline"


@pytest.fixture(autouse=True)
def set_hmac_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTHOR_HMAC_KEY", TEST_KEY)
    reset_hmac_key_cache()
    yield
    reset_hmac_key_cache()


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    d = tmp_path / "data"
    d.mkdir()
    return d


@pytest.fixture
def db(tmp_path: Path) -> DedupDatabase:
    return DedupDatabase(tmp_path / "db" / "collection.sqlite3")


def _make_request(subreddit: str, run_id: str | None = None) -> CollectionRequest:
    return CollectionRequest(
        subreddit=subreddit,
        listing_type="new",
        collection_run_id=run_id or make_collection_run_id(),
    )


class TestFixturePipelineEndToEnd:
    def test_philippines_collection_stores_records(
        self, data_dir: Path, db: DedupDatabase
    ) -> None:
        source = FixtureRedditSource(FIXTURE_PATH)
        request = _make_request("Philippines")

        ctx = run_collection(
            source, request, data_dir=data_dir, db=db, source_type="fixture"
        )

        assert ctx.actual_submissions_stored > 0
        assert ctx.comments_stored > 0
        assert ctx.duplicates_skipped == 0

    def test_jsonl_written(self, data_dir: Path, db: DedupDatabase) -> None:
        source = FixtureRedditSource(FIXTURE_PATH)
        request = _make_request("Philippines")
        run_collection(source, request, data_dir=data_dir, db=db, source_type="fixture")

        jsonl_files = list((data_dir / "raw" / "Philippines").glob("*.jsonl"))
        assert len(jsonl_files) == 1

        lines = jsonl_files[0].read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) > 0
        for line in lines:
            record = json.loads(line)
            assert "reddit_fullname" in record
            assert "schema_version" in record
            assert "collection_run_id" in record
            assert record["record_type"] in ("submission", "comment")

    def test_manifest_written(self, data_dir: Path, db: DedupDatabase) -> None:
        source = FixtureRedditSource(FIXTURE_PATH)
        request = _make_request("Philippines")
        ctx = run_collection(
            source, request, data_dir=data_dir, db=db, source_type="fixture"
        )

        manifest_path = data_dir / "manifests" / f"{ctx.collection_run_id}.json"
        assert manifest_path.exists()
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert manifest["collection_run_id"] == ctx.collection_run_id
        assert manifest["source_type"] == "fixture"
        assert manifest["subreddit"] == "Philippines"

    def test_no_raw_usernames_in_jsonl(self, data_dir: Path, db: DedupDatabase) -> None:
        source = FixtureRedditSource(FIXTURE_PATH)
        request = _make_request("Philippines")
        run_collection(source, request, data_dir=data_dir, db=db, source_type="fixture")

        jsonl_files = list((data_dir / "raw" / "Philippines").glob("*.jsonl"))
        content = jsonl_files[0].read_text(encoding="utf-8")

        # None of the placeholder usernames should appear verbatim
        for i in range(1, 30):
            assert f"placeholder_user_{i}" not in content

    def test_all_subreddits_collect(self, data_dir: Path, db: DedupDatabase) -> None:
        source = FixtureRedditSource(FIXTURE_PATH)
        for subreddit in ("Philippines", "CasualPH", "OffMyChestPH"):
            request = _make_request(subreddit)
            ctx = run_collection(
                source, request, data_dir=data_dir, db=db, source_type="fixture"
            )
            assert ctx.actual_submissions_stored > 0


class TestPrawMapping:
    """Verify PrawRedditSource._map_submission and _map_comment with mocked objects.

    No network calls are made.
    """

    def test_map_submission_from_mock(self) -> None:
        from unittest.mock import MagicMock

        from uyam.sources.praw_source import PrawRedditSource

        mock_sub = MagicMock()
        mock_sub.id = "abc123"
        mock_sub.name = "t3_abc123"
        mock_sub.subreddit.display_name = "Philippines"
        mock_sub.title = "Test title"
        mock_sub.selftext = "Test body"
        mock_sub.created_utc = 1752537600.0
        mock_sub.score = 42
        mock_sub.upvote_ratio = 0.89
        mock_sub.num_comments = 5
        mock_sub.permalink = "/r/Philippines/comments/abc123/test/"
        mock_sub.url = "https://www.reddit.com/r/Philippines/comments/abc123/test/"
        mock_sub.is_self = True
        mock_sub.over_18 = False
        mock_sub.spoiler = False
        mock_sub.stickied = False
        mock_sub.locked = False
        mock_sub.archived = False
        mock_sub.distinguished = None
        mock_sub.link_flair_text = None
        mock_sub.is_original_content = False
        mock_sub.num_crossposts = 0
        mock_sub.gilded = 0
        mock_sub.author = MagicMock()
        mock_sub.author.name = "real_username_never_stored"

        record = PrawRedditSource._map_submission(
            mock_sub, collection_run_id="run-test"
        )

        assert record.reddit_id == "abc123"
        assert record.reddit_fullname == "t3_abc123"
        assert record.subreddit == "Philippines"
        assert record.title == "Test title"
        assert record.score == 42
        assert record.author_status == "pseudonymized"
        assert record.author_hash is not None
        # Raw username must not appear in the record
        assert "real_username_never_stored" not in record.model_dump_json()

    def test_map_comment_from_mock(self) -> None:
        from unittest.mock import MagicMock

        from uyam.sources.praw_source import PrawRedditSource

        mock_com = MagicMock()
        mock_com.id = "xyz789"
        mock_com.name = "t1_xyz789"
        mock_com.link_id = "t3_abc123"
        mock_com.parent_id = "t3_abc123"
        mock_com.subreddit.display_name = "Philippines"
        mock_com.body = "Comment body"
        mock_com.created_utc = 1752538000.0
        mock_com.score = 10
        mock_com.depth = 0
        mock_com.is_submitter = False
        mock_com.distinguished = None
        mock_com.stickied = False
        mock_com.gilded = 0
        mock_com.controversiality = 0
        mock_com.permalink = "/r/Philippines/comments/abc123/test/xyz789/"
        mock_com.author = MagicMock()
        mock_com.author.name = "commenter_username_secret"

        record = PrawRedditSource._map_comment(
            mock_com, collection_run_id="run-test"
        )

        assert record.reddit_id == "xyz789"
        assert record.reddit_fullname == "t1_xyz789"
        assert record.submission_id == "abc123"
        assert record.parent_record_type == "submission"
        assert record.depth == 0
        assert record.author_status == "pseudonymized"
        assert "commenter_username_secret" not in record.model_dump_json()

    def test_deleted_author_on_mock_submission(self) -> None:
        from unittest.mock import MagicMock

        from uyam.sources.praw_source import PrawRedditSource

        mock_sub = MagicMock()
        mock_sub.id = "del001"
        mock_sub.name = "t3_del001"
        mock_sub.subreddit.display_name = "Philippines"
        mock_sub.title = "Deleted author post"
        mock_sub.selftext = ""
        mock_sub.created_utc = 1752537600.0
        mock_sub.score = 1
        mock_sub.upvote_ratio = 0.5
        mock_sub.num_comments = 0
        mock_sub.permalink = "/r/Philippines/comments/del001/deleted/"
        mock_sub.url = "https://reddit.com/r/Philippines/comments/del001/deleted/"
        mock_sub.is_self = True
        mock_sub.over_18 = False
        mock_sub.spoiler = False
        mock_sub.stickied = False
        mock_sub.locked = False
        mock_sub.archived = False
        mock_sub.distinguished = None
        mock_sub.link_flair_text = None
        mock_sub.is_original_content = False
        mock_sub.num_crossposts = 0
        mock_sub.gilded = 0
        mock_sub.author = None  # deleted

        record = PrawRedditSource._map_submission(
            mock_sub, collection_run_id="run-test"
        )
        assert record.author_hash is None
        assert record.author_status == "unavailable"
