"""Export: row shape, dataset card, and the privacy whitelist (no raw authors)."""

from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path
from typing import Any

from tests.annotate_fixtures import make_config, write_raw_corpus
from tests.test_annotate_runner import FakeClient, make_output
from uyam.annotate.aggregate import run_aggregation
from uyam.annotate.candidates import select_candidates
from uyam.annotate.corpus import index_corpus
from uyam.annotate.db import AnnotationDatabase
from uyam.annotate.export import run_export
from uyam.annotate.prompts import PROMPT_VERSION
from uyam.annotate.review import sample_gold_subset
from uyam.annotate.runner import run_annotator


def assert_no_author_key(obj: Any) -> None:
    if isinstance(obj, dict):
        for key, value in obj.items():
            assert key != "author", "raw author key leaked into an export artifact"
            assert_no_author_key(value)
    elif isinstance(obj, list):
        for value in obj:
            assert_no_author_key(value)


def _run_pipeline(tmp_path: Path) -> Any:
    cfg = make_config(tmp_path)
    write_raw_corpus(cfg.data_dir)
    with AnnotationDatabase(cfg.db_path) as db:
        index_corpus(db, cfg.data_dir)
        select_candidates(db, cfg.candidate_filters)
    for key in ("a1", "a2", "a3"):
        run_annotator(cfg, key, client_factory=lambda c, a: FakeClient())
    run_aggregation(cfg, PROMPT_VERSION)
    return cfg


def test_export_rows_and_card(tmp_path: Path) -> None:
    cfg = _run_pipeline(tmp_path)
    result = run_export(cfg, PROMPT_VERSION, dataset_version="vtest", formats=("jsonl",))

    rows = [
        json.loads(line)
        for line in Path(result["jsonl"]).read_text(encoding="utf-8").splitlines()
    ]
    assert result["rows"] == len(rows) > 0

    row = next(r for r in rows if r["record_type"] == "comment")
    assert row["labels"]["sarcastic"] is False
    assert row["labels"]["language"] == "taglish"
    assert row["reliability"]["resolved_by"] == "unanimous"
    assert row["reliability"]["sarcasm_votes"] == "3-0"
    assert len(row["reliability"]["annotators"]) == 3
    assert row["context"] is not None
    assert row["author_hash"].startswith("hash_")
    assert row["provenance"]["prompt_version"] == PROMPT_VERSION

    card = json.loads(Path(result["card"]).read_text(encoding="utf-8"))
    assert card["dataset_version"] == "vtest"
    assert card["counts"]["exported_rows"] == len(rows)
    assert "sarcastic" in card["label_distributions"]
    assert card["agreement"]["labels"]["sarcastic"]["raters"] == ["a1", "a2", "a3"]


def test_export_never_leaks_raw_authors(tmp_path: Path) -> None:
    cfg = _run_pipeline(tmp_path)
    result = run_export(cfg, PROMPT_VERSION, dataset_version="vtest", formats=("jsonl",))

    for artifact in (result["jsonl"], result["corpus"]):
        text = Path(artifact).read_text(encoding="utf-8")
        assert "real_name" not in text  # fixture usernames all contain this marker
        for line in text.splitlines():
            assert_no_author_key(json.loads(line))
    assert_no_author_key(json.loads(Path(result["card"]).read_text(encoding="utf-8")))


def test_export_skips_unresolved_by_default(tmp_path: Path) -> None:
    cfg = make_config(tmp_path)
    write_raw_corpus(cfg.data_dir)
    with AnnotationDatabase(cfg.db_path) as db:
        index_corpus(db, cfg.data_dir)
        select_candidates(db, cfg.candidate_filters)

    # Two annotators disagree on sarcasm -> escalated, unresolved.
    run_annotator(
        cfg,
        "a1",
        client_factory=lambda c, a: FakeClient(lambda ann, p: make_output(sarcastic=True)),
    )
    run_annotator(
        cfg,
        "a2",
        client_factory=lambda c, a: FakeClient(lambda ann, p: make_output(sarcastic=False)),
    )
    run_aggregation(cfg, PROMPT_VERSION)

    result = run_export(cfg, PROMPT_VERSION, dataset_version="vtest", formats=("jsonl",))
    assert result["rows"] == 0
    assert result["skipped_unresolved"] > 0

    included = run_export(
        cfg,
        PROMPT_VERSION,
        dataset_version="vtest2",
        formats=("jsonl",),
        include_unresolved=True,
    )
    assert included["rows"] > 0


