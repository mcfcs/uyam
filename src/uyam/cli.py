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
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from uyam.config import load_config, load_env
from uyam.dedup import DedupDatabase
from uyam.logging_config import configure_logging
from uyam.models import SCHEMA_VERSION
from uyam.pipeline import make_collection_run_id, run_collection
from uyam.sources.base import CollectionRequest
from uyam.sources.fixture import FixtureRedditSource, validate_fixture

app = typer.Typer(
    name="uyam",
    help="Uyam - Reddit data-collection pipeline for Taglish sarcasm detection research.",
    no_args_is_help=True,
)
console = Console()


def _get_db(data_dir: Path) -> DedupDatabase:
    return DedupDatabase(data_dir / "db" / "collection.sqlite3")


# ---------------------------------------------------------------------------
# collect
# ---------------------------------------------------------------------------

@app.command()
def collect(
    source: str = typer.Option("fixture", help="'fixture', 'public', or 'reddit'"),
    subreddit: str | None = typer.Option(None, help="Subreddit name (overrides config)"),
    listing: str = typer.Option("new", help="Listing type: new|hot|top|search"),
    limit: int | None = typer.Option(None, help="Max submissions to collect"),
    search: str | None = typer.Option(None, help="Search query (sets listing to 'search')"),
    proxies: Path | None = typer.Option(None, help="Path to proxies.txt"),
    config_path: Path | None = typer.Option(None, help="Path to collection.yaml"),
    lenient: bool = typer.Option(False, help="Use lenient fixture validation"),
    log_level: str = typer.Option("INFO", help="Logging level"),
) -> None:
    """Collect Reddit submissions and comments."""
    configure_logging(log_level)
    load_env()
    cfg = load_config(config_path)

    if source not in ("fixture", "public", "reddit"):
        typer.echo(
            f"Error: --source must be 'fixture', 'public', or 'reddit', got {source!r}",
            err=True,
        )
        raise typer.Exit(1)

    subreddits_to_collect = [subreddit] if subreddit else cfg.subreddits
    if not subreddits_to_collect:
        typer.echo(
            "Error: no subreddits configured. "
            "Use --subreddit or edit config/collection.yaml",
            err=True,
        )
        raise typer.Exit(1)

    listing_type = "search" if search else listing
    effective_limit = limit if limit is not None else cfg.collection.limit_per_subreddit

    if source == "fixture":
        reddit_source = FixtureRedditSource(lenient_validation=lenient)
    elif source == "public":
        from uyam.privacy import ensure_hmac_key
        from uyam.sources.proxy_pool import ProxyPool
        from uyam.sources.public_json import PublicJsonRedditSource

        ensure_hmac_key()
        pool = ProxyPool.from_file(proxies)
        reddit_source = PublicJsonRedditSource(
            proxy_url=pool.current(),
            min_interval_seconds=cfg.public.min_interval_seconds,
            timeout_seconds=cfg.public.timeout_seconds,
        )
    else:
        from uyam.privacy import ensure_hmac_key
        from uyam.sources.praw_source import PrawRedditSource
        from uyam.sources.proxy_pool import ProxyPool

        ensure_hmac_key()
        pool = ProxyPool.from_file(proxies)
        proxy_url = pool.current()
        reddit_source = PrawRedditSource(proxy_url=proxy_url)

    data_dir = cfg.data_dir
    db = _get_db(data_dir)

    try:
        for sub_name in subreddits_to_collect:
            run_id = make_collection_run_id()
            request = CollectionRequest(
                subreddit=sub_name,
                listing_type=listing_type,
                collection_run_id=run_id,
                limit=effective_limit,
                sort=cfg.collection.sort,
                time_filter=cfg.collection.time_filter,
                search_query=search,
                max_comments_per_submission=(
                    cfg.comments.max_comments_per_submission
                    if cfg.comments.enabled else 0
                ),
                max_depth=cfg.comments.max_depth,
                include_deleted=cfg.comments.include_deleted,
                comment_sort=cfg.comments.sort,
                replace_more_limit=cfg.comments.replace_more_limit,
                sampling_strategy="natural",
            )

            ctx = run_collection(
                reddit_source,
                request,
                data_dir=data_dir,
                db=db,
                source_type=source,
            )

            console.print(
                f"[green]OK[/green] {sub_name}: "
                f"{ctx.actual_submissions_stored} submissions, "
                f"{ctx.comments_stored} comments stored "
                f"({ctx.duplicates_skipped} duplicates skipped)"
            )

        # Oversampling pass
        if cfg.oversampling.enabled and cfg.oversampling.keywords and source == "reddit":
            for keyword in cfg.oversampling.keywords:
                for sub_name in subreddits_to_collect:
                    run_id = make_collection_run_id()
                    request = CollectionRequest(
                        subreddit=sub_name,
                        listing_type="search",
                        collection_run_id=run_id,
                        limit=effective_limit,
                        search_query=keyword,
                        max_comments_per_submission=(
                            cfg.comments.max_comments_per_submission
                            if cfg.comments.enabled else 0
                        ),
                        max_depth=cfg.comments.max_depth,
                        include_deleted=cfg.comments.include_deleted,
                        comment_sort=cfg.comments.sort,
                        replace_more_limit=cfg.comments.replace_more_limit,
                        sampling_strategy="keyword_oversampled",
                        matched_query_or_keyword=keyword,
                    )
                    ctx = run_collection(
                        reddit_source,
                        request,
                        data_dir=data_dir,
                        db=db,
                        source_type=source,
                    )
                    console.print(
                        f"[yellow]+[/yellow] {sub_name} [{keyword!r}]: "
                        f"{ctx.actual_submissions_stored} submissions stored"
                    )
    finally:
        db.close()


# ---------------------------------------------------------------------------
# validate-fixtures
# ---------------------------------------------------------------------------

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
