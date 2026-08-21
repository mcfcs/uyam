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

from uyam.config import AppConfig, load_config, load_env
from uyam.dedup import DedupDatabase
from uyam.logging_config import configure_logging
from uyam.models import SCHEMA_VERSION
from uyam.pipeline import make_collection_run_id, run_collection
from uyam.scrape_status import LOG_FILE, set_control, write_run_meta, write_status
from uyam.sources.base import CollectionRequest
from uyam.sources.fixture import FixtureRedditSource, validate_fixture

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_DEFAULT_PROXIES = _REPO_ROOT / "proxies.txt"

app = typer.Typer(
    name="uyam",
    help="Uyam - Reddit data-collection pipeline for Taglish sarcasm detection research.",
    no_args_is_help=True,
)
console = Console()


def _get_db(data_dir: Path) -> DedupDatabase:
    return DedupDatabase(data_dir / "db" / "collection.sqlite3")


def _resolve_proxies_path(proxies: Path | None) -> Path:
    """Live sources always read proxies.txt unless an explicit path is given."""
    return proxies if proxies is not None else _DEFAULT_PROXIES


def _build_shreddit_source(
    cfg: AppConfig,
    proxies_path: Path,
    *,
    headless: bool | None,
    captcha_wait: float | None = None,
) -> object:
    from uyam.privacy import ensure_hmac_key
    from uyam.sources.proxy_pool import ProxyPool
    from uyam.sources.shreddit import ShredditBrowserSource

    ensure_hmac_key()
    pool = ProxyPool.from_file(proxies_path)
    if pool.is_empty():
        raise typer.BadParameter(
            f"shreddit requires proxies in {proxies_path}. "
            "Copy proxies.example.txt to proxies.txt and add at least one proxy."
        )
    return ShredditBrowserSource(
        proxy_pool=pool,
        headless=cfg.shreddit.headless if headless is None else headless,
        timeout_seconds=cfg.shreddit.timeout_seconds,
        min_interval_seconds=cfg.shreddit.min_interval_seconds,
        max_scrolls=cfg.shreddit.max_scrolls,
        more_comments_clicks=cfg.shreddit.more_comments_clicks,
        scroll_wait_ms=cfg.shreddit.scroll_wait_ms,
        expand_wait_ms=cfg.shreddit.expand_wait_ms,
        use_system_chrome=cfg.shreddit.use_system_chrome,
        captcha_wait_seconds=(
            cfg.shreddit.captcha_wait_seconds if captcha_wait is None else captcha_wait
        ),
    )


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
    write_run_meta(pid=os.getpid())
    write_status("running", "Collection started")

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

    reddit_source: object
    if source == "fixture":
        reddit_source = FixtureRedditSource(lenient_validation=lenient)
    elif source in ("public", "shreddit"):
        try:
            reddit_source = _build_shreddit_source(
                cfg,
                proxies_path,
                headless=headless,
                captcha_wait=captcha_wait,
            )
        except typer.BadParameter as exc:
            typer.echo(f"Error: {exc}", err=True)
            raise typer.Exit(1) from exc
    else:
        from uyam.privacy import ensure_hmac_key
        from uyam.sources.praw_source import PrawRedditSource
        from uyam.sources.proxy_pool import ProxyPool

        ensure_hmac_key()
        pool = ProxyPool.from_file(proxies_path)
        proxy_url = pool.current()
        reddit_source = PrawRedditSource(proxy_url=proxy_url)

    data_dir = cfg.data_dir if cfg.data_dir.is_absolute() else _REPO_ROOT / cfg.data_dir
    db = _get_db(data_dir)

    stopped = False
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
                max_comments_per_submission=effective_max_comments,
                max_depth=cfg.comments.max_depth,
                include_deleted=cfg.comments.include_deleted,
                comment_sort=cfg.comments.sort,
                replace_more_limit=cfg.comments.replace_more_limit,
                sampling_strategy="natural",
            )

            ctx = run_collection(
                reddit_source,  # type: ignore[arg-type]
                request,
                data_dir=data_dir,
                db=db,
                source_type=source_type,
            )

            if "stopped_by_user" in ctx.errors:
                stopped = True
                console.print(
                    f"[yellow]STOPPED[/yellow] {sub_name}: "
                    f"{ctx.actual_submissions_stored} submissions, "
                    f"{ctx.comments_stored} comments stored "
                    f"({ctx.duplicates_skipped} duplicates skipped)"
                )
                break
            console.print(
                f"[green]OK[/green] {sub_name}: "
                f"{ctx.actual_submissions_stored} submissions, "
                f"{ctx.comments_stored} comments stored "
                f"({ctx.duplicates_skipped} duplicates skipped)"
            )

        # Oversampling pass (live sources only)
        if (
            cfg.oversampling.enabled
            and cfg.oversampling.keywords
            and source != "fixture"
            and not stopped
        ):
            for keyword in cfg.oversampling.keywords:
                for sub_name in subreddits_to_collect:
                    run_id = make_collection_run_id()
                    request = CollectionRequest(
                        subreddit=sub_name,
                        listing_type="search",
                        collection_run_id=run_id,
                        limit=effective_limit,
                        search_query=keyword,
                        max_comments_per_submission=effective_max_comments,
                        max_depth=cfg.comments.max_depth,
                        include_deleted=cfg.comments.include_deleted,
                        comment_sort=cfg.comments.sort,
                        replace_more_limit=cfg.comments.replace_more_limit,
                        sampling_strategy="keyword_oversampled",
                        matched_query_or_keyword=keyword,
                    )
                    ctx = run_collection(
                        reddit_source,  # type: ignore[arg-type]
                        request,
                        data_dir=data_dir,
                        db=db,
                        source_type=source_type,
                    )
                    console.print(
                        f"[yellow]+[/yellow] {sub_name} [{keyword!r}]: "
                        f"{ctx.actual_submissions_stored} submissions stored"
                    )
    finally:
        close = getattr(reddit_source, "close", None)
        if callable(close):
            close()
        db.close()
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
