"""Re-tagging /new calendar-walk records that were stored as keyword_oversampled."""

from __future__ import annotations

import json
from pathlib import Path

from uyam.storage import retag_calendar_walk_records


def _rec(fullname: str, strategy: str, keyword: str | None, run: str) -> dict[str, object]:
    return {
        "record_type": "submission" if fullname.startswith("t3_") else "comment",
        "reddit_fullname": fullname,
        "collection_run_id": run,
        "sampling_strategy": strategy,
        "matched_query_or_keyword": keyword,
        "body": "Ang init ngayon ✨",
    }


def _setup(tmp_path: Path) -> tuple[Path, Path, Path]:
    data = tmp_path / "data"
    trial = data / "raw" / "CasualPH" / "2026-11-10.jsonl"
    clean = data / "raw" / "CasualPH" / "2026-11-11.jsonl"
    trial.parent.mkdir(parents=True)
    trial_lines = [
        json.dumps(_rec("t3_walk", "keyword_oversampled", "2026-11-10", "run-a")),
        json.dumps(_rec("t1_walkc", "keyword_oversampled", "2026-11-09", "run-a")),
        json.dumps(_rec("t3_real", "keyword_oversampled", "sana all", "run-b")),
        json.dumps(_rec("t3_nat", "natural", None, "run-a"), ensure_ascii=False),
        '{"partial line',
    ]
    trial.write_text("\n".join(trial_lines) + "\n", encoding="utf-8")
    clean.write_text(json.dumps(_rec("t3_other", "natural", None, "run-c")) + "\n", "utf-8")
    (data / "manifests").mkdir()
    (data / "manifests" / "run-a.json").write_text(
        json.dumps({"collection_run_id": "run-a", "sampling_strategy": "keyword_oversampled"}),
        encoding="utf-8",
    )
    return data, trial, clean


def test_retags_only_date_keyword_records_and_backs_up(tmp_path: Path) -> None:
    data, trial, clean = _setup(tmp_path)
    original = trial.read_text(encoding="utf-8")
    clean_before = clean.read_bytes()
    backup_dir = tmp_path / "backup"

    stats = retag_calendar_walk_records(data, backup_dir)

    assert stats["records"] == 2
    assert stats["files_rewritten"] == 1
    assert stats["runs"] == {"run-a": 2}
    assert sorted(stats["fullnames"]) == ["t1_walkc", "t3_walk"]

    lines = trial.read_text(encoding="utf-8").splitlines()
    rows = {json.loads(line)["reddit_fullname"]: json.loads(line) for line in lines[:4]}
    assert rows["t3_walk"]["sampling_strategy"] == "natural"
    assert rows["t3_walk"]["matched_query_or_keyword"] is None
    assert rows["t1_walkc"]["sampling_strategy"] == "natural"
    assert rows["t3_real"]["sampling_strategy"] == "keyword_oversampled"  # a real keyword
    # Untouched lines are byte-identical; the partial line is kept as-is.
    assert lines[2] == original.splitlines()[2]
    assert lines[3] == original.splitlines()[3]
    assert lines[4] == '{"partial line'
    assert clean.read_bytes() == clean_before  # files without matches aren't rewritten

    backup = backup_dir / "raw" / "CasualPH" / "2026-11-10.jsonl"
    assert backup.read_text(encoding="utf-8") == original

    manifest = json.loads((data / "manifests" / "run-a.json").read_text(encoding="utf-8"))
    assert manifest["sampling_strategy"] == "keyword_oversampled"  # history kept
    assert manifest["corrections"][0]["records"] == 2


def test_retag_is_idempotent(tmp_path: Path) -> None:
    data, trial, _ = _setup(tmp_path)
    retag_calendar_walk_records(data, tmp_path / "b1")
    after_first = trial.read_bytes()
    stats = retag_calendar_walk_records(data, tmp_path / "b2")
    assert stats["records"] == 0 and stats["files_rewritten"] == 0
    assert trial.read_bytes() == after_first
    assert not (tmp_path / "b2").exists()
    manifest = json.loads((data / "manifests" / "run-a.json").read_text(encoding="utf-8"))
    assert len(manifest["corrections"]) == 1


def test_rewrite_splits_on_newline_only_and_keeps_other_bytes(tmp_path: Path) -> None:
    """U+2028/U+2029/U+0085 inside a record must not split it, and untouched
    lines (blank, CRLF, partial) stay byte-identical."""
    data = tmp_path / "data"
    raw = data / "raw" / "CasualPH" / "2026-11-10.jsonl"
    raw.parent.mkdir(parents=True)
    body = "a b c\x85d"
    tricky = _rec("t1_sep", "natural", None, "run-a") | {"body": body}
    lines = [
        json.dumps(_rec("t3_walk", "keyword_oversampled", "2026-11-10", "run-a")) + "\r",
        json.dumps(tricky, ensure_ascii=False),
        "",
        json.dumps(_rec("t3_nat", "natural", None, "run-a")) + "\r",
        '{"partial line',
    ]
    original = "\n".join(lines).encode("utf-8")
    raw.write_bytes(original)

    stats = retag_calendar_walk_records(data, tmp_path / "backup")
    assert stats["records"] == 1
    out = raw.read_bytes().decode("utf-8").split("\n")
    assert len(out) == len(lines)
    assert out[0].endswith("\r") and json.loads(out[0])["sampling_strategy"] == "natural"
    assert out[1:] == lines[1:]  # separator characters, blank and partial lines kept
    assert json.loads(out[1])["body"] == body
    backup = tmp_path / "backup" / "raw" / "CasualPH" / "2026-11-10.jsonl"
    assert backup.read_bytes() == original
