"""`uyam annotate` sub-commands — the annotation pipeline in pass order:

  index -> select -> lid -> sentiment -> run -> aggregate -> adjudicate
  -> (Streamlit review) -> aggregate -> export

Plus: status, smoke, gold-sample, and `pipeline` (the whole chain to a
target count in one command).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from uyam.annotate.config import AnnotationConfig, load_annotation_config
from uyam.annotate.db import AnnotationDatabase
from uyam.logging_config import configure_logging

# Reddit text is full of emoji; Windows consoles (cp1252 pipes) must degrade
# gracefully instead of crashing dry-run/status output.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(errors="replace")

annotate_app = typer.Typer(
    name="annotate",
    help="LLM annotation pipeline: sentiment + sarcasm labels for the collected corpus.",
    no_args_is_help=True,
)
console = Console()

_CONFIG_OPT = typer.Option(None, help="Path to annotation.yaml")
_TARGET_HELP = (
    "Work through the shared N-item target set and stop at N done "
    "(0 = pipeline.target_items from annotation.yaml)."
)


def _resolve_target(cfg: AnnotationConfig, target: int | None) -> int | None:
    """None -> no target (legacy behaviour); <= 0 -> the configured default."""
    if target is None:
        return None
    return target if target > 0 else cfg.pipeline.target_items


@annotate_app.command()
def index(
    config_path: Path | None = _CONFIG_OPT,
    log_level: str = typer.Option("INFO"),
) -> None:
    """Index raw JSONL (data/raw/**) into the annotation database."""
    configure_logging(log_level)
    cfg = load_annotation_config(config_path)
    from uyam.annotate.corpus import index_corpus

    with AnnotationDatabase(cfg.db_path) as db:
        stats = index_corpus(
            db, cfg.data_dir, extra_bot_authors=cfg.candidate_filters.exclude_authors
        )
        counts = db.corpus_counts()
    console.print(
        f"[green]Indexed[/green] {stats['records']} records from {stats['files']} files "
        f"({stats['skipped']} skipped) — corpus now {counts['total']} "
        f"({counts['submissions']} submissions, {counts['comments']} comments)"
    )


@annotate_app.command()
def select(
    config_path: Path | None = _CONFIG_OPT,
    log_level: str = typer.Option("INFO"),
) -> None:
    """Apply candidate filters (bots, deleted, link posts, too short)."""
    configure_logging(log_level)
    cfg = load_annotation_config(config_path)
    from uyam.annotate.candidates import select_candidates

    with AnnotationDatabase(cfg.db_path) as db:
        select_candidates(db, cfg.candidate_filters)
        counts = db.candidate_counts()
    table = Table(title="Candidate selection", show_header=True, header_style="bold")
    table.add_column("Outcome")
    table.add_column("Count", justify="right")
    for reason, n in counts.items():
        table.add_row(reason, str(n))
    console.print(table)


@annotate_app.command()
def lid(
    force: bool = typer.Option(False, help="Recompute all LID results"),
    config_path: Path | None = _CONFIG_OPT,
    log_level: str = typer.Option("INFO"),
) -> None:
    """Two-stage fastText language identification over eligible candidates."""
    configure_logging(log_level)
    cfg = load_annotation_config(config_path)
    from uyam.annotate.lid import run_lid

    with AnnotationDatabase(cfg.db_path) as db:
        stats = run_lid(db, cfg.lid, force=force)
    console.print(f"[green]LID[/green] processed {stats['processed']} items")


@annotate_app.command()
def sentiment(
    device: str | None = typer.Option(None, help="auto|cpu|cuda (overrides annotation.yaml)"),
    batch_size: int | None = typer.Option(None),
    force: bool = typer.Option(False, help="Recompute all sentiment results"),
    target: int | None = typer.Option(
        None, "--target", help="Only the pipeline target set. " + _TARGET_HELP
    ),
    config_path: Path | None = _CONFIG_OPT,
    log_level: str = typer.Option("INFO"),
) -> None:
    """Pass 1: transformer literal-sentiment over eligible candidates.

    Run BEFORE local Ollama passes — transformers and Ollama fight over the 8 GB GPU.
    """
    configure_logging(log_level)
    cfg = load_annotation_config(config_path)
    if device is not None:
        cfg.tx_sentiment.device = device
    if batch_size is not None:
        cfg.tx_sentiment.batch_size = batch_size
    from uyam.annotate.sentiment_tx import run_tx_sentiment

    with AnnotationDatabase(cfg.db_path) as db:
        stats = run_tx_sentiment(
            db,
            cfg.tx_sentiment,
            force=force,
            target=_resolve_target(cfg, target),
            prompt_version=cfg.prompt_version,
        )
    console.print(
        f"[green]Sentiment[/green] processed {stats['processed']} items"
        + (f" on {stats['device']}" if stats.get("device") else "")
    )


@annotate_app.command()
def run(
    annotator: list[str] = typer.Option(
        [], "--annotator", help="Annotator key from annotation.yaml (repeatable). Default: all."
    ),
    limit: int | None = typer.Option(None, help="Stop after N items per annotator THIS run"),
    target: int | None = typer.Option(
        None,
        "--target",
        help=_TARGET_HELP + " With several annotators the least-done one runs first.",
    ),
    dry_run: bool = typer.Option(False, help="Render prompts, make no calls"),
    only_fullname: str | None = typer.Option(None, help="Annotate a single item (debug)"),
    retry_failed: bool = typer.Option(False, help="Clear recorded failures and retry them"),
    config_path: Path | None = _CONFIG_OPT,
    log_level: str = typer.Option("INFO"),
) -> None:
    """LLM annotator pass. Run one annotator per machine; safe to interrupt and resume."""
    configure_logging(log_level)
    cfg = load_annotation_config(config_path)
    from uyam.annotate.runner import order_least_done_first, run_annotator

    keys = annotator or [a.key for a in cfg.annotators]
    resolved_target = _resolve_target(cfg, target)
    if resolved_target is not None and len(keys) > 1:
        with AnnotationDatabase(cfg.db_path) as db:
            keys = order_least_done_first(db, keys, cfg.prompt_version, resolved_target)
        console.print(f"Order (least done first): {' -> '.join(keys)}")
    for key in keys:
        run_annotator(
            cfg,
            key,
            limit=limit,
            target=resolved_target,
            dry_run=dry_run,
            only_fullname=only_fullname,
            retry_failed=retry_failed,
        )


@annotate_app.command()
def pipeline(
    target: int | None = typer.Option(
        None,
        "--target",
        help="Items per annotator (default: pipeline.target_items in annotation.yaml).",
    ),
    skip_prep: bool = typer.Option(False, help="Skip index / select / language ID"),
    skip_sentiment: bool = typer.Option(False, help="Skip the GPU transformer sentiment pass"),
    skip_adjudicate: bool = typer.Option(False, help="Stop after aggregation (no adjudicator)"),
    skip_gold: bool = typer.Option(False, help="Do not draw the gold sample"),
    retry_failed: bool = typer.Option(False, help="Retry previously failed items in every pass"),
    poll_seconds: float = typer.Option(5.0, help="How often to poll child jobs"),
    config_path: Path | None = _CONFIG_OPT,
    log_level: str = typer.Option("INFO"),
) -> None:
    """Everything in one go, to a target count.

    index -> select -> LID -> sentiment (target set) -> annotators (remote lane
    concurrently, local lane least-done-first, each to N) -> aggregate ->
    adjudicate -> aggregate -> gold sample. Resumable: re-run to continue.
    """
    configure_logging(log_level)
    cfg = load_annotation_config(config_path)
    from uyam.annotate.pipeline import PipelineError, run_pipeline

    try:
        run_pipeline(
            cfg,
            target=_resolve_target(cfg, target if target is not None else 0),
            skip_prep=skip_prep,
            skip_sentiment=skip_sentiment,
            skip_adjudicate=skip_adjudicate,
            skip_gold=skip_gold,
            retry_failed=retry_failed,
            poll_seconds=poll_seconds,
        )
    except PipelineError as exc:
        console.print(f"[red]Pipeline stopped:[/red] {exc}")
        raise typer.Exit(1) from exc


@annotate_app.command()
def aggregate(
    report: bool = typer.Option(False, help="Also print the agreement report"),
    config_path: Path | None = _CONFIG_OPT,
    log_level: str = typer.Option("INFO"),
) -> None:
    """Majority vote + escalation queue. Run before AND after adjudication/review."""
    configure_logging(log_level)
    cfg = load_annotation_config(config_path)
    from uyam.annotate.aggregate import compute_agreement, run_aggregation

    stats = run_aggregation(cfg, cfg.prompt_version)
    table = Table(title="Aggregation", show_header=True, header_style="bold")
    table.add_column("Outcome")
    table.add_column("Count", justify="right")
    for key, n in stats.items():
        table.add_row(key, str(n))
    console.print(table)
    if report:
        console.print_json(json.dumps(compute_agreement(cfg, cfg.prompt_version), indent=2))


@annotate_app.command()
def adjudicate(
    limit: int | None = typer.Option(None),
    dry_run: bool = typer.Option(False),
    retry_failed: bool = typer.Option(False),
    config_path: Path | None = _CONFIG_OPT,
    log_level: str = typer.Option("INFO"),
) -> None:
    """Blind re-annotation of escalated items by the adjudicator model (remote box)."""
    configure_logging(log_level)
    cfg = load_annotation_config(config_path)
    from uyam.annotate.adjudicate import run_adjudication

    run_adjudication(cfg, limit=limit, dry_run=dry_run, retry_failed=retry_failed)
    console.print("Re-run [bold]uyam annotate aggregate[/bold] to apply adjudicated labels.")


@annotate_app.command(name="gold-sample")
def gold_sample(
    size: int | None = typer.Option(None, help="Sample size (default: annotation.yaml gold_size)"),
    seed: int = typer.Option(7),
    config_path: Path | None = _CONFIG_OPT,
    log_level: str = typer.Option("INFO"),
) -> None:
    """Draw the stratified human-validation gold subset into the review queue."""
    configure_logging(log_level)
    cfg = load_annotation_config(config_path)
    from uyam.annotate.review import sample_gold_subset

    stats = sample_gold_subset(cfg, cfg.prompt_version, size=size, seed=seed)
    console.print(
        f"[green]Gold sample[/green] {stats['sampled']} items across {stats['strata']} strata "
        f"(population {stats['population']}) — label them in the Streamlit 'Annotation Review' tab"
    )


@annotate_app.command()
def status(
    config_path: Path | None = _CONFIG_OPT,
) -> None:
    """Per-model progress, failures, and pending counts."""
    cfg = load_annotation_config(config_path)
    with AnnotationDatabase(cfg.db_path) as db:
        corpus = db.corpus_counts()
        candidates = db.candidate_counts()
        eligible = candidates.get("eligible", 0)
        progress = db.model_progress(cfg.prompt_version)
        failures = {
            (f["model_key"], f["role"]): f["failed"]
            for f in db.failure_counts(cfg.prompt_version)
        }
        lid_missing = db.missing_lid_count()
        tx_missing = db.missing_tx_sentiment_count()
        queue = db.review_queue_items()
        target_prog = db.target_progress(
            cfg.prompt_version, cfg.pipeline.target_items, [a.key for a in cfg.annotators]
        )

    console.print(
        f"Corpus: {corpus['total']} records — eligible candidates: {eligible} "
        f"(LID missing {lid_missing}, sentiment missing {tx_missing})"
    )
    table = Table(title=f"LLM progress (prompt {cfg.prompt_version})", header_style="bold")
    table.add_column("Model")
    table.add_column("Role")
    table.add_column("Done", justify="right")
    table.add_column("Failed", justify="right")
    table.add_column("Pending", justify="right")
    table.add_column("Avg s/item", justify="right")
    seen_keys = set()
    for row in progress:
        key, role = str(row["model_key"]), str(row["role"])
        seen_keys.add(key)
        done = int(row["done"])
        pending = max(0, eligible - done) if role == "annotator" else "-"
        avg = f"{(row['avg_ms'] or 0) / 1000:.1f}" if row["avg_ms"] else "-"
        table.add_row(key, role, str(done), str(failures.get((key, role), 0)), str(pending), avg)
    for ann in cfg.annotators:
        if ann.key not in seen_keys:
            table.add_row(ann.key, "annotator", "0", "0", str(eligible), "-")
    console.print(table)

    ttable = Table(
        title=f"Pipeline target: {target_prog['in_target']} of {target_prog['target']} items — "
        f"{target_prog['complete']} carry every annotator's vote",
        header_style="bold",
    )
    ttable.add_column("Annotator")
    ttable.add_column("Done", justify="right")
    ttable.add_column("Failed", justify="right")
    ttable.add_column("Pending", justify="right")
    for key, m in target_prog["models"].items():
        ttable.add_row(key, str(m["done"]), str(m["failed"]), str(m["pending"]))
    console.print(ttable)
    if queue:
        gold = sum(1 for q in queue if q["reason"] == "gold")
        console.print(
            f"Human review pending: {gold} gold, {len(queue) - gold} low-confidence "
            "(Streamlit 'Annotation Review' tab)"
        )


@annotate_app.command()
def smoke(
    endpoint: str | None = typer.Option(
        None, help="Endpoint name from annotation.yaml (default: all)"
    ),
    config_path: Path | None = _CONFIG_OPT,
    log_level: str = typer.Option("INFO"),
) -> None:
    """Verify endpoints and models: version, tags, one tiny schema-constrained chat each.

    Catches the Qwen3 thinking/format empty-content bug and template problems
    before a multi-day run.
    """
    configure_logging(log_level)
    cfg = load_annotation_config(config_path)
    from uyam.annotate.ollama_client import OllamaClient
    from uyam.annotate.prompts import SYSTEM_PROMPT, render_user_prompt

    endpoints = {endpoint: cfg.endpoint_url(endpoint)} if endpoint else cfg.endpoints
    failures = 0
    for name, url in endpoints.items():
        console.rule(f"endpoint {name} — {url}")
        client = OllamaClient(url, cfg.options.timeout_seconds)
        version = client.server_version()
        if version is None:
            console.print(f"[red]UNREACHABLE[/red] {url}")
            failures += 1
            continue
        console.print(f"Ollama version: [bold]{version}[/bold]")
        try:
            available = client.list_model_names()
            console.print(f"Models: {', '.join(available) or '(none)'}")
        except Exception as exc:  # noqa: BLE001
            console.print(f"[red]tags failed:[/red] {exc}")
            available = []
            failures += 1

        for ann in [*cfg.annotators, cfg.adjudicator]:
            if ann.endpoint != name:
                continue
            template = client.model_template(ann.model)
            if template:
                console.print(f"[dim]{ann.model} template head: {template[:120]!r}[/dim]")
            prompt = render_user_prompt(
                "=== THREAD CONTEXT ===\n[SUBMISSION r/CasualPH] Nanalo ako sa raffle sa work!",
                "comment",
                "Wow, edi ikaw na. Sana all swerte 🙄",
            )
            try:
                result = client.annotate(ann, SYSTEM_PROMPT, prompt, cfg.options)
                console.print(
                    f"[green]OK[/green] {ann.key} ({ann.model}): "
                    f"sarcastic={result.parsed.sarcastic} "
                    f"language={result.parsed.language} "
                    f"intended={result.parsed.intended_sentiment} "
                    f"({result.attempts} attempt(s))"
                )
            except Exception as exc:  # noqa: BLE001
                console.print(f"[red]FAIL[/red] {ann.key} ({ann.model}): {exc}")
                failures += 1
    if failures:
        raise typer.Exit(1)
    console.print("[green]Smoke test passed[/green]")


@annotate_app.command()
def export(
    version: str = typer.Option("v1", help="Dataset version tag (dataset-<version>.jsonl)"),
    formats: str = typer.Option("jsonl,parquet", help="Comma-separated: jsonl,parquet"),
    include_unresolved: bool = typer.Option(
        False, help="Also export items still awaiting adjudication/review"
    ),
    config_path: Path | None = _CONFIG_OPT,
    log_level: str = typer.Option("INFO"),
) -> None:
    """Write the final labeled dataset + corpus dump + dataset card for leische."""
    configure_logging(log_level)
    cfg = load_annotation_config(config_path)
    from uyam.annotate.export import run_export

    result = run_export(
        cfg,
        cfg.prompt_version,
        dataset_version=version,
        formats=tuple(f.strip() for f in formats.split(",") if f.strip()),
        include_unresolved=include_unresolved,
    )
    console.print(
        f"[green]Exported[/green] {result['rows']} rows "
        f"({result['skipped_unresolved']} unresolved skipped)\n"
        f"  dataset: {result['jsonl']}\n"
        + (f"  parquet: {result['parquet']}\n" if result["parquet"] else "")
        + f"  corpus:  {result['corpus']}\n  card:    {result['card']}"
    )