def test_gold_sample_fills_review_queue(tmp_path: Path) -> None:
    cfg = _run_pipeline(tmp_path)
    stats = sample_gold_subset(cfg, PROMPT_VERSION, size=3, seed=7)
    assert stats["sampled"] == 3
    with AnnotationDatabase(cfg.db_path) as db:
        queue = db.review_queue_items()
        assert len([q for q in queue if q["reason"] == "gold"]) == 3
    # Re-running tops up to the target only: a full queue gets nothing new.
    stats2 = sample_gold_subset(cfg, PROMPT_VERSION, size=3, seed=7)
    assert stats2["sampled"] == 0 and stats2["kept"] == 3
    with AnnotationDatabase(cfg.db_path) as db:
        assert len([q for q in db.review_queue_items() if q["reason"] == "gold"]) == 3


def test_export_bundle_zip(tmp_path: Path) -> None:
    cfg = _run_pipeline(tmp_path)
    result = run_export(cfg, PROMPT_VERSION, dataset_version="vtest", formats=("jsonl",))

    zip_path = Path(result["zip"])
    assert zip_path.exists()
    with zipfile.ZipFile(zip_path) as zf:
        names = set(zf.namelist())
        assert {
            "dataset-vtest.jsonl",
            "corpus-vtest.jsonl",
            "dataset_card.json",
            "SHA256SUMS.txt",
        } <= names
        assert not any(n.endswith(".tmp") for n in names)
        sums = zf.read("SHA256SUMS.txt").decode("utf-8").splitlines()
        digests = {line.split("  ")[1]: line.split("  ")[0] for line in sums}
        for name, digest in digests.items():
            assert digest == hashlib.sha256(zf.read(name)).hexdigest()
        # The bundle carries the same bytes leische would read off disk.
        assert zf.read("dataset-vtest.jsonl") == Path(result["jsonl"]).read_bytes()


def test_export_bundle_can_be_skipped(tmp_path: Path) -> None:
    cfg = _run_pipeline(tmp_path)
    result = run_export(
        cfg, PROMPT_VERSION, dataset_version="vtest", formats=("jsonl",), bundle=False
    )
    assert result["zip"] is None
    assert not (cfg.export.out_dir / "uyam-dataset-vtest.zip").exists()


def test_export_skips_items_no_longer_eligible(tmp_path: Path) -> None:
    cfg = _run_pipeline(tmp_path)
    with AnnotationDatabase(cfg.db_path) as db:
        db.conn.execute(
            "UPDATE candidates SET eligible = 0, exclusion_reason = 'phatic' "
            "WHERE reddit_fullname = 't1_c1'"
        )
        db.commit()
    result = run_export(cfg, PROMPT_VERSION, dataset_version="vtest", formats=("jsonl",))

    fullnames = {
        json.loads(line)["reddit_fullname"]
        for line in Path(result["jsonl"]).read_text(encoding="utf-8").splitlines()
    }
    assert "t1_c1" not in fullnames
    assert result["skipped_ineligible"] == 1
    card = json.loads(Path(result["card"]).read_text(encoding="utf-8"))
    assert card["counts"]["skipped_ineligible"] == 1
    assert card["counts"]["candidates"]["phatic"] == 1
    assert card["candidate_filters"]["exclude_phatic"] is True


# ---------------------------------------------------------------------------
# v2 export additions: U1 majority label, U9 phatic flag, review fields,
# coverage by window, corpus scope
# ---------------------------------------------------------------------------


def test_majority_label_ties_fall_back_to_the_shipped_label() -> None:
    from uyam.annotate.export import majority_label

    assert majority_label([True, True, False], False) == (True, 0.6667)
    assert majority_label([False, False, True], True) == (False, 0.3333)
    assert majority_label([True, False], False) == (False, 0.5)  # tie -> shipped
    assert majority_label([True, False], True) == (True, 0.5)
    assert majority_label([], True) == (True, None)


