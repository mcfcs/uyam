"""Tests for fixture validation (strict and lenient modes)."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from uyam.sources.fixture import (
    FixtureValidationError,
    validate_fixture,
)

FIXTURE_PATH = Path(__file__).parent.parent / "fixtures" / "sample_posts.json"


def _load_fixture() -> dict:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


class TestFixturePassesStrictValidation:
    def test_sample_posts_validates(self) -> None:
        data = _load_fixture()
        warnings = validate_fixture(data, lenient=False)
        assert warnings == []

    def test_returns_empty_warnings_list(self) -> None:
        data = _load_fixture()
        result = validate_fixture(data)
        assert isinstance(result, list)
        assert len(result) == 0


class TestStrictModeFailures:
    def _remove_field(self, field: str, from_submission: bool = True) -> dict:
        data = _load_fixture()
        target = data["submissions"][0]
        if from_submission:
            del target[field]
        else:
            del target["comments"][0][field]
        return data

    def test_missing_submission_title_fails(self) -> None:
        data = self._remove_field("title")
        with pytest.raises(FixtureValidationError, match="title"):
            validate_fixture(data)

    def test_missing_submission_id_fails(self) -> None:
        data = self._remove_field("id")
        with pytest.raises(FixtureValidationError, match="id"):
            validate_fixture(data)

    def test_missing_comment_body_fails(self) -> None:
        data = self._remove_field("body", from_submission=False)
        with pytest.raises(FixtureValidationError, match="body"):
            validate_fixture(data)

    def test_missing_comment_depth_fails(self) -> None:
        data = self._remove_field("depth", from_submission=False)
        with pytest.raises(FixtureValidationError, match="depth"):
            validate_fixture(data)

    def test_wrong_submission_fullname_prefix_fails(self) -> None:
        data = _load_fixture()
        data["submissions"][0]["name"] = "t1_FAKE001"  # wrong prefix
        with pytest.raises(FixtureValidationError, match="t3_"):
            validate_fixture(data)

    def test_wrong_comment_fullname_prefix_fails(self) -> None:
        data = _load_fixture()
        data["submissions"][0]["comments"][0]["name"] = "t3_FAKECOM001"
        with pytest.raises(FixtureValidationError, match="t1_"):
            validate_fixture(data)

    def test_duplicate_submission_fullname_fails(self) -> None:
        data = _load_fixture()
        dupe = copy.deepcopy(data["submissions"][0])
        dupe["id"] = "FAKE001_DUPE"
        data["submissions"].append(dupe)
        with pytest.raises(FixtureValidationError, match="duplicate"):
            validate_fixture(data)

    def test_duplicate_comment_fullname_fails(self) -> None:
        data = _load_fixture()
        dupe_comment = copy.deepcopy(data["submissions"][0]["comments"][0])
        data["submissions"][0]["comments"].append(dupe_comment)
        with pytest.raises(FixtureValidationError, match="duplicate"):
            validate_fixture(data)

    def test_subreddit_mismatch_in_comment_fails(self) -> None:
        data = _load_fixture()
        data["submissions"][0]["comments"][0]["subreddit"] = "WrongSubreddit"
        with pytest.raises(FixtureValidationError, match="subreddit"):
            validate_fixture(data)

    def test_wrong_parent_submission_id_fails(self) -> None:
        data = _load_fixture()
        # Change a top-level comment's parent_id to reference a different submission
        data["submissions"][0]["comments"][0]["parent_id"] = "t3_NOTFAKE001"
        with pytest.raises(FixtureValidationError, match="parent_id"):
            validate_fixture(data)

    def test_missing_submissions_key_fails(self) -> None:
        with pytest.raises(FixtureValidationError, match="submissions"):
            validate_fixture({"schema_version": "1.0"})

    def test_non_dict_root_fails(self) -> None:
        with pytest.raises(FixtureValidationError, match="root"):
            validate_fixture([])  # type: ignore[arg-type]


class TestLenientMode:
    def test_lenient_returns_warnings_instead_of_raising(self) -> None:
        data = _load_fixture()
        data["submissions"][0]["comments"][0]["subreddit"] = "WrongSubreddit"
        # Should not raise
        warnings = validate_fixture(data, lenient=True)
        assert len(warnings) > 0
        assert any("subreddit" in w for w in warnings)

    def test_lenient_missing_field_warning(self) -> None:
        data = _load_fixture()
        del data["submissions"][0]["title"]
        warnings = validate_fixture(data, lenient=True)
        assert any("title" in w for w in warnings)
