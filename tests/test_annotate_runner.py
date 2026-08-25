"""Annotator runner: resumability, failure handling, prompt-version guard."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tests.annotate_fixtures import make_config, write_raw_corpus
from uyam.annotate.candidates import select_candidates
from uyam.annotate.corpus import index_corpus
from uyam.annotate.db import AnnotationDatabase
from uyam.annotate.ollama_client import AnnotationCallError, AnnotationResult
from uyam.annotate.prompts import PROMPT_VERSION, prompt_content_hash
from uyam.annotate.runner import run_annotator
from uyam.annotate.schemas import CueFlags, LLMAnnotationOut


def make_output(
    *,
    sarcastic: bool = False,
    language: str = "taglish",
    literal: str = "neutral",
    intended: str = "neutral",
    confidence: float = 0.9,
) -> LLMAnnotationOut:
    return LLMAnnotationOut(
        language=language,  # type: ignore[arg-type]
        literal_sentiment=literal,  # type: ignore[arg-type]
        cues=CueFlags(
            polarity_inversion=sarcastic,
            rhetorical_intent=False,
            contextual_incongruity=False,
            hyperbole=False,
        ),
        sarcastic=sarcastic,
        intended_sentiment=intended,  # type: ignore[arg-type]
        confidence=confidence,
        rationale="test rationale",
    )


class FakeClient:
    def __init__(self, respond: Any = None) -> None:
        self.respond = respond or (lambda annotator, prompt: make_output())
        self.calls = 0

    def annotate(
        self, annotator: Any, system_prompt: str, user_prompt: str, options_cfg: Any
    ) -> AnnotationResult:
        self.calls += 1
        parsed = self.respond(annotator, user_prompt)
        return AnnotationResult(
            parsed=parsed,
            raw_content=parsed.model_dump_json(),
            options_used={"temperature": 0.0},
            attempts=1,
            duration_ms=5,
            prompt_tokens=100,
            completion_tokens=50,
        )

    def server_version(self) -> str:
        return "0.0-test"

    def model_digest(self, model: str) -> str:
        return "sha256:test"


def _prepare(tmp_path: Path) -> Any:
    cfg = make_config(tmp_path)
    write_raw_corpus(cfg.data_dir)
    with AnnotationDatabase(cfg.db_path) as db:
        index_corpus(db, cfg.data_dir)
        select_candidates(db, cfg.candidate_filters)
        eligible = db.eligible_fullnames()
    return cfg, eligible


def test_runner_annotates_all_pending_then_resumes_to_zero(tmp_path: Path) -> None:
    cfg, eligible = _prepare(tmp_path)
    fake = FakeClient()

    stats = run_annotator(cfg, "a1", client_factory=lambda c, a: fake)
    assert stats["done"] == len(eligible)
    assert fake.calls == len(eligible)

    stats2 = run_annotator(cfg, "a1", client_factory=lambda c, a: fake)
    assert stats2["done"] == 0
    assert fake.calls == len(eligible)  # no extra calls — fully resumed

    with AnnotationDatabase(cfg.db_path) as db:
        rows = db.annotations_for_item(eligible[0], PROMPT_VERSION)
        assert len(rows) == 1
        assert rows[0]["model_key"] == "a1"
        assert rows[0]["model_digest"] == "sha256:test"
        assert db.get_context(eligible[0]) is not None  # snapshot persisted


def test_runner_records_failures_and_retry_failed(tmp_path: Path) -> None:
    cfg, eligible = _prepare(tmp_path)

    def explode(annotator: Any, prompt: str) -> LLMAnnotationOut:
        raise AnnotationCallError("boom")

    class FailingClient(FakeClient):
        def annotate(self, annotator: Any, system_prompt: str, user_prompt: str, options_cfg: Any):
            self.calls += 1
            raise AnnotationCallError("boom")

    failing = FailingClient(explode)
    stats = run_annotator(cfg, "a1", client_factory=lambda c, a: failing)
    assert stats["failed"] == len(eligible)

    # Failed items are excluded from the pending queue by default...
    stats2 = run_annotator(cfg, "a1", client_factory=lambda c, a: FakeClient())
    assert stats2["done"] == 0

    # ...and retried after --retry-failed clears them.
    stats3 = run_annotator(cfg, "a1", retry_failed=True, client_factory=lambda c, a: FakeClient())
    assert stats3["done"] == len(eligible)


def test_runner_limit_and_dry_run(tmp_path: Path) -> None:
    cfg, _ = _prepare(tmp_path)
    fake = FakeClient()
    stats = run_annotator(cfg, "a1", limit=2, client_factory=lambda c, a: fake)
    assert stats["done"] == 2

    dry = run_annotator(cfg, "a2", dry_run=True, client_factory=lambda c, a: fake)
    assert dry["done"] == 0
    with AnnotationDatabase(cfg.db_path) as db:
        assert db.pending_annotator_items("a2", PROMPT_VERSION)  # dry run stored nothing


def test_prompt_version_drift_is_rejected(tmp_path: Path) -> None:
    cfg, _ = _prepare(tmp_path)
    with AnnotationDatabase(cfg.db_path) as db:
        db.check_prompt_version(PROMPT_VERSION, prompt_content_hash())
        with pytest.raises(RuntimeError, match="Bump PROMPT_VERSION"):
            db.check_prompt_version(PROMPT_VERSION, "a-different-hash")

    cfg.prompt_version = "sarc-v999"
    with pytest.raises(RuntimeError, match="does not match"):
        run_annotator(cfg, "a1", client_factory=lambda c, a: FakeClient())