def _rows(path: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines()]


def _run_pipeline_with(tmp_path: Path, extra: list[dict[str, Any]]) -> Any:
    """_run_pipeline plus extra raw records (written before indexing)."""
    cfg = make_config(tmp_path)
    write_raw_corpus(cfg.data_dir)
    out = cfg.data_dir / "raw" / "CasualPH" / "2026-08-19.jsonl"
    out.write_text("".join(json.dumps(r) + "\n" for r in extra), encoding="utf-8")
    with AnnotationDatabase(cfg.db_path) as db:
        index_corpus(db, cfg.data_dir)
        select_candidates(db, cfg.candidate_filters)
    for key in ("a1", "a2", "a3"):
        run_annotator(cfg, key, client_factory=lambda c, a: FakeClient())
    run_aggregation(cfg, PROMPT_VERSION)
    return cfg


def test_export_ships_majority_flags_and_review_fields(tmp_path: Path) -> None:
    cfg = _run_pipeline(tmp_path)
    with AnnotationDatabase(cfg.db_path) as db:
        db.conn.execute("UPDATE candidates SET phatic = 1 WHERE reddit_fullname = 't1_c2'")
        # t1_c1: annotators vote 2-1 sarcastic, the adjudicator ships "no".
        # t1_c2: unanimous sarcastic, shipped "yes". Everything else: 3-0 "no".
        db.conn.execute(
            "UPDATE llm_annotations SET sarcastic = 1 WHERE role = 'annotator' AND "
            "((reddit_fullname = 't1_c1' AND model_key IN ('a1', 'a2')) "
            "OR reddit_fullname = 't1_c2')"
        )
        db.conn.execute(
            "UPDATE aggregates SET sarcastic_final = 0, resolved_by = 'adjudicator' "
            "WHERE reddit_fullname = 't1_c1'"
        )
        db.conn.execute(
            "UPDATE aggregates SET sarcastic_final = 1 WHERE reddit_fullname = 't1_c2'"
        )
        first_pass = {
            "sarcastic": 0,
            "language": "taglish",
            "literal_sentiment": "neutral",
            "intended_sentiment": "neutral",
            "cue_polarity_inversion": 0,
            "cue_rhetorical_intent": 1,
            "cue_contextual_incongruity": 0,
            "cue_hyperbole": 0,
            "sarcastic_span": None,
            "unsure": 0,
        }
        db.upsert_human_review(
            {
                "reddit_fullname": "t1_c1",
                "sarcastic": 1,
                "is_gold": 1,
                "sarcastic_span": "Ang galing mo naman",
                "unsure": 1,
                "first_pass_json": json.dumps(first_pass),
                "assist_seen": 1,
                "votes_seen": 1,
            }
        )
        db.commit()
    result = run_export(cfg, PROMPT_VERSION, dataset_version="vtest", formats=("jsonl",))
    rows = {r["reddit_fullname"]: r for r in _rows(result["jsonl"])}

    c1 = rows["t1_c1"]
    assert c1["labels"]["sarcastic"] is False
    assert c1["labels"]["sarcastic_majority"] is True
    assert c1["labels"]["sarcasm_vote_share"] == 0.6667
    assert rows["t1_c2"]["labels"]["sarcastic_majority"] is True
    assert rows["t1_c3"]["labels"]["sarcastic_majority"] is False
    assert rows["t1_c2"]["flags"] == {"phatic": True}
    assert c1["flags"] == {"phatic": False}
    gold = c1["human_gold"]
    assert gold["sarcastic"] is True and gold["sarcastic_span"] == "Ang galing mo naman"
    assert gold["unsure"] is True and gold["assist_seen"] is True and gold["votes_seen"] is True
    # first_pass has the same shape as the top-level human labels (bools, cues dict).
    assert gold["first_pass"] == {
        "sarcastic": False,
        "language": "taglish",
        "literal_sentiment": "neutral",
        "intended_sentiment": "neutral",
        "cues": {
            "polarity_inversion": False,
            "rhetorical_intent": True,
            "contextual_incongruity": False,
            "hyperbole": False,
        },
        "sarcastic_span": None,
        "unsure": False,
    }
    extra_keys = {"notes", "first_pass", "assist_seen", "votes_seen", "assist_before_first_pass"}
    assert gold["assist_before_first_pass"] is False
    assert set(gold) == set(gold["first_pass"]) | extra_keys

    card = json.loads(Path(result["card"]).read_text(encoding="utf-8"))
    assert card["counts"]["phatic_flagged_rows"] == 1
    sets = card["label_sets"]
    n = len(rows)
    assert sets["n"] == n and sets["rows_that_differ"] == 1
    assert (sets["shipped_positives"], sets["majority_positives"]) == (1, 2)
    p_o = (n - 1) / n
    p_e = (1 / n) * (2 / n) + (1 - 1 / n) * (1 - 2 / n)
    assert sets["cohen_kappa_shipped_vs_majority"] == round((p_o - p_e) / (1 - p_e), 4)
    cov = card["temporal_context_coverage"]
    assert set(cov["by_window"]) == {"48h", "7d", "30d", "unbounded"}
    assert cov["share_with_at_least_1"] == cov["by_window"]["48h"]["share_with_at_least_1"]
    # votes_seen keeps t1_c1 out of the blind gold kappa.
    gold_kappa = card["agreement"]["gold_vs_ensemble_cohen_kappa"]
    assert gold_kappa["n_gold_items"] == 0 and gold_kappa["n_excluded_votes_seen"] == 1


