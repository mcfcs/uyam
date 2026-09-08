"""Resumable annotator pass: one model over all pending candidates.

Pending = eligible candidates minus rows already in llm_annotations for
(model_key, prompt_version, role) minus previously failed items (unless
--retry-failed). Safe to Ctrl+C and re-run; safe to run one model per machine
concurrently (local + remote endpoints), since all writes land in the one
local SQLite from this process.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any, Protocol

from rich.console import Console
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)

from uyam.annotate.config import AnnotationConfig, AnnotatorConfig
from uyam.annotate.context import get_or_build_context
from uyam.annotate.db import AnnotationDatabase
from uyam.annotate.ollama_client import AnnotationCallError, AnnotationResult
from uyam.annotate.prompts import (
    PROMPT_VERSION,
    SYSTEM_PROMPT,
    prompt_content_hash,
    render_user_prompt,
)

logger = logging.getLogger(__name__)
console = Console()


class AnnotatorClient(Protocol):
    """What the runner needs from a client (OllamaClient or a test fake)."""

    def annotate(
        self, annotator: AnnotatorConfig, system_prompt: str, user_prompt: str, options_cfg: Any
    ) -> AnnotationResult: ...

    def server_version(self) -> str | None: ...

    def model_digest(self, model: str) -> str | None: ...


def _make_client(cfg: AnnotationConfig, annotator: AnnotatorConfig) -> AnnotatorClient:
    from uyam.annotate.ollama_client import OllamaClient

    return OllamaClient(cfg.endpoint_url(annotator.endpoint), cfg.options.timeout_seconds)


def _annotation_row(
    fullname: str,
    annotator: AnnotatorConfig,
    role: str,
    result: AnnotationResult,
    *,
    endpoint_url: str,
    model_digest: str | None,
    ollama_version: str | None,
) -> dict[str, Any]:
    parsed = result.parsed
    return {
        "reddit_fullname": fullname,
        "model_key": annotator.key,
        "prompt_version": PROMPT_VERSION,
        "role": role,
        "language": parsed.language,
        "literal_sentiment": parsed.literal_sentiment,
        "intended_sentiment": parsed.intended_sentiment,
        "sarcastic": int(parsed.sarcastic),
        "cue_polarity_inversion": int(parsed.cues.polarity_inversion),
        "cue_rhetorical_intent": int(parsed.cues.rhetorical_intent),
        "cue_contextual_incongruity": int(parsed.cues.contextual_incongruity),
        "cue_hyperbole": int(parsed.cues.hyperbole),
        "confidence": parsed.confidence,
        "rationale": parsed.rationale,
        "raw_json": result.raw_content,
        "model_digest": model_digest,
        "endpoint": endpoint_url,
        "ollama_version": ollama_version,
        "options_json": json.dumps({"model": annotator.model, **result.options_used}),
        "attempts": result.attempts,
        "duration_ms": result.duration_ms,
        "prompt_tokens": result.prompt_tokens,
        "completion_tokens": result.completion_tokens,
    }


def order_least_done_first(
    db: AnnotationDatabase, keys: list[str], prompt_version: str, target: int
) -> list[str]:
    """Annotator keys sorted by how few target-set items they have finished."""
    progress = db.target_progress(prompt_version, target, keys)["models"]
    return sorted(keys, key=lambda k: (progress[k]["done"], keys.index(k)))


def run_annotator(
    cfg: AnnotationConfig,
    annotator_key: str,
    *,
    role: str = "annotator",
    limit: int | None = None,
    target: int | None = None,
    dry_run: bool = False,
    only_fullname: str | None = None,
    retry_failed: bool = False,
    client_factory: Callable[[AnnotationConfig, AnnotatorConfig], AnnotatorClient] | None = None,
) -> dict[str, int]:
    """Run one model over its pending queue. Returns done/failed/skipped counts.

    `limit` caps how many items THIS run labels. `target` makes the model work
    through the shared N-item target set (see AnnotationDatabase.target_items)
    and stop once it has N done — the pipeline's "reach 12k then move on".
    """
    if cfg.prompt_version != PROMPT_VERSION:
        raise RuntimeError(
            f"annotation.yaml prompt_version={cfg.prompt_version!r} does not match "
            f"prompts.PROMPT_VERSION={PROMPT_VERSION!r} — align them before running."
        )
    annotator = cfg.annotator(annotator_key)

    with AnnotationDatabase(cfg.db_path) as db:
        db.check_prompt_version(PROMPT_VERSION, prompt_content_hash())

        if retry_failed:
            cleared = db.clear_failures(annotator.key, PROMPT_VERSION, role)
            if cleared:
                console.print(f"Cleared {cleared} recorded failures for {annotator.key}")

        if only_fullname:
            pending = [only_fullname]
        elif role == "adjudicator":
            pending = db.pending_adjudicator_items(annotator.key, PROMPT_VERSION)
        elif target is not None:
            pending = db.pending_target_items(annotator.key, PROMPT_VERSION, target)
        else:
            pending = db.pending_annotator_items(annotator.key, PROMPT_VERSION)
        if limit is not None:
            pending = pending[:limit]

        stats = {"done": 0, "failed": 0, "skipped": 0}
        if not pending:
            if target is not None and role == "annotator":
                prog = db.target_progress(PROMPT_VERSION, target, [annotator.key])
                mine = prog["models"][annotator.key]
                if mine["done"] >= prog["in_target"]:
                    console.print(
                        f"[green]Target reached[/green] {annotator.key}: "
                        f"{mine['done']}/{prog['in_target']} target items done"
                    )
                else:
                    console.print(
                        f"[yellow]Nothing pending[/yellow] for {annotator.key}: "
                        f"{mine['done']}/{prog['in_target']} done, {mine['failed']} failed "
                        "(re-run with --retry-failed to retry them)"
                    )
            else:
                console.print(f"[green]Nothing pending[/green] for {annotator.key} ({role})")
            return stats

        if dry_run:
            preview = pending[: min(len(pending), 5 if limit is None else limit)]
            for fullname in preview:
                record = db.get_record(fullname)
                if record is None:
                    continue
                block, _ = get_or_build_context(db, record, cfg.context, PROMPT_VERSION)
                prompt = render_user_prompt(block, str(record["record_type"]), str(record["text"]))
                console.rule(f"{fullname} -> {annotator.model}")
                console.print(prompt)
            console.print(
                f"\n[cyan]Dry run:[/cyan] {len(pending)} pending for {annotator.key} ({role}); "
                f"previewed {len(preview)}. No calls made."
            )
            return stats

        client = (client_factory or _make_client)(cfg, annotator)
        ollama_version = client.server_version()
        model_digest = client.model_digest(annotator.model)
        target_note = f" (target {target})" if target is not None else ""
        console.print(
            f"Annotating with [bold]{annotator.model}[/bold] ({annotator.key}, {role}) "
            f"on {annotator.endpoint} — {len(pending)} pending{target_note}"
        )

        progress = Progress(
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            MofNCompleteColumn(),
            TimeElapsedColumn(),
            TimeRemainingColumn(),
            console=console,
        )
        try:
            with progress:
                task = progress.add_task(annotator.key, total=len(pending))
                for fullname in pending:
                    record = db.get_record(fullname)
                    if record is None:
                        stats["skipped"] += 1
                        progress.advance(task)
                        continue
                    block, _ = get_or_build_context(db, record, cfg.context, PROMPT_VERSION)
                    prompt = render_user_prompt(
                        block, str(record["record_type"]), str(record["text"])
                    )
                    try:
                        result = client.annotate(annotator, SYSTEM_PROMPT, prompt, cfg.options)
                    except AnnotationCallError as exc:
                        db.record_failure(fullname, annotator.key, PROMPT_VERSION, role, str(exc))
                        stats["failed"] += 1
                        progress.advance(task)
                        continue
                    inserted = db.insert_llm_annotation(
                        _annotation_row(
                            fullname,
                            annotator,
                            role,
                            result,
                            endpoint_url=cfg.endpoint_url(annotator.endpoint),
                            model_digest=model_digest,
                            ollama_version=ollama_version,
                        )
                    )
                    stats["done" if inserted else "skipped"] += 1
                    progress.advance(task)
        except KeyboardInterrupt:
            console.print(
                f"\n[yellow]Interrupted.[/yellow] {stats['done']} stored this session — "
                f"re-run the same command to resume."
            )
            return stats

        console.print(
            f"[green]Done[/green] {annotator.key} ({role}): "
            f"{stats['done']} stored, {stats['failed']} failed, {stats['skipped']} skipped"
        )
        return stats
