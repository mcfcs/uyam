"""One-command annotation chain — `uyam annotate pipeline` / Streamlit "Start ALL".

    index -> select -> language ID
      -> remote annotator lane(s) start            (24 GB box, concurrent)
      -> transformer sentiment on the target set   (local GPU, before any local LLM)
      -> local annotator lane                      (local GPU, least-done model first)
      -> wait for every lane
      -> aggregate -> adjudicate -> aggregate -> gold sample

Every annotator works through the SAME N-item target set (see
AnnotationDatabase.target_items) and stops once it has N done, so the run
ends with N items carrying every annotator's vote. Lanes are detached child
jobs (uyam.annotate.jobs) with their own logs; the orchestrator is normally a
job as well, so the whole tree can be stopped from the UI.

Everything is idempotent: re-running the command resumes wherever it stopped.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from rich.console import Console
from rich.table import Table

from uyam.annotate import jobs
from uyam.annotate.config import AnnotationConfig
from uyam.annotate.db import AnnotationDatabase
from uyam.annotate.runner import order_least_done_first

logger = logging.getLogger(__name__)
console = Console()

PIPELINE_JOB = "pipeline"


class PipelineError(RuntimeError):
    """A lane or pass failed; the chain stopped and can be resumed by re-running."""


def lane_job_name(endpoint: str) -> str:
    return f"run-all-{endpoint}"


@dataclass
class JobBackend:
    """How the orchestrator starts/waits on child jobs (swappable in tests)."""

    start: Callable[..., int] = field(default=jobs.start_job)
    wait: Callable[..., int | None] = field(default=jobs.wait_job)
    is_running: Callable[[str], bool] = field(default=jobs.is_running)


def _phase(title: str) -> None:
    console.rule(f"[bold]{datetime.now().strftime('%H:%M:%S')}  {title}[/bold]")


def print_target_progress(db: AnnotationDatabase, cfg: AnnotationConfig, target: int) -> dict:
    keys = [a.key for a in cfg.annotators]
    prog = db.target_progress(cfg.prompt_version, target, keys)
    table = Table(
        title=f"Target set: {prog['in_target']} of {prog['target']} items "
        f"(eligible corpus caps it) — {prog['complete']} carry every annotator's vote",
        header_style="bold",
    )
    table.add_column("Annotator")
    table.add_column("Done", justify="right")
    table.add_column("Failed", justify="right")
    table.add_column("Pending", justify="right")
    for key in keys:
        m = prog["models"][key]
        table.add_row(key, str(m["done"]), str(m["failed"]), str(m["pending"]))
    console.print(table)
    return prog


def _lane_args(keys: list[str], target: int, retry_failed: bool) -> list[str]:
    args = ["run"]
    for key in keys:
        args.extend(["--annotator", key])
    args.extend(["--target", str(target)])
    if retry_failed:
        args.append("--retry-failed")
    return args


def _start_or_adopt(backend: JobBackend, name: str, args: list[str]) -> None:
    if backend.is_running(name):
        console.print(f"[yellow]{name}[/yellow] is already running — adopting it.")
        return
    pid = backend.start(name, args, parent=PIPELINE_JOB)
    console.print(f"started [bold]{name}[/bold] (pid {pid}): annotate {' '.join(args)}")


def _wait(backend: JobBackend, name: str, poll_seconds: float) -> int | None:
    code = backend.wait(name, poll_seconds=poll_seconds)
    outcome = "finished" if code in (0, None) else f"[red]failed (exit {code})[/red]"
    console.print(f"{name} {outcome}")
    return code


def run_pipeline(
    cfg: AnnotationConfig,
    *,
    target: int | None = None,
    skip_prep: bool = False,
    skip_sentiment: bool = False,
    skip_adjudicate: bool = False,
    skip_gold: bool = False,
    retry_failed: bool = False,
    poll_seconds: float = 5.0,
    backend: JobBackend | None = None,
) -> dict[str, Any]:
    """Run the whole chain to `target` items per annotator. Returns a summary."""
    target = int(target or cfg.pipeline.target_items)
    if target <= 0:
        raise ValueError("target must be a positive integer")
    backend = backend or JobBackend()
    t0 = time.time()
    summary: dict[str, Any] = {
        "target": target,
        "lanes": {},
        "sentiment_exit": None,
        "adjudicate_exit": None,
        "progress": None,
    }

    # ------------------------------------------------------------------ 1
    if skip_prep:
        _phase("1/6 prepare — skipped (--skip-prep)")
    else:
        _phase("1/6 prepare: index → select → language ID")
        from uyam.annotate.candidates import select_candidates
        from uyam.annotate.corpus import index_corpus
        from uyam.annotate.lid import run_lid

        with AnnotationDatabase(cfg.db_path) as db:
            stats = index_corpus(
                db, cfg.data_dir, extra_bot_authors=cfg.candidate_filters.exclude_authors
            )
            console.print(f"indexed {stats['records']} records from {stats['files']} files")
            select_candidates(db, cfg.candidate_filters)
            console.print(f"eligible candidates: {db.eligible_count()}")
            lid_stats = run_lid(db, cfg.lid)
            console.print(f"language ID: {lid_stats['processed']} new items")

    # ------------------------------------------------------------------ 2
    _phase(f"2/6 plan: every annotator to {target} items of one shared target set")
    endpoints = list(dict.fromkeys(a.endpoint for a in cfg.annotators))
    with AnnotationDatabase(cfg.db_path) as db:
        print_target_progress(db, cfg, target)
        lanes: dict[str, list[str]] = {}
        for ep in endpoints:
            ep_keys = [a.key for a in cfg.annotators if a.endpoint == ep]
            lanes[ep] = order_least_done_first(db, ep_keys, cfg.prompt_version, target)
    local_eps = [ep for ep in endpoints if cfg.is_local_endpoint(ep)]
    remote_eps = [ep for ep in endpoints if ep not in local_eps]
    for ep in endpoints:
        kind = "local GPU, sequential" if ep in local_eps else "remote, concurrent"
        console.print(f"lane [bold]{ep}[/bold] ({kind}): {' → '.join(lanes[ep])}")

    # Remote lanes do not touch the local GPU: start them right away.
    for ep in remote_eps:
        _start_or_adopt(backend, lane_job_name(ep), _lane_args(lanes[ep], target, retry_failed))

    # ------------------------------------------------------------------ 3
    if skip_sentiment:
        _phase("3/6 transformer sentiment — skipped (--skip-sentiment)")
    else:
        _phase("3/6 transformer sentiment on the target set (local GPU)")
        local_llm_jobs = [lane_job_name(ep) for ep in local_eps] + [
            f"run-{a.key}" for a in cfg.annotators if cfg.is_local_endpoint(a.endpoint)
        ]
        for name in local_llm_jobs:
            if backend.is_running(name):
                console.print(f"waiting for {name} to release the GPU…")
                _wait(backend, name, poll_seconds)
        from uyam.annotate.ollama_client import unload_model

        for ann in cfg.annotators:
            if cfg.is_local_endpoint(ann.endpoint):
                unload_model(cfg.endpoint_url(ann.endpoint), ann.model)
        _start_or_adopt(backend, "sentiment", ["sentiment", "--target", str(target)])
        code = _wait(backend, "sentiment", poll_seconds)
        summary["sentiment_exit"] = code
        if code not in (0, None):
            console.print(
                "[yellow]sentiment failed[/yellow] — continuing with the LLM passes; "
                "run `uyam annotate sentiment --target 0` later (see the sentiment job log)."
            )

    # ------------------------------------------------------------------ 4
    for ep in local_eps:
        _phase(f"4/6 local annotators on {ep}: {' → '.join(lanes[ep])}")
        name = lane_job_name(ep)
        _start_or_adopt(backend, name, _lane_args(lanes[ep], target, retry_failed))
        summary["lanes"][ep] = _wait(backend, name, poll_seconds)
    if not local_eps:
        _phase("4/6 local annotators — none configured")

    for ep in remote_eps:
        name = lane_job_name(ep)
        if backend.is_running(name):
            console.print(f"waiting for {name}…")
        summary["lanes"][ep] = _wait(backend, name, poll_seconds)

    with AnnotationDatabase(cfg.db_path) as db:
        summary["progress"] = print_target_progress(db, cfg, target)
    failed_lanes = [ep for ep, code in summary["lanes"].items() if code not in (0, None)]
    if failed_lanes:
        raise PipelineError(
            f"annotator lane(s) failed: {', '.join(failed_lanes)} — check the run-all-* job "
            "logs, then re-run `uyam annotate pipeline` to resume"
        )

    # ------------------------------------------------------------------ 5
    _phase("5/6 aggregate → adjudicate → aggregate")
    from uyam.annotate.aggregate import run_aggregation

    agg = run_aggregation(cfg, cfg.prompt_version)
    console.print(
        f"aggregated {agg['items']} items: {agg['unanimous']} unanimous, "
        f"{agg['majority']} majority, {agg['escalated_pending']} escalated"
    )
    if skip_adjudicate:
        console.print("adjudication skipped (--skip-adjudicate)")
    else:
        with AnnotationDatabase(cfg.db_path) as db:
            escalated = db.pending_adjudicator_count(cfg.adjudicator.key, cfg.prompt_version)
        if escalated:
            console.print(
                f"{escalated} escalated items → {cfg.adjudicator.model} on "
                f"{cfg.adjudicator.endpoint}"
            )
            args = ["adjudicate"] + (["--retry-failed"] if retry_failed else [])
            _start_or_adopt(backend, "adjudicate", args)
            code = _wait(backend, "adjudicate", poll_seconds)
            summary["adjudicate_exit"] = code
            if code not in (0, None):
                raise PipelineError(
                    "adjudication failed — check the adjudicate job log, then re-run "
                    "`uyam annotate pipeline --skip-prep --skip-sentiment` to resume"
                )
            agg = run_aggregation(cfg, cfg.prompt_version)
            console.print(
                f"re-aggregated: {agg['adjudicated']} adjudicated, "
                f"{agg['queued_low_confidence']} sent to the human queue"
            )
        else:
            console.print("no escalated items — adjudication not needed")

    # ------------------------------------------------------------------ 6
    if skip_gold:
        _phase("6/6 gold sample — skipped (--skip-gold)")
    else:
        _phase("6/6 gold sample for human validation")
        with AnnotationDatabase(cfg.db_path) as db:
            existing = int(
                db.conn.execute(
                    "SELECT COUNT(*) AS n FROM review_queue WHERE reason = 'gold'"
                ).fetchone()["n"]
                or 0
            )
        if existing:
            console.print(f"gold sample already drawn ({existing} items) — not re-sampling")
        else:
            from uyam.annotate.review import sample_gold_subset

            gold = sample_gold_subset(cfg, cfg.prompt_version)
            console.print(f"sampled {gold['sampled']} gold items across {gold['strata']} strata")

    elapsed = time.time() - t0
    _phase(f"done in {elapsed / 3600:.1f} h")
    console.print(
        "Next: label the gold subset in the Streamlit [bold]Annotation Review[/bold] tab, "
        "then run [bold]uyam annotate aggregate[/bold] and "
        "[bold]uyam annotate export --version v1[/bold]."
    )
    summary["elapsed_seconds"] = round(elapsed, 1)
    return summary