def test_corpus_dump_scope(tmp_path: Path) -> None:
    import pytest

    from tests.annotate_fixtures import _comment

    # Too short to be a candidate, so never exported: one by a labeled author
    # (the author of t1_c1), one by an author with no exported row.
    same_author = _comment("old1", "sub1", "t3_sub1", 0, body="ok", author_hash="hash_c1")
    stranger = _comment("old2", "sub1", "t3_sub1", 0, body="ok", author_hash="hash_nobody")
    cfg = _run_pipeline_with(tmp_path, [same_author, stranger])
    labeled = run_export(cfg, PROMPT_VERSION, dataset_version="va", formats=("jsonl",))
    everything = run_export(
        cfg, PROMPT_VERSION, dataset_version="vb", formats=("jsonl",), corpus_scope="all"
    )
    with AnnotationDatabase(cfg.db_path) as db:
        total = db.corpus_counts()["total"]
    exported = {r["reddit_fullname"] for r in _rows(labeled["jsonl"])}
    assert "t1_c1" in exported and not {"t1_old1", "t1_old2"} & exported

    dumped = {r["reddit_fullname"] for r in _rows(labeled["corpus"])}
    assert labeled["corpus_rows"] == len(dumped)
    assert exported <= dumped
    assert "t1_old1" in dumped  # history of a labeled author
    assert "t1_old2" not in dumped  # nobody labeled wrote it
    assert everything["corpus_rows"] == total
    assert {"t1_old1", "t1_old2"} <= {r["reddit_fullname"] for r in _rows(everything["corpus"])}

    # dataset_card.json is the latest export; each version keeps its own copy.
    card = json.loads(Path(labeled["card_versioned"]).read_text(encoding="utf-8"))
    assert card["corpus_dump_scope"] == "labeled_authors"
    latest = json.loads(Path(everything["card"]).read_text(encoding="utf-8"))
    assert latest["dataset_version"] == "vb" and latest["corpus_dump_scope"] == "all"
    with pytest.raises(ValueError):
        run_export(cfg, PROMPT_VERSION, dataset_version="vc", corpus_scope="some")


