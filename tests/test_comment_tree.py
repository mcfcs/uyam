"""Tests for comment tree relationship preservation through the pipeline."""

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
TEST_KEY = "test-hmac-key-tree"


@pytest.fixture(autouse=True)
def set_hmac_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTHOR_HMAC_KEY", TEST_KEY)
    reset_hmac_key_cache()
    yield
    reset_hmac_key_cache()


class TestCommentTreePreservation:
    """FAKE010 has a 4-level deep thread (depths 0-3). Verify relationships survive."""

    def _collect_philippines(self, tmp_path: Path) -> list[dict]:
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        db = DedupDatabase(tmp_path / "db" / "collection.sqlite3")
        source = FixtureRedditSource(FIXTURE_PATH)
        request = CollectionRequest(
            subreddit="Philippines",
            listing_type="new",
            collection_run_id=make_collection_run_id(),
        )
        run_collection(source, request, data_dir=data_dir, db=db, source_type="fixture")
        db.close()

        jsonl_files = list((data_dir / "raw" / "Philippines").glob("*.jsonl"))
        records = []
        for line in jsonl_files[0].read_text(encoding="utf-8").strip().splitlines():
            records.append(json.loads(line))
        return records

    def test_comment_depth_preserved(self, tmp_path: Path) -> None:
        records = self._collect_philippines(tmp_path)
        comments = [r for r in records if r["record_type"] == "comment"]

        # FAKE010 has comments at depths 0, 1, 2, 3
        depths = {c["depth"] for c in comments}
        assert 0 in depths
        assert 1 in depths
        assert 2 in depths
        assert 3 in depths

    def test_parent_id_preserved(self, tmp_path: Path) -> None:
        records = self._collect_philippines(tmp_path)
        comments = [r for r in records if r["record_type"] == "comment"]

        # Every comment should have a parent_id
        for comment in comments:
            assert "parent_id" in comment
            assert comment["parent_id"].startswith("t1_") or comment["parent_id"].startswith("t3_")

    def test_parent_record_type_correct(self, tmp_path: Path) -> None:
        records = self._collect_philippines(tmp_path)
        comments = [r for r in records if r["record_type"] == "comment"]

        for comment in comments:
            if comment["parent_id"].startswith("t3_"):
                assert comment["parent_record_type"] == "submission"
            else:
                assert comment["parent_record_type"] == "comment"

    def test_submission_id_links_to_submission(self, tmp_path: Path) -> None:
        records = self._collect_philippines(tmp_path)
        submissions = {r["reddit_id"] for r in records if r["record_type"] == "submission"}
        comments = [r for r in records if r["record_type"] == "comment"]

        for comment in comments:
            assert comment["submission_id"] in submissions

    def test_linear_chain_reconstruction(self, tmp_path: Path) -> None:
        """Verify the FAKE010 chain: FAKE010 -> COM015 -> COM016 -> COM017 -> COM018."""
        records = self._collect_philippines(tmp_path)

        by_fullname = {r["reddit_fullname"]: r for r in records}

        # depth-0 comment should have parent_id = t3_FAKE010
        com015 = by_fullname.get("t1_FAKECOM015")
        assert com015 is not None
        assert com015["parent_id"] == "t3_FAKE010"
        assert com015["depth"] == 0

        # depth-1 comment should have parent_id = t1_FAKECOM015
        com016 = by_fullname.get("t1_FAKECOM016")
        assert com016 is not None
        assert com016["parent_id"] == "t1_FAKECOM015"
        assert com016["depth"] == 1

        # depth-2 comment
        com017 = by_fullname.get("t1_FAKECOM017")
        assert com017 is not None
        assert com017["parent_id"] == "t1_FAKECOM016"
        assert com017["depth"] == 2

        # depth-3 comment
        com018 = by_fullname.get("t1_FAKECOM018")
        assert com018 is not None
        assert com018["parent_id"] == "t1_FAKECOM017"
        assert com018["depth"] == 3

    def test_is_submitter_preserved(self, tmp_path: Path) -> None:
        records = self._collect_philippines(tmp_path)
        # FAKE010/FAKECOM018 has is_submitter=True
        com018 = next(
            (r for r in records if r.get("reddit_fullname") == "t1_FAKECOM018"), None
        )
        assert com018 is not None
        assert com018["is_submitter"] is True
