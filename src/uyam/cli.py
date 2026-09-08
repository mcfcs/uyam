"""Uyám CLI — Reddit data-collection pipeline for Taglish sarcasm detection.

Commands:
  uyam collect          Collect from fixture or live Reddit source
  uyam validate-fixtures Validate fixture JSON in strict or lenient mode
  uyam status           Show collection statistics
  uyam runs             List collection runs
  uyam inspect-run      Show detail for one collection run
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from uyam.annotate.cli import annotate_app
from uyam.config import load_config, load_env
from uyam.dedup import DedupDatabase
from uyam.logging_config import configure_logging
from uyam.models import SCHEMA_VERSION
from uyam.pipeline import make_collection_run_id
from uyam.scrape_status import (
    LOG_FILE,
    reset_worker_pids,
    set_control,
    write_run_meta,
    write_status,
)
from uyam.sources.fixture import validate_fixture

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_DEFAULT_PROXIES = _REPO_ROOT / "proxies.txt"

app = typer.Typer(
    name="uyam",
    help="Uyam - Reddit data-collection pipeline for Taglish sarcasm detection research.",
    no_args_is_help=True,
)
console = Console()

app.add_typer(annotate_app, name="annotate")


def _get_db(data_dir: Path) -> DedupDatabase:
    return DedupDatabase(data_dir / "db" / "collection.sqlite3")


def _resolve_proxies_path(proxies: Path | None) -> Path:
    """Live sources always read proxies.txt unless an explicit path is given."""
    return proxies if proxies is not None else _DEFAULT_PROXIES


# ---------------------------------------------------------------------------
# collect
# ---------------------------------------------------------------------------

@app.command()
def collect(
    source: str = typer.Option(
        "fixture",
        help="'fixture', 'shreddit' (live HTML), 'public' (alias of shreddit), or 'reddit'",
    ),
    subreddit: str | None = typer.Option(None, help="Subreddit name (overrides config)"),
    subreddits: str | None = typer.Option(
        None, help="Comma-separated subreddit list (overrides config and --subreddit)"
    ),
    listing: str = typer.Option("new", help="Listing type: new|hot|top|search"),
    limit: int | None = typer.Option(None, help="Max submissions to collect"),
    search: str | None = typer.Option(None, help="Search query (sets listing to 'search')"),
    proxies: Path | None = typer.Option(
        None, help="Path to proxies.txt (defaults to ./proxies.txt for live sources)"
    ),
    headless: bool | None = typer.Option(
        None,
        "--headless/--headed",
        help="Shreddit browser mode. Default: headed (see collection.yaml shreddit.headless).",
    ),
    max_comments: int | None = typer.Option(
        None,
        help="Max comments per submission. -1 = all replies. 0 = skip comments.",
    ),
    captcha_wait: float | None = typer.Option(
        None,
        help="Seconds to wait for you to solve a Chrome captcha (default: collection.yaml).",
    ),
    max_seconds: float | None = typer.Option(
        None,
        help="Stop collection after N seconds (wall clock). 0 or omit = no time limit.",
    ),
    workers: int | None = typer.Option(
        None,
        help="Parallel subreddit browsers. Default: min(3, subreddit count) for shreddit.",
    ),
    since: str | None = typer.Option(
        None,
        help="UTC start date YYYY-MM-DD. Walk /new back to this day (N posts/day).",
    ),
    until: str | None = typer.Option(
        None,
        help="UTC end date YYYY-MM-DD inclusive. Default: today. Requires --since.",
    ),
    per_day: int | None = typer.Option(
        None,
        help="Max new posts per UTC day (default 15 when --since is set).",
    ),
    oversample_only: bool = typer.Option(
        False,
        "--oversample-only",
        help="Skip the natural listing; run one search per sarcasm marker in "
        "collection.yaml oversampling.keywords (tagged keyword_oversampled).",
    ),
    config_path: Path | None = typer.Option(None, help="Path to collection.yaml"),
    lenient: bool = typer.Option(False, help="Use lenient fixture validation"),
    log_level: str = typer.Option("INFO", help="Logging level"),
) -> None:
    """Collect Reddit submissions and comments."""
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    configure_logging(log_level, log_file=str(LOG_FILE))
    load_env()
    cfg = load_config(config_path)
    set_control("run")
    reset_worker_pids()
    effective_max_seconds = max_seconds if max_seconds and max_seconds > 0 else None
    write_run_meta(pid=os.getpid(), max_seconds=effective_max_seconds)
    write_status(
        "running",
        "Collection started"
        if effective_max_seconds is None
        else f"Collection started (time limit {effective_max_seconds:.0f}s)",
    )

    if source not in ("fixture", "public", "shreddit", "reddit"):
        typer.echo(
            "Error: --source must be 'fixture', 'shreddit', 'public', or 'reddit', "
            f"got {source!r}",
            err=True,
        )
        raise typer.Exit(1)

    if subreddits:
        subreddits_to_collect = [s.strip() for s in subreddits.split(",") if s.strip()]
    elif subreddit:
        subreddits_to_collect = [subreddit]
    else:
        subreddits_to_collect = cfg.subreddits
    if not subreddits_to_collect:
        typer.echo(
            "Error: no subreddits configured. "
            "Use --subreddit or edit config/collection.yaml",
            err=True,
        )
        raise typer.Exit(1)

    listing_type = "search" if search else listing
    effective_limit = limit if limit is not None else cfg.collection.limit_per_subreddit
    calendar_since = since.strip() if since else None
    calendar_until = until.strip() if until else None
    posts_per_day: int | None = None
    if calendar_since:
        from uyam.calendar_window import parse_iso_date

        try:
            parse_iso_date(calendar_since)
            if calendar_until:
                parse_iso_date(calendar_until)
        except ValueError as exc:
            typer.echo(f"Error: dates must be YYYY-MM-DD ({exc})", err=True)
            raise typer.Exit(1) from exc
        posts_per_day = per_day if per_day and per_day > 0 else 15
    elif per_day or calendar_until:
        typer.echo("Error: --per-day / --until require --since YYYY-MM-DD", err=True)
        raise typer.Exit(1)
    proxies_path = _resolve_proxies_path(proxies)
    source_type = "shreddit" if source in ("public", "shreddit") else source
    if max_comments is None:
        effective_max_comments = (
            cfg.comments.max_comments_per_submission if cfg.comments.enabled else 0
        )
    elif max_comments < 0:
        effective_max_comments = None
    else:
        effective_max_comments = max_comments

    data_dir = cfg.data_dir if cfg.data_dir.is_absolute() else _REPO_ROOT / cfg.data_dir
    if workers is None:
        effective_workers = (
            min(3, len(subreddits_to_collect)) if source_type == "shreddit" else 1
        )
    else:
        effective_workers = max(1, int(workers))

    jobs: list[dict] = []
    for index, sub_name in enumerate(subreddits_to_collect):
        jobs.append(
            {
                "source": source,
                "source_type": source_type,
                "subreddit": sub_name,
                "listing_type": listing_type,
                "collection_run_id": make_collection_run_id(),
                "limit": effective_limit,
                "sort": cfg.collection.sort,
                "time_filter": cfg.collection.time_filter,
                "search_query": search,
                "max_comments_per_submission": effective_max_comments,
                "max_depth": cfg.comments.max_depth,
                "include_deleted": cfg.comments.include_deleted,
                "comment_sort": cfg.comments.sort,
                "replace_more_limit": cfg.comments.replace_more_limit,
                "sampling_strategy": "natural",
                "matched_query_or_keyword": None,
                "max_seconds": effective_max_seconds,
                "calendar_since": calendar_since,
                "calendar_until": calendar_until,
                "posts_per_day": posts_per_day,
                "headless": headless,
                "captcha_wait": captcha_wait,
                "proxies_path": str(proxies_path),
                "config_path": str(config_path) if config_path else None,
                "data_dir": str(data_dir),
                "log_level": log_level,
                "lenient": lenient,
                "worker_index": index,
                "author_hmac_key": os.environ.get("AUTHOR_HMAC_KEY"),
            }
        )

    from uyam.parallel import run_jobs

    stopped = False
    try:
        if oversample_only:
            console.print("Keyword oversampling only — skipping the natural listing pass.")
            results = []
        else:
            if effective_workers > 1:
                console.print(
                    f"Parallel scrape: {len(jobs)} subreddits, {effective_workers} browsers"
                )
            results = run_jobs(jobs, max_workers=effective_workers)
        for row in results:
            sub_name = str(row.get("subreddit") or "")
            errors = [str(e) for e in (row.get("errors") or [])]
            if "stopped_by_user" in errors or "time_limit_reached" in errors:
                stopped = True
                why = "time limit" if "time_limit_reached" in errors else "stopped"
                console.print(
                    f"[yellow]{why.upper()}[/yellow] {sub_name}: "
                    f"{row.get('submissions', 0)} submissions, "
                    f"{row.get('comments', 0)} comments stored "
                    f"({row.get('duplicates', 0)} duplicates skipped)"
                )
            elif not row.get("ok"):
                console.print(
                    f"[red]FAIL[/red] {sub_name}: {'; '.join(errors) or 'unknown error'}"
                )
            else:
                console.print(
                    f"[green]OK[/green] {sub_name}: "
                    f"{row.get('submissions', 0)} submissions, "
                    f"{row.get('comments', 0)} comments stored "
                    f"({row.get('duplicates', 0)} duplicates skipped)"
                )

        want_oversampling = (
            (oversample_only or cfg.oversampling.enabled)
            and bool(cfg.oversampling.keywords)
            and source != "fixture"
        )
        if want_oversampling and stopped:
            console.print(
                "[yellow]Keyword oversampling skipped[/yellow]: the natural pass was stopped. "
                "Run `uyam collect --oversample-only` to do it on its own."
            )
        if want_oversampling and not stopped:
            over_jobs: list[dict] = []
            idx = 0
            for keyword in cfg.oversampling.keywords:
                for sub_name in subreddits_to_collect:
                    over_jobs.append(
                        {
                            **jobs[0],
                            "subreddit": sub_name,
                            "listing_type": "search",
                            "collection_run_id": make_collection_run_id(),
                            "search_query": keyword,
                            "sampling_strategy": "keyword_oversampled",
                            "matched_query_or_keyword": keyword,
                            "worker_index": idx,
                        }
                    )
                    idx += 1
            over_results = run_jobs(over_jobs, max_workers=effective_workers)
            for job, row in zip(over_jobs, over_results, strict=True):
                errs = [str(e) for e in (row.get("errors") or [])]
                if "stopped_by_user" in errs or "time_limit_reached" in errs:
                    stopped = True
                console.print(
                    f"[yellow]+[/yellow] {row.get('subreddit')} "
                    f"[{job.get('matched_query_or_keyword')!r}]: "
                    f"{row.get('submissions', 0)} submissions stored"
                )
    finally:
        write_status(
            "idle",
            "Collection stopped" if stopped else "Collection finished",
        )


# ---------------------------------------------------------------------------
# clear-data
# ---------------------------------------------------------------------------

@app.command(name="clear-data")
def clear_data_cmd(
    yes: bool = typer.Option(False, "--yes", help="Skip confirmation prompt"),
    config_path: Path | None = typer.Option(None, help="Path to collection.yaml"),
) -> None:
    """Delete collected JSONL, manifests, and the SQLite index."""
    from uyam.scrape_status import scrape_is_running
    from uyam.storage import clear_collected_data

    if scrape_is_running():
        typer.echo("Error: a scrape is running. Stop it first.", err=True)
        raise typer.Exit(1)

    cfg = load_config(config_path)
    data_dir = cfg.data_dir if cfg.data_dir.is_absolute() else _REPO_ROOT / cfg.data_dir
    if not yes:
        confirm = typer.confirm(
            f"Delete all collected JSONL, manifests, and SQLite under {data_dir}?"
        )
        if not confirm:
            raise typer.Abort()

    stats = clear_collected_data(data_dir)
    console.print(
        "[green]Cleared[/green] "
        f"{stats['jsonl_files']} JSONL files, "
        f"{stats['manifests']} manifests, "
        f"{stats['db_files']} DB files"
    )


# ---------------------------------------------------------------------------
# validate-fixtures
# ---------------------------------------------------------------------------

@app.command(name="scrub-authors")
def scrub_authors_cmd(
    config_path: Path | None = typer.Option(None, help="Path to collection.yaml"),
) -> None:
    """Remove plaintext usernames from every raw JSONL line (keeps author_hash).

    Thesis §3.1: author identifiers are anonymized before storage. Files
    written before 2026-09-08 carried the raw `author` next to the hash; this
    rewrites them in place. Idempotent. Refuses to run during a live scrape.
    """
    from uyam.scrape_status import scrape_is_running
    from uyam.storage import scrub_author_field

    cfg = load_config(config_path)
    if scrape_is_running():
        typer.echo("Error: a live scrape is running — stop it before scrubbing.", err=True)
        raise typer.Exit(1)
    data_dir = cfg.data_dir if cfg.data_dir.is_absolute() else _REPO_ROOT / cfg.data_dir
    stats = scrub_author_field(data_dir)
    console.print(
        f"[green]Scrubbed[/green] {stats['records_scrubbed']} records in "
        f"{stats['files_rewritten']} of {stats['files']} JSONL files "
        "(plaintext `author` removed; author_hash kept)."
    )


@app.command(name="validate-fixtures")
def validate_fixtures_cmd(
    fixture_path: Path | None = typer.Option(None, help="Path to fixture JSON"),
    lenient: bool = typer.Option(False, help="Warn instead of failing on issues"),
) -> None:
    """Validate fixture JSON schema and relationships."""
    if fixture_path is None:
        fixture_path = Path(__file__).parent.parent.parent / "fixtures" / "sample_posts.json"

    import json as _json

    from uyam.sources.fixture import FixtureValidationError

    try:
        data = _json.loads(fixture_path.read_text(encoding="utf-8"))
    except (OSError, _json.JSONDecodeError) as exc:
        typer.echo(f"Error reading fixture: {exc}", err=True)
        raise typer.Exit(1) from exc

    try:
        warnings = validate_fixture(data, lenient=lenient)
        if warnings:
            for w in warnings:
                console.print(f"[yellow]WARN[/yellow] {w}")
            console.print(
                f"\n[yellow]{len(warnings)} warning(s)[/yellow] - fixture valid in lenient mode."
            )
        else:
            count = len(data.get("submissions", []))
            console.print(f"[green]OK Fixture valid[/green] - {count} submissions, strict mode.")
    except FixtureValidationError as exc:
        console.print(f"[red]FAIL Fixture validation:[/red] {exc}")
        raise typer.Exit(1) from exc


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------

@app.command()
def status(
    config_path: Path | None = typer.Option(None),
) -> None:
    """Show collection statistics by subreddit."""
    cfg = load_config(config_path)
    db = _get_db(cfg.data_dir)

    try:
        stats = db.subreddit_stats()
        totals = db.total_counts()
    finally:
        db.close()

    if not stats:
        console.print("[yellow]No data collected yet.[/yellow]")
        return

    table = Table(title="Uyám Collection Status", show_header=True, header_style="bold")
    table.add_column("Subreddit", style="cyan")
    table.add_column("Submissions", justify="right")
    table.add_column("Comments", justify="right")
    table.add_column("Last Collection", style="dim")

    for row in stats:
        last = str(row["last_collection"])[:19] if row["last_collection"] else "-"
        table.add_row(
            str(row["subreddit"]),
            str(row["submissions"]),
            str(row["comments"]),
            last,
        )

    console.print(table)
    console.print(
        f"\nTotal records: [bold]{totals['total_records']}[/bold]  "
        f"Collection runs: [bold]{totals['total_runs']}[/bold]  "
        f"Schema version: [bold]{SCHEMA_VERSION}[/bold]  "
        f"DB: [dim]{cfg.data_dir / 'db' / 'collection.sqlite3'}[/dim]"
    )


# ---------------------------------------------------------------------------
# runs
# ---------------------------------------------------------------------------

@app.command()
def runs(
    config_path: Path | None = typer.Option(None),
) -> None:
    """List all collection runs."""
    cfg = load_config(config_path)
    db = _get_db(cfg.data_dir)

    try:
        all_runs = db.list_runs()
    finally:
        db.close()

    if not all_runs:
        console.print("[yellow]No collection runs yet.[/yellow]")
        return

    table = Table(title="Collection Runs", show_header=True, header_style="bold")
    table.add_column("Run ID", style="dim", max_width=38)
    table.add_column("Source")
    table.add_column("Subreddit", style="cyan")
    table.add_column("Started", style="dim")
    table.add_column("Subs", justify="right")
    table.add_column("Comments", justify="right")
    table.add_column("Dups", justify="right")

    for run in all_runs:
        table.add_row(
            str(run["collection_run_id"])[:36],
            str(run["source_type"]),
            str(run["subreddit"]),
            str(run["started_at_utc"])[:19],
            str(run["submissions_stored"]),
            str(run["comments_stored"]),
            str(run["duplicates_skipped"]),
        )

    console.print(table)


# ---------------------------------------------------------------------------
# inspect-run
# ---------------------------------------------------------------------------

@app.command(name="inspect-run")
def inspect_run(
    collection_run_id: str = typer.Argument(help="Collection run UUID to inspect"),
    config_path: Path | None = typer.Option(None),
) -> None:
    """Show detail for one collection run."""
    cfg = load_config(config_path)
    db = _get_db(cfg.data_dir)

    try:
        run = db.get_run(collection_run_id)
    finally:
        db.close()

    if run is None:
        typer.echo(f"Run not found: {collection_run_id}", err=True)
        raise typer.Exit(1)

    # Also load manifest if available
    manifest: dict | None = None
    manifest_path = run.get("manifest_path")
    if manifest_path and Path(str(manifest_path)).exists():
        import contextlib
        with contextlib.suppress(OSError, json.JSONDecodeError):
            manifest = json.loads(Path(str(manifest_path)).read_text(encoding="utf-8"))

    data = manifest or run
    console.print_json(json.dumps(data, indent=2, default=str))


def main() -> None:
    app()


if __name__ == "__main__":
    main()