def test_coverage_by_window_counts_exact_history(tmp_path: Path) -> None:
    from tests.annotate_fixtures import _comment
    from uyam.annotate.export import temporal_context_coverage

    # One earlier post per target author, at a different distance each:
    # c1 (11:00) 2 h before, c2 (11:01) 3 d, c3 (11:02) 20 d, c4 (11:05) 60 d.
    history = [
        _comment("h1", "sub1", "t3_sub1", 0, author_hash="hash_c1",
                 created_utc="2026-08-20T09:00:00Z"),
        _comment("h2", "sub1", "t3_sub1", 0, author_hash="hash_c2",
                 created_utc="2026-08-17T11:01:00Z"),
        _comment("h3", "sub1", "t3_sub1", 0, author_hash="hash_c3",
                 created_utc="2026-07-31T11:02:00Z"),
        _comment("h4", "sub1", "t3_sub1", 0, author_hash="hash_c4",
                 created_utc="2026-06-21T11:05:00Z"),
    ]
    cfg = make_config(tmp_path)
    write_raw_corpus(cfg.data_dir)
    out = cfg.data_dir / "raw" / "CasualPH" / "2026-06-21.jsonl"
    out.write_text("".join(json.dumps(r) + "\n" for r in history), encoding="utf-8")
    with AnnotationDatabase(cfg.db_path) as db:
        index_corpus(db, cfg.data_dir)
        cov = temporal_context_coverage(db, ["t1_c1", "t1_c2", "t1_c3", "t1_c4"])
    shares = {w: cov["by_window"][w]["share_with_at_least_1"] for w in cov["by_window"]}
    assert shares == {"48h": 0.25, "7d": 0.5, "30d": 0.75, "unbounded": 1.0}
    assert cov["share_with_at_least_1"] == 0.25  # the thesis rule is the 48 h window


def test_single_class_gold_gives_null_kappa_and_strict_json(tmp_path: Path) -> None:
    cfg = _run_pipeline(tmp_path)  # every shipped label is "not sarcastic"
    with AnnotationDatabase(cfg.db_path) as db:
        for fullname in ("t1_c1", "t1_c2", "t1_c3"):
            db.upsert_human_review({"reddit_fullname": fullname, "sarcastic": 0, "is_gold": 1})
    result = run_export(cfg, PROMPT_VERSION, dataset_version="vtest", formats=("jsonl",))

    def reject(token: str) -> None:
        raise AssertionError(f"non-standard JSON constant {token} in the card")

    card = json.loads(Path(result["card"]).read_text(encoding="utf-8"), parse_constant=reject)
    gold = card["agreement"]["gold_vs_ensemble_cohen_kappa"]
    assert gold["n_gold_items"] == 3
    assert gold["sarcastic"] is None


def test_latest_bundle_picks_the_newest_zip(tmp_path: Path) -> None:
    import os

    from uyam.annotate.export import latest_bundle

    assert latest_bundle(tmp_path) is None
    old = tmp_path / "uyam-dataset-v1.zip"
    new = tmp_path / "uyam-dataset-v2.zip"
    for path, mtime in ((old, 1_000_000), (new, 2_000_000)):
        path.write_bytes(b"zip")
        os.utime(path, (mtime, mtime))
    (tmp_path / "uyam-dataset-v3.zip.tmp").write_bytes(b"partial")  # never offered
    assert latest_bundle(tmp_path) == new


def test_list_bundles_and_bundle_info_for_the_download_picker(tmp_path: Path) -> None:
    import os
    import zipfile

    from uyam.annotate.export import bundle_info, list_bundles

    assert list_bundles(tmp_path) == []
    specs = (("v2", 1_000_000, 15000, 1601), ("v4", 3_000_000, 5000, 1104))
    for version, mtime, rows, positives in specs:
        path = tmp_path / f"uyam-dataset-{version}.zip"
        card = {"counts": {"exported_rows": rows}, "readiness": {"sarcastic_positives": positives}}
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr(f"dataset_card-{version}.json", json.dumps(card))
            zf.writestr("dataset_card.json", json.dumps({}))  # the unversioned copy is ignored
        os.utime(path, (mtime, mtime))
    broken = tmp_path / "uyam-dataset-v3.zip"
    broken.write_bytes(b"not a zip")
    os.utime(broken, (2_000_000, 2_000_000))
    (tmp_path / "uyam-dataset-v5.zip.tmp").write_bytes(b"partial")  # never offered

    assert [p.name for p in list_bundles(tmp_path)] == [
        "uyam-dataset-v4.zip", "uyam-dataset-v3.zip", "uyam-dataset-v2.zip"
    ]
    v4 = bundle_info(tmp_path / "uyam-dataset-v4.zip")
    assert (v4["version"], v4["rows"], v4["sarcastic"]) == ("v4", 5000, 1104)
    v3 = bundle_info(broken)  # still listed, just without counts
    assert v3["version"] == "v3" and v3["rows"] is None and v3["sarcastic"] is None

