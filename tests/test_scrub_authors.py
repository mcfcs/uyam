"""Plaintext usernames never persist: scrub of legacy JSONL + hash-based bot detection."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.annotate_fixtures import make_config, write_raw_corpus
from uyam.annotate.corpus import index_corpus
from uyam.annotate.db import AnnotationDatabase
from uyam.privacy import pseudonymize_author, reset_hmac_key_cache
from uyam.storage import load_jsonl_records, scrub_author_field

TEST_KEY = "scrub-test-key"


@pytest.fixture
def hmac_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTHOR_HMAC_KEY", TEST_KEY)
    reset_hmac_key_cache()
    yield
    reset_hmac_key_cache()


def test_scrub_removes_author_keeps_hash_and_is_idempotent(tmp_path: Path) -> None:
    raw = tmp_path / "raw" / "CasualPH"
    raw.mkdir(parents=True)
    legacy = raw / "2026-08-01.jsonl"
    legacy.write_text(
        json.dumps({"reddit_fullname": "t3_a", "author": "RealName", "author_hash": "h1"})
        + "\n\n"
        + json.dumps({"reddit_fullname": "t1_b", "author": None, "author_hash": None})
        + "\n"
        + '{"reddit_fullname": "t1_partial", "author": "Cut'  # crash-window partial line
        + "\n",
        encoding="utf-8",
    )
    clean = raw / "2026-09-08.jsonl"
    clean_line = json.dumps({"reddit_fullname": "t3_c", "author_hash": "h3"})
    clean.write_text(clean_line + "\n", encoding="utf-8")

    stats = scrub_author_field(tmp_path)
    assert stats == {"files": 2, "files_rewritten": 1, "records_scrubbed": 2}

    text = legacy.read_text(encoding="utf-8")
    assert '"author":' not in text.split("t1_partial")[0]
    assert "RealName" not in text.split("t1_partial")[0]
    assert '"author_hash": "h1"' in text
    assert '{"reddit_fullname": "t1_partial", "author": "Cut' in text  # untouched
    assert clean.read_text(encoding="utf-8") == clean_line + "\n"

    again = scrub_author_field(tmp_path)
    assert again == {"files": 2, "files_rewritten": 0, "records_scrubbed": 0}

    records = load_jsonl_records(tmp_path)
    assert [r["reddit_fullname"] for r in records] == ["t3_a", "t1_b", "t3_c"]
    assert all("author" not in r for r in records)


def test_index_flags_bots_by_hash_without_plaintext(tmp_path: Path, hmac_key: None) -> None:
    cfg = make_config(tmp_path)
    write_raw_corpus(cfg.data_dir)
    # Re-hash the fixture corpus with the test key, then drop every plaintext author.
    raw_file = next((cfg.data_dir / "raw").glob("**/*.jsonl"))
    rows = [json.loads(line) for line in raw_file.read_text(encoding="utf-8").splitlines()]
    for row in rows:
        row["author_hash"] = pseudonymize_author(row.get("author"))[0]
    raw_file.write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8"
    )
    assert scrub_author_field(cfg.data_dir)["records_scrubbed"] == len(rows)
    assert "AutoModerator" not in raw_file.read_text(encoding="utf-8")

    with AnnotationDatabase(cfg.db_path) as db:
        index_corpus(db, cfg.data_dir, extra_bot_authors=cfg.candidate_filters.exclude_authors)
        bot = db.get_record("t1_bot1")
        human = db.get_record("t1_c1")
    assert bot is not None and bot["is_bot"] == 1
    assert human is not None and human["is_bot"] == 0
