"""Evaluation-only annotators: config, the evaluate pass, the report, and the
guarantee that evaluator votes never reach aggregation, agreement, or export."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from tests.annotate_fixtures import make_config, write_raw_corpus
from tests.test_annotate_runner import FakeClient, make_output
from uyam.annotate.aggregate import compute_agreement, run_aggregation
from uyam.annotate.candidates import select_candidates
from uyam.annotate.config import AnnotationConfig, AnnotatorConfig, load_annotation_config
from uyam.annotate.corpus import index_corpus
from uyam.annotate.db import AnnotationDatabase
from uyam.annotate.evaluation import (
    EVALUATOR_ROLE,
    build_eval_report,
    insert_eval_annotation,
    render_eval_markdown,
    run_evaluator,
    sarcasm_metrics,
)
from uyam.annotate.export import run_export
from uyam.annotate.ollama_client import AnnotationCallError
from uyam.annotate.prompts import PROMPT_VERSION
from uyam.annotate.runner import run_annotator
from uyam.annotate.schemas import LLMAnnotationOut

_REPO = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

_BASE_YAML = """
prompt_version: {pv}
endpoints:
  local: http://127.0.0.1:11434
  remote: http://remote:11434
annotators:
  - {{key: a1, model: m1, endpoint: local}}
  - {{key: a2, model: m2, endpoint: remote}}
