"""Reminders: definitions, persistent done-state, detectors, and the Streamlit tab."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from uyam.reminders import CHECKS, ReminderState, load_reminders, run_check

REPO_ROOT = Path(__file__).resolve().parents[1]
REAL_CONFIG = REPO_ROOT / "config" / "reminders.yaml"


def test_shipped_reminders_are_well_formed() -> None:
    reminders = load_reminders(REAL_CONFIG)
    assert reminders, "config/reminders.yaml should list Gio's to-dos"
    assert len({r.id for r in reminders}) == len(reminders)
    for r in reminders:
        assert r.title.strip()
        assert r.check is None or r.check in CHECKS, f"{r.id}: unknown check {r.check!r}"


def test_duplicate_ids_are_rejected(tmp_path: Path) -> None:
    cfg = tmp_path / "r.yaml"
    cfg.write_text(
        "reminders:\n  - {id: a, title: One}\n  - {id: a, title: Two}\n", encoding="utf-8"
    )
    with pytest.raises(ValueError, match="duplicate"):
        load_reminders(cfg)


def test_done_state_persists_and_untick_clears(tmp_path: Path) -> None:
    path = tmp_path / "reminders.json"
    ReminderState(path).set_done("backup", True)

    reloaded = ReminderState(path)
    assert "backup" in reloaded.done
    first_stamp = reloaded.done["backup"]
    reloaded.set_done("backup", True)  # re-ticking keeps the original timestamp
    assert ReminderState(path).done["backup"] == first_stamp

    ReminderState(path).set_done("backup", False)
    assert ReminderState(path).done == {}
    assert not path.with_suffix(".json.tmp").exists()


def test_custom_reminders_add_and_delete(tmp_path: Path) -> None:
    path = tmp_path / "reminders.json"
    state = ReminderState(path)
    rid = state.add_custom("Ask adviser about κ", "before the defense")
    state.set_done(rid, True)

    customs = ReminderState(path).custom_reminders()
    assert [(c.id, c.title, c.custom) for c in customs] == [(rid, "Ask adviser about κ", True)]

    ReminderState(path).delete_custom(rid)
    reloaded = ReminderState(path)
    assert reloaded.custom_reminders() == []
    assert rid not in reloaded.done
    with pytest.raises(ValueError):
        reloaded.add_custom("   ")


def _manifest(data_dir: Path, name: str, **fields: object) -> None:
    (data_dir / "manifests").mkdir(parents=True, exist_ok=True)
    (data_dir / "manifests" / f"{name}.json").write_text(json.dumps(fields), encoding="utf-8")


def test_detectors_report_evidence_and_stay_quiet_without_it(tmp_path: Path) -> None:
    data = tmp_path / "data"
    assert all(run_check(name, data) is None for name in CHECKS)
    assert run_check("no_such_check", data) is None
    assert run_check(None, data) is None

    (data / "db").mkdir(parents=True)
    conn = sqlite3.connect(data / "db" / "annotation.sqlite3")
    conn.executescript(
        """
        CREATE TABLE candidates (reddit_fullname TEXT, exclusion_reason TEXT);
        INSERT INTO candidates VALUES ('t1_a', 'phatic'), ('t1_b', NULL);
        CREATE TABLE review_queue (reddit_fullname TEXT, reason TEXT, completed INTEGER);
        INSERT INTO review_queue VALUES ('t1_a', 'low_confidence', 0),
            ('t1_c', 'gold', 1), ('t1_d', 'gold', 0);
        """
    )
    conn.commit()
    conn.close()
    (data / "db" / "annotation-2026-11-10.bak.sqlite3").write_bytes(b"")
    _manifest(data, "a", source_type="archive", actual_submissions_stored=3, comments_stored=7)
    _manifest(
        data, "b", source_type="shreddit", started_at_utc="2026-11-09T01:00:00+00:00",
        actual_submissions_stored=2,
    )
    _manifest(data, "c", source_type="shreddit", started_at_utc="2026-09-01T00:00:00+00:00")
    (data / "annotated").mkdir()
    (data / "annotated" / "dataset-v2.jsonl").write_text("{}\n", encoding="utf-8")

    assert "annotation-2026-11-10.bak.sqlite3" in str(run_check("db_backup", data))
    assert run_check("archive_runs", data) == "1 archive runs: 3 posts, 7 comments stored"
    assert run_check("shreddit_recent", data) == (
        "1 Shreddit runs since 2026-11-08: 2 posts stored"
    )
    assert "1 items excluded" in str(run_check("phatic_selected", data))
    assert run_check("low_conf_queue", data) == "low_confidence queue: 1 pending, 0 done"
    assert run_check("gold_progress", data) == "Gold subset: 1 / 2 labeled"
    assert "dataset-v2.jsonl exported" in str(run_check("export_v2", data))


def test_reminders_tab_ticks_persist(tmp_path: Path) -> None:
    from streamlit.testing.v1 import AppTest

    cfg = tmp_path / "reminders.yaml"
    cfg.write_text(
        "reminders:\n"
        "  - {id: one, section: Annotation, title: Do the first thing, command: uyam x}\n"
        "  - {id: two, section: Optional, title: Do the second thing}\n",
        encoding="utf-8",
    )
    state = tmp_path / "data" / "reminders.json"

    def page(cfg_path: str, state_path: str, data_dir: str) -> None:
        from pathlib import Path

        from uyam.reminders_ui import render_reminders_tab

        render_reminders_tab(Path(cfg_path), Path(state_path), Path(data_dir))

    at = AppTest.from_function(page, args=(str(cfg), str(state), str(tmp_path / "data")))
    at.run()
    assert not at.exception
    assert len(at.checkbox) == 2

    at.checkbox(key="reminder_one").check().run()
    assert not at.exception
    assert set(json.loads(state.read_text(encoding="utf-8"))["done"]) == {"one"}
    # "Pending" view (default) no longer lists the ticked item.
    assert [c.key for c in at.checkbox] == ["reminder_two"]
