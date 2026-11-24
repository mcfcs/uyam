"""`uyam annotate` sub-commands — the annotation pipeline in pass order:

  index -> select -> lid -> sentiment -> run -> aggregate -> adjudicate
  -> (Streamlit review) -> aggregate -> export

Plus: status, smoke, gold-sample, `pipeline` (the whole chain to a target
count in one command), and the evaluation-only `evaluate` / `eval-report`
(compare a model against the gold labels without touching the dataset).
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
    """Apply candidate filters (bots, deleted, link posts, too short, phatic)."""
    configure_logging(log_level)
    cfg = load_annotation_config(config_path)
    from uyam.annotate.candidates import select_candidates

    with AnnotationDatabase(cfg.db_path) as db:
        stats = select_candidates(db, cfg.candidate_filters)
        counts = db.candidate_counts()
    table = Table(title="Candidate selection", show_header=True, header_style="bold")
    table.add_column("Outcome")
    table.add_column("Count", justify="right")
    for reason, n in counts.items():
        table.add_row(reason, str(n))
    console.print(table)
    if stats["phatic_flagged"]:
        console.print(
            f"{stats['phatic_flagged']} already-labeled phatic items kept and flagged "
            f"(labels unchanged); {stats['phatic_excluded']} unlabeled ones excluded"
        )


@annotate_app.command()
def lid(
    force: bool = typer.Option(False, help="Recompute all LID results"),
    target: int | None = typer.Option(
        None, "--target", help="Only the pipeline target set. " + _TARGET_HELP
    ),
    refresh_rule: bool = typer.Option(
        False, "--refresh-rule", help="Only (re)compute language_rule (GUIDELINE L2-L4b)"
    ),
    config_path: Path | None = _CONFIG_OPT,
    log_level: str = typer.Option("INFO"),
) -> None:
    """Two-stage fastText language identification over eligible candidates."""
    configure_logging(log_level)
    cfg = load_annotation_config(config_path)
    from uyam.annotate.lid import run_lid

    with AnnotationDatabase(cfg.db_path) as db:
        stats = run_lid(
            db,
            cfg.lid,
            force=force,
            target=_resolve_target(cfg, target),
            prompt_version=cfg.prompt_version,
            refresh_rule=refresh_rule,
        )
    what = "language_rule for" if refresh_rule else "LID"
    console.print(f"[green]{what}[/green] processed {stats['processed']} items")


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


@annotate_app.command()
def evaluate(
    model: str = typer.Option(..., "--model", help="Evaluator key from annotation.yaml"),
    items: str = typer.Option(
        "gold",
        "--items",
        help="'gold' (every gold-sample item, labeled or not) or a txt file of reddit_fullnames",
    ),
    limit: int | None = typer.Option(None, help="Stop after N items THIS run"),
    retry_failed: bool = typer.Option(False, help="Clear recorded failures and retry them"),
    config_path: Path | None = _CONFIG_OPT,
    log_level: str = typer.Option("INFO"),
) -> None:
    """Evaluation-only model pass: votes go to eval_annotations, never to the dataset.

    Same prompt and persisted thread context as the base annotators; items with
    no context snapshot are skipped (and counted), not rebuilt. Resumable.
    """
    configure_logging(log_level)
    cfg = load_annotation_config(config_path)
    from uyam.annotate.evaluation import run_evaluator

    try:
        run_evaluator(cfg, model, items=items, limit=limit, retry_failed=retry_failed)
    except (KeyError, FileNotFoundError) as exc:
        console.print(f"[red]{exc.args[0] if exc.args else exc}[/red]")
        raise typer.Exit(1) from exc


@annotate_app.command(name="eval-report")
def eval_report(
    out: Path | None = typer.Option(None, "--out", help="Also write the report as Markdown"),
    config_path: Path | None = _CONFIG_OPT,
) -> None:
    """Sarcasm accuracy / F1 / Cohen's kappa vs the human gold labels, per source.

    Rows: each base annotator, their majority vote, the shipped final label, and
    every evaluator; plus each evaluator's kappa against the shipped final label.
    """
    cfg = load_annotation_config(config_path)
    from uyam.annotate.evaluation import build_eval_report, fmt_metric, render_eval_markdown

    report = build_eval_report(cfg, cfg.prompt_version)
    table = Table(
        title=f"Sarcasm vs human gold — {report['n_gold']} items "
        f"({report['n_gold_sarcastic']} sarcastic), prompt {report['prompt_version']}",
        header_style="bold",
    )
    for col in ("Source", "Kind"):
        table.add_column(col)
    for col in ("n", "Accuracy", "F1 (sarcastic)", "kappa vs human"):
        table.add_column(col, justify="right")
    for r in report["vs_human"]:
        table.add_row(
            r["source"],
            r["kind"],
            str(r["n"]),
            fmt_metric(r["accuracy"]),
            fmt_metric(r["f1_sarcastic"]),
            fmt_metric(r["kappa"]),
        )
    console.print(table)

    ftable = Table(title="Evaluators vs the shipped final label", header_style="bold")
    ftable.add_column("Evaluator")
    for col in ("Labeled", "n with final", "Agreement", "kappa"):
        ftable.add_column(col, justify="right")
    for r in report["evaluators_vs_final"]:
        ftable.add_row(
            r["evaluator"],
            str(r["n_labeled"]),
            str(r["n"]),
            fmt_metric(r["agreement"]),
            fmt_metric(r["kappa"]),
        )
    console.print(ftable)
    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(render_eval_markdown(report), encoding="utf-8")
        console.print(f"[green]Wrote[/green] {out}")


@annotate_app.command(name="load-assist")
def load_assist(
    files: list[Path] = typer.Argument(..., help="Suggestion JSONL file(s)"),
    source: str = typer.Option(..., help="Who produced them, e.g. claude-code:claude-opus-5-5"),
    guideline_version: str = typer.Option("guideline-v1-draft", help="docs/GUIDELINE.md version"),
    config_path: Path | None = _CONFIG_OPT,
) -> None:
    """Load reviewer-assist suggestions (shown only after a blind first-pass label)."""
    cfg = load_annotation_config(config_path)
    from uyam.annotate.assist import AssistValidationError, load_assist_suggestions

    with AnnotationDatabase(cfg.db_path) as db:
        try:
            stats = load_assist_suggestions(
                db, files, source=source, guideline_version=guideline_version
            )
        except AssistValidationError as exc:
            typer.echo(f"Error: {exc} — nothing was loaded.", err=True)
            raise typer.Exit(1) from exc
    console.print(
        # No verdict counts: the reviewer labels these items blind.
        f"[green]Loaded[/green] {stats['loaded']} suggestions from {stats['files']} file(s)."
    )


@annotate_app.command(name="media-context")
def media_context_cmd(
    version: str = typer.Option(..., "--version", help="Dataset version the file goes with"),
    config_path: Path | None = _CONFIG_OPT,
) -> None:
    """Write media_context-<version>.jsonl for an already exported dataset (read-only).

    One line per labeled item (reddit_fullname + media_context), so the model
    side can slice an existing export by post type without a re-export.
    """
    cfg = load_annotation_config(config_path)
    from uyam.annotate.export import _media_context
    from uyam.annotate.media import load_post_urls, summarize

    post_urls = load_post_urls(cfg.data_dir)
    out = cfg.export.out_dir / f"media_context-{version}.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    with AnnotationDatabase(cfg.db_path) as db, out.open("w", encoding="utf-8") as fh:
        for agg in db.all_aggregates(cfg.prompt_version):
            if agg.get("sarcastic_final") is None:
                continue
            record = db.get_record(str(agg["reddit_fullname"]))
            if record is None:
                continue
            ctx_row = db.get_context(str(agg["reddit_fullname"]))
            ctx = json.loads(str(ctx_row["context_json"])) if ctx_row else {}
            flags = _media_context(db, record, ctx, post_urls)
            row = {"reddit_fullname": agg["reddit_fullname"], "media_context": flags}
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            rows.append(row | {"labels": {"sarcastic": bool(agg["sarcastic_final"])}})
    summary = summarize(rows)
    console.print(f"[green]Wrote[/green] {len(rows)} rows to {out}")
    for kind, v in summary["by_post_type"].items():
        console.print(f"  {kind:8s} {v['rows']:6d} rows, sarcastic {v['sarcastic_rate']:.1%}")
    console.print(
        f"  text-only slice: {summary['text_only_rows']} rows, "
        f"{summary['text_only_sarcastic']} sarcastic"
    )


@annotate_app.command(name="fork")
def fork(
    to: Path = typer.Option(..., "--to", help="New database file (must not exist)"),
    keep: list[str] = typer.Option(
        ..., "--keep", help="Annotator key whose votes carry over (repeat)"
    ),
    extend: list[str] = typer.Option(
        [], "--extend", help="Add N unlabeled items from a month: YYYY-MM:N (repeat)"
    ),
    seed: int = typer.Option(13, help="Seed for the extension sample"),
    config_path: Path | None = _CONFIG_OPT,
) -> None:
    """Fork the annotation DB for a re-annotation (dataset-v3); the source is never changed."""
    cfg = load_annotation_config(config_path)
    from uyam.annotate.fork import fork_for_reannotation, parse_extend

    stats = fork_for_reannotation(
        cfg.db_path,
        to,
        prompt_version=cfg.prompt_version,
        keep_annotators=keep,
        extend=parse_extend(extend),
        seed=seed,
    )
    console.print(
        f"[green]Forked[/green] {cfg.db_path.name} -> {to}: {stats['base']} source-labeled "
        f"items + extension {stats['extension']} = target set {stats['target_total']}. "
        f"Kept votes of {', '.join(stats['kept_annotators'])}; dropped "
        f"{stats['dropped_votes']} other votes and {stats['dropped_aggregates']} aggregates."
    )
    if stats["extension_short"]:
        short = stats["extension_short"]
        console.print(f"[yellow]Short months (not enough items):[/yellow] {short}")


@annotate_app.command(name="fork-subset")
def fork_subset_cmd(
    to: Path = typer.Option(..., "--to", help="New database file (must not exist)"),
    size: int = typer.Option(5000, help="Items in the subset, gold items included"),
    seed: int = typer.Option(13, help="Seed for the stratified sample"),
    config_path: Path | None = _CONFIG_OPT,
) -> None:
    """Fork a stratified subset (all gold + a sample) of a labeled dataset for a pilot (v4)."""
    cfg = load_annotation_config(config_path)
    from uyam.annotate.fork import fork_subset

    stats = fork_subset(
        cfg.db_path, to, base_prompt_version=cfg.prompt_version, size=size, seed=seed
    )
    console.print(
        f"[green]Forked subset[/green] {cfg.db_path.name} -> {to}: {stats['gold']} gold + "
        f"{stats['sampled']} sampled ({stats['sampled_sarcastic']} labeled sarcastic in the "
        f"source) = target set {stats['target_total']}; dropped {stats['dropped_votes']} "
        "votes (the pilot labels from scratch)."
    )


@annotate_app.command(name="import-eval-votes")
def import_eval_votes_cmd(
    source: Path = typer.Option(
        Path("data/db/annotation.sqlite3"), "--from", help="Database holding the eval votes"
    ),
    source_config: Path | None = typer.Option(
        None, "--from-config", help="Config that defines the evaluators (default config)"
    ),
    pair: list[str] = typer.Option(
        ..., "--map", help="annotator=evaluator, e.g. qwen3=qwen3-guide (repeat)"
    ),
    config_path: Path | None = _CONFIG_OPT,
) -> None:
    """Reuse evaluation-only votes as annotator votes when model, prompt and options match."""
    cfg = load_annotation_config(config_path)
    src_cfg = load_annotation_config(source_config)
    from uyam.annotate.evaluation import import_eval_votes, prompt_set

    mapping: dict[str, str] = {}
    for spec in pair:
        ann_key, _, ev_key = spec.partition("=")
        ann, ev = cfg.annotator(ann_key), src_cfg.evaluator(ev_key)
        same = (ann.model, ann.think, ann.merge_system_prompt) == (
            ev.model,
            ev.think,
            ev.merge_system_prompt,
        )
        if not same or prompt_set(ev)[0] != cfg.prompt_version:
            raise typer.BadParameter(f"{spec}: model, settings or prompt version differ")
        mapping[ann_key] = ev_key
    if cfg.options != src_cfg.options:
        raise typer.BadParameter("the two configs use different decoding options")
    stats = import_eval_votes(
        source, cfg.db_path, mapping=mapping, prompt_version=cfg.prompt_version
    )
    console.print(
        f"[green]Imported[/green] {stats['copied']} eval votes into {cfg.db_path.name} "
        f"({stats['already_present']} already there)."
    )


@annotate_app.command(name="sync-gold")
def sync_gold_cmd(
    source: Path = typer.Option(
        Path("data/db/annotation.sqlite3"), "--from", help="Database Gio labels gold in"
    ),
    config_path: Path | None = _CONFIG_OPT,
) -> None:
    """Copy gold reviews, queue state and assist suggestions into a fork (back it up first)."""
    cfg = load_annotation_config(config_path)
    if source.resolve() == cfg.db_path.resolve():
        raise typer.BadParameter("--from is the configured database itself")
    from uyam.annotate.fork import sync_gold

    stats = sync_gold(source, cfg.db_path)
    console.print(
        f"[green]Synced gold[/green] {source.name} -> {cfg.db_path.name}: "
        f"{stats['reviews_added']} reviews added, {stats['reviews_updated']} updated, "
        f"{stats['reviews_unchanged']} unchanged; {stats['gold_completed']} gold items done; "
        f"{stats['assist_copied']} assist suggestions copied."
    )
    if stats["dst_only_reviews"]:
        console.print(
            f"[yellow]{stats['dst_only_reviews']} gold reviews exist only in "
            f"{cfg.db_path.name}[/yellow] (outside the shared gold queue; left as is)"
        )


@annotate_app.command(name="gold-sample")
def gold_sample(
    size: int | None = typer.Option(None, help="Sample size (default: annotation.yaml gold_size)"),
    seed: int = typer.Option(7),
    redraw: bool = typer.Option(
        False,
        "--redraw",
        help="Replace the gold items nobody has labeled yet; labeled ones are always kept.",
    ),
    config_path: Path | None = _CONFIG_OPT,
    log_level: str = typer.Option("INFO"),
) -> None:
    """Fill the stratified human-validation gold subset up to gold_size."""
    configure_logging(log_level)
    cfg = load_annotation_config(config_path)
    from uyam.annotate.review import sample_gold_subset

    stats = sample_gold_subset(cfg, cfg.prompt_version, size=size, seed=seed, redraw=redraw)
    if redraw:
        console.print(f"Removed {stats['removed_unlabeled']} unlabeled gold items.")
    console.print(
        f"[green]Gold sample[/green] +{stats['sampled']} items ({stats['kept']} already queued) "
        f"across {stats['strata']} strata (population {stats['population']}); "
        f"split votes {stats['split_sampled']}, shipped sarcastic {stats['sarcastic_sampled']}, "
        f"length bands {stats['bands']}. "
        "Label them in the Streamlit 'Annotation Review' tab."
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

        for ann in [*cfg.annotators, *([cfg.adjudicator] if cfg.adjudicator else [])]:
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
    bundle: bool = typer.Option(
        True, help="Also write uyam-dataset-<version>.zip with every artifact + SHA256SUMS"
    ),
    corpus_scope: str = typer.Option(
        "labeled_authors",
        help="Corpus dump: 'labeled_authors' (exported rows + everything by their authors) "
        "or 'all' (the whole corpus, ~590k records)",
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
        bundle=bundle,
        corpus_scope=corpus_scope,
    )
    console.print(
        f"[green]Exported[/green] {result['rows']} rows "
        f"({result['skipped_unresolved']} unresolved skipped)\n"
        f"  dataset: {result['jsonl']}\n"
        + (f"  parquet: {result['parquet']}\n" if result["parquet"] else "")
        + f"  corpus:  {result['corpus']} ({result['corpus_rows']} records)\n"
        + f"  card:    {result['card']}"
        + (f"\n  bundle:  {result['zip']}" if result.get("zip") else "")
    )