adjudicator: {{key: adj, model: madj, endpoint: remote}}
"""


def _write_yaml(tmp_path: Path, extra: str = "") -> Path:
    path = tmp_path / "annotation.yaml"
    path.write_text(_BASE_YAML.format(pv=PROMPT_VERSION) + extra, encoding="utf-8")
    return path


def test_evaluators_load_and_stay_out_of_annotator_lookup(tmp_path: Path) -> None:
    cfg = load_annotation_config(
        _write_yaml(
            tmp_path,
            "evaluators:\n  - {key: nimble, model: 'nimble:9b', endpoint: remote}\n",
        )
    )
    assert [e.key for e in cfg.evaluators] == ["nimble"]
    assert cfg.evaluator("nimble").model == "nimble:9b"
    assert cfg.evaluator("nimble").think is None
    assert [a.key for a in cfg.annotators] == ["a1", "a2"]
    with pytest.raises(KeyError):
        cfg.annotator("nimble")  # `annotate run --annotator nimble` cannot reach it
    with pytest.raises(KeyError, match="No evaluator"):
        cfg.evaluator("a1")


def test_evaluators_default_to_empty(tmp_path: Path) -> None:
    assert load_annotation_config(_write_yaml(tmp_path)).evaluators == []


@pytest.mark.parametrize("key", ["a1", "adj"])
def test_evaluator_key_collision_fails_clearly(tmp_path: Path, key: str) -> None:
    path = _write_yaml(
        tmp_path, f"evaluators:\n  - {{key: {key}, model: x, endpoint: remote}}\n"
    )
    with pytest.raises(ValueError, match="collides"):
        load_annotation_config(path)


def test_evaluator_duplicate_key_and_unknown_endpoint_fail(tmp_path: Path) -> None:
    dup = _write_yaml(
        tmp_path,
        "evaluators:\n  - {key: e, model: x, endpoint: remote}\n"
        "  - {key: e, model: y, endpoint: remote}\n",
    )
    with pytest.raises(ValueError, match="listed twice"):
        load_annotation_config(dup)
    bad = _write_yaml(tmp_path, "evaluators:\n  - {key: e, model: x, endpoint: nowhere}\n")
    with pytest.raises(ValueError, match="unknown endpoint"):
        load_annotation_config(bad)


def test_repo_config_has_nimble_as_evaluator_only() -> None:
    cfg = load_annotation_config(_REPO / "config" / "annotation.yaml")
    nimble = cfg.evaluator("nimble")
    assert (nimble.model, nimble.endpoint) == ("nimble:9b", "remote")
    assert "nimble" not in {a.key for a in cfg.annotators}


# ---------------------------------------------------------------------------
# The evaluate pass
# ---------------------------------------------------------------------------

def _target(prompt: str) -> str:
    """The TARGET block of a rendered prompt (the few-shots also mention 'Wow')."""
    return prompt.split("=== TARGET", 1)[1].split("=== TASK", 1)[0]


def _vote(annotator: Any, prompt: str) -> LLMAnnotationOut:
    """Deterministic disagreement so aggregation has escalations and kappa."""
    target = _target(prompt)
    sarcastic = ("Wow" in target and annotator.key != "a3") or (
        "c1 " in target and annotator.key == "a1"
    )
    return make_output(sarcastic=sarcastic, intended="negative" if sarcastic else "neutral")


class RecordingClient(FakeClient):
    """FakeClient that remembers the exact user prompt per target text."""

    def __init__(self, respond: Any = None) -> None:
        super().__init__(respond or _vote)
        self.prompts: list[str] = []

    def annotate(
        self, annotator: Any, system_prompt: str, user_prompt: str, options_cfg: Any
    ) -> Any:
        self.prompts.append(user_prompt)
        return super().annotate(annotator, system_prompt, user_prompt, options_cfg)


def _prepare(tmp_path: Path) -> tuple[AnnotationConfig, RecordingClient]:
    """Corpus -> 3 annotators -> aggregate -> gold sample, plus an evaluator."""
    cfg = make_config(tmp_path)
    cfg.evaluators = [AnnotatorConfig(key="ev", model="model-eval", endpoint="remote")]
    write_raw_corpus(cfg.data_dir)
    with AnnotationDatabase(cfg.db_path) as db:
        index_corpus(db, cfg.data_dir)
        select_candidates(db, cfg.candidate_filters)
    base = RecordingClient()
    for key in ("a1", "a2", "a3"):
        run_annotator(cfg, key, client_factory=lambda c, a: base)
    run_aggregation(cfg, PROMPT_VERSION)
    with AnnotationDatabase(cfg.db_path) as db:
        for fullname in db.eligible_fullnames()[:4]:
            db.enqueue_review(fullname, "gold")
        db.commit()
    return cfg, base


def _table(db: AnnotationDatabase, table: str, drop: tuple[str, ...] = ()) -> list[dict[str, Any]]:
    rows = db.conn.execute(f"SELECT * FROM {table} ORDER BY 1, 2, 3").fetchall()
    return [{k: r[k] for k in r.keys() if k not in drop} for r in rows]  # noqa: SIM118


def _dataset_state(db: AnnotationDatabase) -> dict[str, Any]:
    return {
        "llm_annotations": _table(db, "llm_annotations"),
        "aggregates": _table(db, "aggregates", drop=("updated_at",)),
        "contexts": _table(db, "contexts"),
        "review_queue": _table(db, "review_queue"),
        "target": db.target_items(PROMPT_VERSION, 100),
        "progress": db.model_progress(PROMPT_VERSION),
    }


def _gold(db: AnnotationDatabase) -> list[str]:
    return [str(q["reddit_fullname"]) for q in db.review_queue_items(only_pending=False)]


def test_evaluate_writes_only_eval_annotations(tmp_path: Path) -> None:
    cfg, base = _prepare(tmp_path)
    with AnnotationDatabase(cfg.db_path) as db:
        before = _dataset_state(db)
        gold = _gold(db)

    ev = RecordingClient(lambda a, p: make_output(sarcastic=True))
    stats = run_evaluator(cfg, "ev", client_factory=lambda c, a: ev)
    assert stats["selected"] == len(gold) == 4
    assert stats["done"] == 4 and ev.calls == 4

    with AnnotationDatabase(cfg.db_path) as db:
        assert _dataset_state(db) == before
        rows = _table(db, "eval_annotations")
        assert sorted(r["reddit_fullname"] for r in rows) == sorted(gold)
        assert {r["model_key"] for r in rows} == {"ev"}
        assert {r["prompt_version"] for r in rows} == {PROMPT_VERSION}
        assert all(r["sarcastic"] == 1 and r["model_digest"] == "sha256:test" for r in rows)
        assert json.loads(rows[0]["options_json"])["model"] == "model-eval"
    # The evaluator saw exactly the prompts (incl. persisted context) the base saw.
    assert set(ev.prompts) <= set(base.prompts)

    # Re-aggregating after the evaluator ran changes nothing either.
    run_aggregation(cfg, PROMPT_VERSION)
    with AnnotationDatabase(cfg.db_path) as db:
        assert _dataset_state(db) == before


def test_evaluate_resumes_and_respects_limit(tmp_path: Path) -> None:
    cfg, _ = _prepare(tmp_path)
    ev = FakeClient()
    first = run_evaluator(cfg, "ev", limit=1, client_factory=lambda c, a: ev)
    assert first["done"] == 1
    second = run_evaluator(cfg, "ev", client_factory=lambda c, a: ev)
    assert (second["done"], second["already_done"]) == (3, 1)
    third = run_evaluator(cfg, "ev", client_factory=lambda c, a: ev)
    assert (third["done"], third["already_done"]) == (0, 4)
    assert ev.calls == 4  # nothing labeled twice


def test_evaluate_skips_items_without_context_snapshot(tmp_path: Path) -> None:
    cfg, _ = _prepare(tmp_path)
    with AnnotationDatabase(cfg.db_path) as db:
        missing, stale, *_ = _gold(db)
        db.conn.execute("DELETE FROM contexts WHERE reddit_fullname = ?", (missing,))
        db.conn.execute(
            "UPDATE contexts SET prompt_version = 'sarc-v0' WHERE reddit_fullname = ?", (stale,)
        )
        db.commit()

    ev = FakeClient()
    stats = run_evaluator(cfg, "ev", client_factory=lambda c, a: ev)
    assert stats["no_context"] == 2
    assert stats["done"] == 2 and ev.calls == 2
    with AnnotationDatabase(cfg.db_path) as db:
        assert db.get_context(missing) is None  # skipped, not rebuilt
        assert db.get_context(stale)["prompt_version"] == "sarc-v0"  # type: ignore[index]
        labeled = {r["reddit_fullname"] for r in _table(db, "eval_annotations")}
        assert missing not in labeled and stale not in labeled


def test_evaluate_items_file_and_failures(tmp_path: Path) -> None:
    cfg, _ = _prepare(tmp_path)
    with AnnotationDatabase(cfg.db_path) as db:
        eligible = db.eligible_fullnames()
        pending_before = db.pending_annotator_items("a1", PROMPT_VERSION)
    items = tmp_path / "items.txt"
    items.write_text(
        f"# picked by hand\n{eligible[-1]}\n\n{eligible[-2]}  # second\n{eligible[-1]}\n",
        encoding="utf-8",
    )

    class Failing(FakeClient):
        def annotate(
            self, annotator: Any, system_prompt: str, user_prompt: str, options_cfg: Any
        ) -> Any:
            self.calls += 1
            raise AnnotationCallError("boom")

    stats = run_evaluator(cfg, "ev", items=items, client_factory=lambda c, a: Failing())
    assert (stats["selected"], stats["failed"]) == (2, 2)
    with AnnotationDatabase(cfg.db_path) as db:
        roles = {
            (str(r["model_key"]), str(r["role"]))
            for r in db.conn.execute("SELECT model_key, role FROM llm_failures")
        }
        assert roles == {("ev", EVALUATOR_ROLE)}
        assert db.pending_annotator_items("a1", PROMPT_VERSION) == pending_before
        assert _table(db, "eval_annotations") == []

    again = run_evaluator(cfg, "ev", items=items, client_factory=lambda c, a: FakeClient())
    assert (again["done"], again["skipped_failed"]) == (0, 2)
    retry = run_evaluator(
        cfg, "ev", items=items, retry_failed=True, client_factory=lambda c, a: FakeClient()
    )
    assert retry["done"] == 2

    with pytest.raises(FileNotFoundError):
        run_evaluator(
            cfg, "ev", items=tmp_path / "nope.txt", client_factory=lambda c, a: FakeClient()
        )
    with pytest.raises(KeyError):
        run_evaluator(cfg, "a1", client_factory=lambda c, a: FakeClient())


def test_aggregation_agreement_and_export_ignore_evaluator_rows(tmp_path: Path) -> None:
    cfg, _ = _prepare(tmp_path)
    with AnnotationDatabase(cfg.db_path) as db:
        gold = _gold(db)
        for i, fullname in enumerate(gold):
            db.upsert_human_review(
                {
                    "reddit_fullname": fullname,
                    "sarcastic": i % 2,
                    "language": "taglish",
                    "literal_sentiment": "neutral",
                    "intended_sentiment": "neutral",
                    "is_gold": 1,
                }
            )
        eligible = db.eligible_fullnames()

    def snapshot(tag: str) -> str:
        stats = run_aggregation(cfg, PROMPT_VERSION)
        report = compute_agreement(cfg, PROMPT_VERSION)
        assert report["labels"]["sarcastic"]["fleiss_kappa"] is not None
        result = run_export(
            cfg, PROMPT_VERSION, dataset_version=tag, formats=("jsonl",), bundle=False
        )
        rows = Path(result["jsonl"]).read_text(encoding="utf-8")
        with AnnotationDatabase(cfg.db_path) as db:
            aggregates = _table(db, "aggregates", drop=("updated_at",))
        # json, not ==: the gold kappa can be NaN (single-class gold), and NaN != NaN.
        return json.dumps([stats, report, rows, aggregates], sort_keys=True, default=str)

    before = snapshot("vbefore")

    # Evaluator votes on every eligible item, disagreeing with the base annotators.
    items = tmp_path / "all.txt"
    items.write_text("\n".join(eligible), encoding="utf-8")
    run_evaluator(
        cfg,
        "ev",
        items=items,
        client_factory=lambda c, a: FakeClient(
            lambda an, p: make_output(sarcastic="Wow" not in _target(p), language="english")
        ),
    )
    with AnnotationDatabase(cfg.db_path) as db:
        n_eval = db.conn.execute("SELECT COUNT(*) FROM eval_annotations").fetchone()[0]
    assert n_eval == len(eligible)

    after = snapshot("vbefore")  # same version tag so the files are comparable
    assert after == before


_EVAL_SQL = re.compile(r"\b(FROM|JOIN|INTO|UPDATE|TABLE|EXISTS)\s+eval_annotations\b", re.I)


def test_only_db_and_evaluation_modules_query_eval_annotations() -> None:
    """Static guard: no other module may query the evaluator table."""
    src = _REPO / "src" / "uyam"
    offenders = [
        str(p.relative_to(src))
        for p in src.rglob("*.py")
        if _EVAL_SQL.search(p.read_text(encoding="utf-8"))
        and p.relative_to(src).as_posix() not in {"annotate/db.py", "annotate/evaluation.py"}
    ]
    assert offenders == []


# ---------------------------------------------------------------------------
# eval-report numbers on a hand-made gold set
# ---------------------------------------------------------------------------

def _row(fullname: str, model_key: str, sarcastic: int, role: str | None) -> dict[str, Any]:
    row: dict[str, Any] = {
        "reddit_fullname": fullname,
        "model_key": model_key,
        "prompt_version": PROMPT_VERSION,
        "language": "taglish",
        "literal_sentiment": "neutral",
        "intended_sentiment": "neutral",
        "sarcastic": sarcastic,
        "confidence": 0.9,
    }
    if role is not None:
        row["role"] = role
    return row


def _hand_made_report(tmp_path: Path) -> dict[str, Any]:
    cfg = make_config(tmp_path)
    cfg.evaluators = [
        AnnotatorConfig(key="nimble", model="nimble:9b", endpoint="remote"),
        AnnotatorConfig(key="idle", model="idle:1b", endpoint="remote"),
    ]
    human = [1, 1, 1, 0, 0, 0]
    votes: dict[str, list[int | None]] = {
        "a1": [1, 1, 0, 0, 0, 1],
        "a2": [1, 1, 1, 0, 0, 0],
        "a3": [0, 1, 1, 0, 1, None],  # no vote on item 6 -> a1/a2 tie there
    }
    shipped = [1, 1, 0, 0, 0, 0, 1]  # item 7 is not gold
    nimble = [1, 1, 1, 1, 1, 0, 1]
    names = [f"t1_g{i}" for i in range(1, 8)]
    with AnnotationDatabase(cfg.db_path) as db:
        for i, fullname in enumerate(names):
            if i < len(human):
                db.upsert_human_review(
                    {"reddit_fullname": fullname, "sarcastic": human[i], "is_gold": 1}
                )
                for key, vs in votes.items():
                    if vs[i] is not None:
                        db.insert_llm_annotation(_row(fullname, key, int(vs[i]), "annotator"))
            db.upsert_aggregate(
                {
                    "reddit_fullname": fullname,
                    "prompt_version": PROMPT_VERSION,
                    "sarcastic_final": shipped[i],
                    "resolved_by": "majority",
                }
            )
            insert_eval_annotation(db, _row(fullname, "nimble", nimble[i], None))
        # A non-gold human review must not count as gold.
        db.upsert_human_review({"reddit_fullname": "t1_g7", "sarcastic": 0, "is_gold": 0})
        db.commit()
    return build_eval_report(cfg, PROMPT_VERSION)


def test_eval_report_numbers(tmp_path: Path) -> None:
    report = _hand_made_report(tmp_path)
    assert (report["n_gold"], report["n_gold_sarcastic"]) == (6, 3)
    rows = {r["source"]: r for r in report["vs_human"]}
    assert list(rows) == [
        "a1", "a2", "a3", "annotator majority", "shipped final", "nimble", "idle",
    ]

    def metrics(source: str) -> tuple[Any, ...]:
        r = rows[source]
        return r["n"], r["accuracy"], r["f1_sarcastic"], r["kappa"]

    assert metrics("a1") == (6, 0.6667, 0.6667, 0.3333)
    assert metrics("a2") == (6, 1.0, 1.0, 1.0)
    assert metrics("a3") == (5, 0.6, 0.6667, 0.1667)
    assert metrics("annotator majority") == (5, 1.0, 1.0, 1.0)  # item 6 is a 1-1 tie
    assert metrics("shipped final") == (6, 0.8333, 0.8, 0.6667)
    assert metrics("nimble") == (6, 0.6667, 0.75, 0.3333)
    assert metrics("idle") == (0, None, None, None)

    vs_final = {r["evaluator"]: r for r in report["evaluators_vs_final"]}
    assert vs_final["nimble"] == {
        "evaluator": "nimble",
        "n_labeled": 7,
        "n": 7,
        "agreement": 0.5714,
        "kappa": 0.2222,  # (28/49 - 22/49) / (1 - 22/49) = 6/27
    }
    assert vs_final["idle"]["n"] == 0 and vs_final["idle"]["kappa"] is None

    md = render_eval_markdown(report)
    assert "| nimble | evaluator | 6 | 0.667 | 0.750 | 0.333 |" in md
    assert "| idle | evaluator | 0 | n/a | n/a | n/a |" in md


def test_sarcasm_metrics_edge_cases() -> None:
    assert sarcasm_metrics([], []) == {
        "n": 0, "accuracy": None, "f1_sarcastic": None, "kappa": None,
    }
    # Both raters only ever say "not sarcastic": accuracy 1, F1 and kappa undefined.
    assert sarcasm_metrics([0, 0, 0], [0, 0, 0]) == {
        "n": 3, "accuracy": 1.0, "f1_sarcastic": None, "kappa": None,
    }
    # One rater single-class, the other not: kappa is 0, not a crash.
    assert sarcasm_metrics([1, 0], [1, 1]) == {
        "n": 2, "accuracy": 0.5, "f1_sarcastic": 0.6667, "kappa": 0.0,
    }
