"""Tests for author pseudonymization.

Verifies that raw usernames never appear in output and that the
HMAC-SHA256 pseudonymization behaves correctly.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path

import pytest

from uyam.privacy import pseudonymize_author, reset_hmac_key_cache

TEST_KEY = "test-hmac-key-for-unit-tests-only"
TEST_USERNAME = "example_user_thesis_test"


@pytest.fixture(autouse=True)
def set_hmac_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTHOR_HMAC_KEY", TEST_KEY)
    reset_hmac_key_cache()
    yield
    reset_hmac_key_cache()


class TestPseudonymizeAuthor:
    def test_returns_hex_digest(self) -> None:
        digest, status = pseudonymize_author(TEST_USERNAME)
        assert status == "pseudonymized"
        assert digest is not None
        assert len(digest) == 64  # SHA-256 hex = 64 chars
        assert all(c in "0123456789abcdef" for c in digest)

    def test_deterministic(self) -> None:
        d1, _ = pseudonymize_author(TEST_USERNAME)
        d2, _ = pseudonymize_author(TEST_USERNAME)
        assert d1 == d2

    def test_case_normalization(self) -> None:
        lower, _ = pseudonymize_author("example_user")
        upper, _ = pseudonymize_author("EXAMPLE_USER")
        mixed, _ = pseudonymize_author("Example_User")
        assert lower == upper == mixed

    def test_deleted_string(self) -> None:
        digest, status = pseudonymize_author("[deleted]")
        assert digest is None
        assert status == "deleted"

    def test_removed_string(self) -> None:
        digest, status = pseudonymize_author("[removed]")
        assert digest is None
        assert status == "deleted"

    def test_empty_string(self) -> None:
        digest, status = pseudonymize_author("")
        assert digest is None
        assert status == "deleted"

    def test_none_username(self) -> None:
        digest, status = pseudonymize_author(None)
        assert digest is None
        assert status == "unavailable"

    def test_different_users_differ(self) -> None:
        d1, _ = pseudonymize_author("user_alpha")
        d2, _ = pseudonymize_author("user_beta")
        assert d1 != d2

    def test_different_keys_differ(self, monkeypatch: pytest.MonkeyPatch) -> None:
        d1, _ = pseudonymize_author(TEST_USERNAME)

        monkeypatch.setenv("AUTHOR_HMAC_KEY", "different-key")
        reset_hmac_key_cache()
        d2, _ = pseudonymize_author(TEST_USERNAME)

        assert d1 != d2

    def test_missing_key_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("AUTHOR_HMAC_KEY", raising=False)
        reset_hmac_key_cache()
        with pytest.raises(RuntimeError, match="AUTHOR_HMAC_KEY"):
            pseudonymize_author(TEST_USERNAME)


class TestUsernameNeverExposedInOutput:
    """Asserts the raw username never appears in JSONL, SQLite, or logs."""

    def test_username_not_in_jsonl(self, tmp_path: Path) -> None:
        digest, _ = pseudonymize_author(TEST_USERNAME)
        record = {
            "record_type": "submission",
            "author_hash": digest,
            "author_status": "pseudonymized",
        }
        jsonl = tmp_path / "out.jsonl"
        jsonl.write_text(json.dumps(record) + "\n", encoding="utf-8")

        content = jsonl.read_text(encoding="utf-8")
        assert TEST_USERNAME not in content
        assert TEST_USERNAME.upper() not in content

    def test_username_not_in_sqlite(self, tmp_path: Path) -> None:
        digest, _ = pseudonymize_author(TEST_USERNAME)
        db = tmp_path / "test.sqlite3"
        conn = sqlite3.connect(db)
        conn.execute(
            "CREATE TABLE t (reddit_fullname TEXT, author_hash TEXT)"
        )
        conn.execute("INSERT INTO t VALUES (?, ?)", ("t3_abc123", digest))
        conn.commit()
        conn.close()

        raw = db.read_bytes()
        assert TEST_USERNAME.encode() not in raw

    def test_username_not_in_logs(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.DEBUG):
            pseudonymize_author(TEST_USERNAME)

        for record in caplog.records:
            assert TEST_USERNAME not in record.getMessage()
            assert TEST_USERNAME not in str(record.args)
