"""Target set: ranking, per-model stop-at-N, overlap across models, orchestrator order."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tests.annotate_fixtures import make_config, write_raw_corpus
from tests.test_annotate_runner import FakeClient
from uyam.annotate.candidates import select_candidates
from uyam.annotate.corpus import index_corpus
from uyam.annotate.db import AnnotationDatabase
from uyam.annotate.pipeline import JobBackend, PipelineError, run_pipeline
from uyam.annotate.prompts import PROMPT_VERSION
from uyam.annotate.runner import order_least_done_first, run_annotator


def _prepare(tmp_path: Path) -> tuple[Any, list[str]]:
    cfg = make_config(tmp_path)
    write_raw_corpus(cfg.data_dir)
    with AnnotationDatabase(cfg.db_path) as db:
        index_corpus(db, cfg.data_dir)
        select_candidates(db, cfg.candidate_filters)
        eligible = db.eligible_fullnames()
    assert len(eligible) >= 5
    return cfg, eligible


def test_target_set_ranks_voted_items_first_then_by_id(tmp_path: Path) -> None:
    cfg, eligible = _prepare(tmp_path)
    fake = FakeClient()
    # a1 labels the LAST three ids, a2 the last two: they must lead the ranking
    # even though plain id order would put them last.
    with AnnotationDatabase(cfg.db_path) as db:
        for fullname in eligible[-3:]:
            run_annotator(cfg, "a1", only_fullname=fullname, client_factory=lambda c, a: fake)
        for fullname in eligible[-2:]:
            run_annotator(cfg, "a2", only_fullname=fullname, client_factory=lambda c, a: fake)

        target = db.target_items(PROMPT_VERSION, 4)
        assert target[:2] == eligible[-2:]  # two votes each
        assert target[2] == eligible[-3]  # one vote
        assert target[3] == eligible[0]  # zero votes, smallest id

        # A model's pending queue is the target set minus its own finished items.
        assert db.pending_target_items("a3", PROMPT_VERSION, 4) == target
        assert db.pending_target_items("a1", PROMPT_VERSION, 4) == [eligible[0]]
        assert db.pending_target_items("a2", PROMPT_VERSION, 4) == [eligible[-3], eligible[0]]

        prog = db.target_progress(PROMPT_VERSION, 4, ["a1", "a2", "a3"])
        assert prog["in_target"] == 4
        assert prog["models"]["a1"] == {"done": 3, "failed": 0, "pending": 1}
        assert prog["models"]["a2"] == {"done": 2, "failed": 0, "pending": 2}
        assert prog["models"]["a3"] == {"done": 0, "failed": 0, "pending": 4}
        assert prog["complete"] == 0

        # Sentiment for the target set only covers those four ids.
        assert set(db.missing_tx_sentiment_in_target(PROMPT_VERSION, 4)) == set(target)


def test_target_caps_at_eligible_corpus(tmp_path: Path) -> None:
    cfg, eligible = _prepare(tmp_path)
    with AnnotationDatabase(cfg.db_path) as db:
        assert db.target_items(PROMPT_VERSION, 10_000) == sorted(eligible)
        assert db.target_progress(PROMPT_VERSION, 10_000, ["a1"])["in_target"] == len(eligible)


def test_runner_stops_at_target_and_models_converge_on_the_same_items(tmp_path: Path) -> None:
    cfg, eligible = _prepare(tmp_path)
    fake = FakeClient()

    stats = run_annotator(cfg, "a1", target=4, client_factory=lambda c, a: fake)
    assert stats["done"] == 4
    assert fake.calls == 4

    # Target reached: a second run makes no calls even though items remain eligible.
    again = run_annotator(cfg, "a1", target=4, client_factory=lambda c, a: fake)
    assert again["done"] == 0
    assert fake.calls == 4
    with AnnotationDatabase(cfg.db_path) as db:
        assert len(db.pending_annotator_items("a1", PROMPT_VERSION)) == len(eligible) - 4

    # The other models pick the SAME four items (a1's votes rank them first).
    for key in ("a2", "a3"):
        run_annotator(cfg, key, target=4, client_factory=lambda c, a: FakeClient())
    with AnnotationDatabase(cfg.db_path) as db:
        prog = db.target_progress(PROMPT_VERSION, 4, ["a1", "a2", "a3"])
        assert prog["complete"] == 4
        assert all(m["done"] == 4 for m in prog["models"].values())

    # --limit still caps a single run inside the target.
    cfg2, _ = _prepare(tmp_path / "second")
    capped = run_annotator(cfg2, "a1", target=4, limit=2, client_factory=lambda c, a: FakeClient())
    assert capped["done"] == 2


def test_order_least_done_first(tmp_path: Path) -> None:
    cfg, _ = _prepare(tmp_path)
    run_annotator(cfg, "a1", target=4, limit=3, client_factory=lambda c, a: FakeClient())
    run_annotator(cfg, "a2", target=4, limit=1, client_factory=lambda c, a: FakeClient())
    with AnnotationDatabase(cfg.db_path) as db:
        assert order_least_done_first(db, ["a1", "a2", "a3"], PROMPT_VERSION, 4) == [
            "a3",
            "a2",
            "a1",
        ]


class _FakeBackend:
    """Records every start/wait; child jobs 'succeed' instantly."""

    def __init__(self, exit_codes: dict[str, int] | None = None) -> None:
        self.events: list[tuple[str, str, list[str]]] = []
        self.exit_codes = exit_codes or {}

    def start(self, name: str, args: list[str], *, parent: str | None = None) -> int:
        assert parent == "pipeline"
        self.events.append(("start", name, list(args)))
        return 4242

    def wait(self, name: str, *, poll_seconds: float = 0.0) -> int | None:
        self.events.append(("wait", name, []))
        return self.exit_codes.get(name, 0)

    def is_running(self, name: str) -> bool:
        return False

    def backend(self) -> JobBackend:
        return JobBackend(start=self.start, wait=self.wait, is_running=self.is_running)


def test_pipeline_orders_lanes_and_passes_target(tmp_path: Path) -> None:
    cfg, _ = _prepare(tmp_path)
    # a1 (local) already has 2 done, a2 (local) none: the local lane must run a2 first.
    run_annotator(cfg, "a1", target=4, limit=2, client_factory=lambda c, a: FakeClient())

    fake = _FakeBackend()
    summary = run_pipeline(
        cfg,
        target=4,
        skip_prep=True,  # LID needs the fastText model download
        poll_seconds=0.0,
        backend=fake.backend(),
    )
    assert summary["target"] == 4

    starts = [(name, args) for kind, name, args in fake.events if kind == "start"]
    names = [name for name, _ in starts]
    # Remote lane first (concurrent), then GPU sentiment, then the local lane,
    # and only after every lane the adjudicator (a1's two single-vote items are
    # escalated by aggregation, so it does run here).
    assert names == ["run-all-remote", "sentiment", "run-all-local", "adjudicate"]
    by_name = dict(starts)
    assert by_name["run-all-remote"] == ["run", "--annotator", "a3", "--target", "4"]
    assert by_name["sentiment"] == ["sentiment", "--target", "4"]
    assert by_name["run-all-local"] == [
        "run", "--annotator", "a2", "--annotator", "a1", "--target", "4",
    ]
    assert by_name["adjudicate"] == ["adjudicate"]
    # Sentiment is awaited BEFORE the local lane starts (shared GPU), and both
    # lanes are awaited before adjudication (the adjudicator shares the remote box).
    order = [(kind, name) for kind, name, _ in fake.events]
    assert order.index(("wait", "sentiment")) < order.index(("start", "run-all-local"))
    assert order.index(("wait", "run-all-remote")) < order.index(("start", "adjudicate"))
    assert summary["lanes"] == {"local": 0, "remote": 0}
    assert summary["adjudicate_exit"] == 0


def test_pipeline_stops_when_a_lane_fails(tmp_path: Path) -> None:
    cfg, _ = _prepare(tmp_path)
    fake = _FakeBackend(exit_codes={"run-all-local": 1})
    with pytest.raises(PipelineError, match="local"):
        run_pipeline(cfg, target=4, skip_prep=True, poll_seconds=0.0, backend=fake.backend())
    names = [name for kind, name, _ in fake.events if kind == "start"]
    assert "adjudicate" not in names


def test_pipeline_retry_failed_flag_reaches_lanes(tmp_path: Path) -> None:
    cfg, _ = _prepare(tmp_path)
    fake = _FakeBackend()
    run_pipeline(
        cfg,
        target=4,
        skip_prep=True,
        skip_sentiment=True,
        retry_failed=True,
        poll_seconds=0.0,
        backend=fake.backend(),
    )
    starts = {name: args for kind, name, args in fake.events if kind == "start"}
    assert "sentiment" not in starts
    assert starts["run-all-local"][-1] == "--retry-failed"
    assert starts["run-all-remote"][-1] == "--retry-failed"
