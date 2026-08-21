"""Streamlit web UI for the Uyám collection pipeline.

Launch with:
    streamlit run src/uyam/app.py
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

from uyam.config import load_config, load_env
from uyam.dedup import DedupDatabase
from uyam.pipeline import make_collection_run_id, run_collection
from uyam.scrape_status import (
    LOG_FILE,
    SCREENSHOT_FILE,
    get_control,
    read_status,
    request_stop_and_kill,
    scrape_is_running,
    set_control,
    write_run_meta,
)
from uyam.sources.base import CollectionRequest
from uyam.sources.fixture import FixtureRedditSource
from uyam.sources.proxy_pool import ProxyPool
from uyam.storage import clear_collected_data

# ---------------------------------------------------------------------------
# Page config — must be the first Streamlit call
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="Uyam Collector",
    page_icon=":mag:",
    layout="wide",
    initial_sidebar_state="expanded",
)

load_env()
cfg = load_config()

_REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR: Path = cfg.data_dir if cfg.data_dir.is_absolute() else _REPO_ROOT / cfg.data_dir
DB_PATH: Path = DATA_DIR / "db" / "collection.sqlite3"
PROXIES_PATH: Path = _REPO_ROOT / "proxies.txt"
CONFIG_PATH: Path = _REPO_ROOT / "config" / "collection.yaml"

# ---------------------------------------------------------------------------
# Session state
# ---------------------------------------------------------------------------
if "logs" not in st.session_state:
    st.session_state["logs"]: list[str] = []
if "last_results" not in st.session_state:
    st.session_state["last_results"]: list[dict[str, Any]] = []


# ---------------------------------------------------------------------------
# Custom log handler: captures pipeline log output to a list
# ---------------------------------------------------------------------------
class _ListHandler(logging.Handler):
    def __init__(self, target: list[str]) -> None:
        super().__init__()
        self.target = target

    def emit(self, record: logging.LogRecord) -> None:
        self.target.append(self.format(record))


# ---------------------------------------------------------------------------
# Data helpers (no cache — always read fresh after a collection run)
# ---------------------------------------------------------------------------

_SUBMISSION_BROWSER_COLS = [
    "record_type",
    "id",
    "subreddit",
    "title",
    "selftext",
    "author",
    "created_utc",
    "score",
    "upvote_ratio",
    "num_comments",
    "permalink",
    "post_url",
    "url",
    "is_self",
    "over_18",
    "spoiler",
    "stickied",
    "locked",
    "archived",
    "distinguished",
    "link_flair_text",
    "gilded",
    "num_crossposts",
    "is_original_content",
    "author_status",
    "sampling_strategy",
    "collection_run_id",
]

_COMMENT_BROWSER_COLS = [
    "record_type",
    "id",
    "author",
    "body",
    "depth",
    "reply_to",
    "post_id",
    "post_title",
    "post_url",
    "parent_comment_id",
    "parent_id",
    "parent_url",
    "comment_url",
    "link_id",
    "submission_id",
    "subreddit",
    "created_utc",
    "score",
    "is_submitter",
    "distinguished",
    "stickied",
    "gilded",
    "controversiality",
    "author_status",
    "collection_run_id",
]

_ALL_BROWSER_COLS = [
    "record_type",
    "id",
    "subreddit",
    "author",
    "title",
    "selftext",
    "body",
    "depth",
    "reply_to",
    "post_id",
    "post_title",
    "post_url",
    "parent_comment_id",
    "parent_id",
    "parent_url",
    "comment_url",
    "created_utc",
    "score",
    "upvote_ratio",
    "num_comments",
    "permalink",
    "url",
    "is_self",
    "over_18",
    "spoiler",
    "stickied",
    "locked",
    "archived",
    "distinguished",
    "link_flair_text",
    "gilded",
    "num_crossposts",
    "is_original_content",
    "is_submitter",
    "controversiality",
    "author_status",
    "sampling_strategy",
    "collection_run_id",
]

_TEXT_TRUNCATE = frozenset({"title", "selftext", "body", "post_title"})


def _reddit_url(path: str | None) -> str | None:
    if not path:
        return None
    if str(path).startswith("http"):
        return str(path)
    return "https://www.reddit.com" + (path if str(path).startswith("/") else "/" + str(path))


def _enrich_relationships(
    rec: dict[str, Any], posts_by_id: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    """Add post/parent ids and clickable URLs so threads are reconstructable."""
    out = dict(rec)
    if rec.get("record_type") == "submission":
        sid = rec.get("id") or rec.get("reddit_id")
        out["id"] = sid
        out["post_id"] = sid
        out["post_url"] = rec.get("post_url") or _reddit_url(rec.get("permalink"))
        return out

    sub_id = rec.get("submission_id") or rec.get("post_id") or ""
    link_id = rec.get("link_id") or ""
    if not sub_id and link_id.startswith("t3_"):
        sub_id = link_id[3:]
    parent_id = rec.get("parent_id") or ""
    post = posts_by_id.get(sub_id) or {}
    post_url = (
        rec.get("post_url")
        or _reddit_url(post.get("permalink"))
        or (
            f"https://www.reddit.com/r/{rec.get('subreddit', '')}/comments/{sub_id}/"
            if sub_id
            else None
        )
    )
    if parent_id.startswith("t1_"):
        parent_comment_id = rec.get("parent_comment_id") or parent_id[3:]
        reply_to = "comment"
        parent_url = rec.get("parent_url") or (
            f"https://www.reddit.com/r/{rec.get('subreddit', '')}/comments/"
            f"{sub_id}/comment/{parent_comment_id}/"
        )
    else:
        parent_comment_id = rec.get("parent_comment_id")
        reply_to = "post"
        parent_url = rec.get("parent_url") or post_url

    out["id"] = rec.get("id") or rec.get("reddit_id")
    out["post_id"] = sub_id
    out["post_title"] = post.get("title")
    out["post_url"] = post_url
    out["reply_to"] = rec.get("reply_to") or reply_to
    out["parent_comment_id"] = parent_comment_id
    out["parent_url"] = parent_url
    out["comment_url"] = rec.get("comment_url") or _reddit_url(rec.get("permalink"))
    return out


def _browser_row(rec: dict[str, Any], columns: list[str]) -> dict[str, Any]:
    row: dict[str, Any] = {}
    for col in columns:
        val = rec.get(col)
        if col == "id" and not val:
            val = rec.get("reddit_id")
        if col in _TEXT_TRUNCATE and isinstance(val, str) and len(val) > 160:
            val = val[:160] + "…"
        if col in {"created_utc", "collection_run_id"} and val is not None:
            val = str(val)
        row[col] = val
    return row


def _thread_lines(records: list[dict[str, Any]]) -> str:
    """Indented text view: post → comments → replies-to-replies."""
    posts = [r for r in records if r.get("record_type") == "submission"]
    comments = [r for r in records if r.get("record_type") == "comment"]
    by_parent: dict[str, list[dict[str, Any]]] = {}
    for com in comments:
        parent = com.get("parent_id") or ""
        by_parent.setdefault(parent, []).append(com)
    for kids in by_parent.values():
        kids.sort(key=lambda c: (int(c.get("depth") or 0), str(c.get("created_utc") or "")))

    lines: list[str] = []

    def walk(parent_fullname: str, indent: int) -> None:
        for com in by_parent.get(parent_fullname, []):
            cid = com.get("id") or com.get("reddit_id")
            author = com.get("author") or com.get("author_status") or "?"
            body = (com.get("body") or "").replace("\n", " ")
            if len(body) > 90:
                body = body[:90] + "…"
            prefix = "  " * indent + ("└ " if indent else "• ")
            lines.append(f"{prefix}[comment {cid}] u/{author}  {body}")
            walk(com.get("reddit_fullname") or f"t1_{cid}", indent + 1)

    seen_posts: set[str] = set()
    for post in posts:
        fn = post.get("reddit_fullname") or f"t3_{post.get('id')}"
        seen_posts.add(fn)
        title = post.get("title") or ""
        lines.append(f"POST [{post.get('id')}] {title}")
        url = post.get("post_url") or _reddit_url(post.get("permalink"))
        if url:
            lines.append(f"  {url}")
        walk(fn, 1)
        lines.append("")

    orphan_parents = [k for k in by_parent if k not in seen_posts and k.startswith("t3_")]
    for fn in orphan_parents:
        pid = fn[3:]
        lines.append(f"POST [{pid}]")
        walk(fn, 1)
        lines.append("")
    return "\n".join(lines).rstrip()


def _load_jsonl_records() -> list[dict[str, Any]]:
    """Read every JSONL line from data/raw/**/*.jsonl."""
    records: list[dict[str, Any]] = []
    raw_dir = DATA_DIR / "raw"
    if not raw_dir.exists():
        return records
    for filepath in sorted(raw_dir.glob("**/*.jsonl")):
        try:
            text = filepath.read_text(encoding="utf-8")
        except OSError:
            continue
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            with contextlib.suppress(json.JSONDecodeError):
                records.append(json.loads(line))
    return records


def _load_runs() -> list[dict[str, Any]]:
    if not DB_PATH.exists():
        return []
    with sqlite3.connect(str(DB_PATH)) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM collection_runs ORDER BY started_at_utc DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def _max_comments_arg(collect: bool, all_replies: bool, cap: int) -> int:
    if not collect:
        return 0
    if all_replies:
        return -1
    return cap


def _refresh_captcha_preview(warn_slot: Any, img_slot: Any) -> None:
    status = read_status()
    if status and status.get("state") == "captcha":
        warn_slot.warning(
            status.get("message")
            or "Solve the captcha in the Chrome window. This preview is a snapshot."
        )
        if SCREENSHOT_FILE.exists():
            img_slot.image(
                SCREENSHOT_FILE.read_bytes(),
                caption="Chrome preview — click/solve the challenge in the Chrome popup",
            )
    elif status and status.get("state") == "ok":
        warn_slot.empty()


def _start_background_collect(
    *,
    source: str,
    subreddits: list[str],
    listing: str,
    limit: int,
    search: str | None,
    max_comments: int,
    headed: bool,
    captcha_wait: float,
) -> int:
    """Start `uyam collect` in the background so Pause/Stop stay clickable."""
    cmd: list[str] = [
        sys.executable,
        "-m",
        "uyam.cli",
        "collect",
        "--source",
        source,
        "--subreddits",
        ",".join(subreddits),
        "--listing",
        listing,
        "--limit",
        str(limit),
        "--max-comments",
        str(max_comments),
        "--config-path",
        str(CONFIG_PATH),
        "--log-level",
        "INFO",
    ]
    if search:
        cmd.extend(["--search", search])
    if source in ("shreddit", "public"):
        cmd.append("--headed" if headed else "--headless")
        cmd.extend(["--proxies", str(PROXIES_PATH)])
        cmd.extend(["--captcha-wait", str(int(captcha_wait))])

    env = os.environ.copy()
    src_path = str(_REPO_ROOT / "src")
    env["PYTHONPATH"] = src_path + os.pathsep + env.get("PYTHONPATH", "")
    env["PYTHONUNBUFFERED"] = "1"

    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    log_handle = LOG_FILE.open("w", encoding="utf-8")
    set_control("run")
    proc = subprocess.Popen(
        cmd,
        cwd=str(_REPO_ROOT),
        env=env,
        stdout=log_handle,
        stderr=subprocess.STDOUT,
    )
    write_run_meta(pid=proc.pid, argv=cmd)
    return proc.pid


def _load_stats() -> list[dict[str, Any]]:
    if not DB_PATH.exists():
        return []
    with sqlite3.connect(str(DB_PATH)) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT subreddit,
                   SUM(CASE WHEN record_type = 'submission' THEN 1 ELSE 0 END) AS submissions,
                   SUM(CASE WHEN record_type = 'comment'    THEN 1 ELSE 0 END) AS comments,
                   MAX(first_seen_at_utc) AS last_collection
            FROM collected_records
            GROUP BY subreddit
            ORDER BY subreddit
            """
        ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Sidebar — collection controls
# ---------------------------------------------------------------------------
with st.sidebar:
    st.markdown("## Uyam Collector")
    st.caption("Taglish sarcasm detection — data pipeline")
    st.divider()

    source_type: str = st.radio(
        "Source",
        ["fixture", "shreddit", "reddit"],
        horizontal=True,
        help=(
            "'fixture' = offline sample data. "
            "'shreddit' = live www.reddit.com via headful Chrome + proxies.txt. "
            "'reddit' = live Reddit via API app credentials."
        ),
    )  # type: ignore[assignment]

    if source_type == "shreddit":
        st.info(
            "No Reddit API credentials. Starts a real Chrome scrape through "
            "`proxies.txt` (Shreddit HTML). A pseudonymization key is "
            "auto-generated in `.env` on first run."
        )
        proxy_pool = ProxyPool.from_file(PROXIES_PATH)
        if proxy_pool.is_empty():
            st.error(f"No proxies loaded from `{PROXIES_PATH.name}`. Scrape will not start.")
        else:
            st.caption(f"Proxies loaded: {proxy_pool.healthy_count} from `{PROXIES_PATH.name}`")
        headed: bool = st.checkbox(
            "Headed Chrome (required for captcha)",
            value=True,
            help="A real Chrome window must stay open so you can solve Reddit's challenge.",
        )
        wait_for_captcha: bool = st.checkbox(
            "Pause when captcha appears so I can solve it",
            value=True,
            help="The scraper waits and shows a live screenshot here. Solve it in Chrome.",
        )
        captcha_wait_val: int = st.number_input(
            "Captcha wait (seconds)",
            min_value=30,
            max_value=900,
            value=int(cfg.shreddit.captcha_wait_seconds or 300),
            disabled=not wait_for_captcha,
        )  # type: ignore[assignment]
        st.caption(
            "Auto-solving captchas is not supported. When Reddit pops the challenge, "
            "Chrome stays open — complete it there. This page shows a snapshot of that window."
        )
    elif source_type == "reddit":
        st.info(
            "Requires `REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET`, "
            "`REDDIT_USER_AGENT`, and `AUTHOR_HMAC_KEY` in `.env`."
        )
        headed = True
        wait_for_captcha = False
        captcha_wait_val = 0
    else:
        headed = True
        wait_for_captcha = False
        captcha_wait_val = 0

    st.markdown("**Subreddits**")
    available: list[str] = cfg.subreddits or ["Philippines", "CasualPH", "OffMyChestPH"]
    selected_subs: list[str] = st.multiselect(
        "Select subreddits",
        options=available,
        default=available,
        label_visibility="collapsed",
    )
    custom_sub = st.text_input("Add subreddit", placeholder="e.g. filipinotrending").strip()
    if custom_sub and custom_sub not in selected_subs:
        selected_subs = selected_subs + [custom_sub]

    st.divider()

    listing_opt: str = st.selectbox(  # type: ignore[assignment]
        "Listing",
        ["new", "hot", "top", "search"],
        help="Listing type to fetch from each subreddit.",
    )
    search_q: str | None = None
    if listing_opt == "search":
        raw_q = st.text_input("Search query", placeholder="e.g. sana all, edi wow")
        search_q = raw_q.strip() or None

    limit_val: int = st.number_input(
        "Limit per subreddit",
        min_value=1,
        max_value=1000,
        value=int(cfg.collection.limit_per_subreddit or 100),
    )  # type: ignore[assignment]

    collect_comments: bool = st.checkbox(
        "Collect replies",
        value=bool(cfg.comments.enabled),
        help="Visit each post and store its comment tree.",
    )
    all_replies: bool = st.checkbox(
        "All replies (no comment cap)",
        value=True,
        disabled=not collect_comments,
        help="Expand Shreddit 'more comments' until the thread is exhausted.",
    )
    max_comments_val: int = st.number_input(
        "Max comments per post",
        min_value=1,
        max_value=5000,
        value=int(cfg.comments.max_comments_per_submission or 100),
        disabled=(not collect_comments) or all_replies,
    )  # type: ignore[assignment]

    st.divider()

    shreddit_blocked = source_type == "shreddit" and ProxyPool.from_file(PROXIES_PATH).is_empty()
    already_running = scrape_is_running()
    run_btn = st.button(
        "Start Collection",
        type="primary",
        use_container_width=True,
        disabled=(not bool(selected_subs)) or shreddit_blocked or already_running,
    )

    if st.button("Refresh data", use_container_width=True):
        st.rerun()

# ---------------------------------------------------------------------------
# Tabs
# ---------------------------------------------------------------------------
tab_collect, tab_data, tab_history, tab_stats = st.tabs(
    ["Collection", "Data Browser", "Run History", "Stats"]
)

# ===========================================================================
# Tab 1: Collection
# ===========================================================================
with tab_collect:
    if run_btn and selected_subs:
        logs: list[str] = []
        results: list[dict[str, Any]] = []

        # Attach a log handler so pipeline messages are captured
        handler = _ListHandler(logs)
        handler.setLevel(logging.INFO)
        handler.setFormatter(logging.Formatter("%(levelname)-5s %(name)s — %(message)s"))
        # Suppress noisy third-party loggers
        for noisy in ("urllib3", "prawcore", "praw", "playwright", "patchright"):
            logging.getLogger(noisy).setLevel(logging.WARNING)
        root_logger = logging.getLogger()
        root_logger.addHandler(handler)
        root_logger.setLevel(logging.INFO)

        try:
            listing_type = "search" if search_q else listing_opt
            max_comments_arg = _max_comments_arg(
                collect_comments, all_replies, int(max_comments_val)
            )

            with st.status("Collecting…", expanded=True) as status_widget:
                if source_type == "fixture":
                    reddit_source: Any = FixtureRedditSource()
                    db = DedupDatabase(DB_PATH)
                    try:
                        for sub in selected_subs:
                            st.write(f"r/{sub} — fetching…")
                            run_id = make_collection_run_id()
                            request = CollectionRequest(
                                subreddit=sub,
                                listing_type=listing_type,
                                collection_run_id=run_id,
                                limit=int(limit_val),
                                sort=cfg.collection.sort,
                                time_filter=cfg.collection.time_filter,
                                search_query=search_q,
                                max_comments_per_submission=(
                                    None if max_comments_arg < 0 else max_comments_arg
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
                                data_dir=DATA_DIR,
                                db=db,
                                source_type=source_type,
                            )
                            results.append(
                                {
                                    "subreddit": sub,
                                    "submissions": ctx.actual_submissions_stored,
                                    "comments": ctx.comments_stored,
                                    "duplicates": ctx.duplicates_skipped,
                                }
                            )
                            st.write(
                                f"r/{sub} — "
                                f"{ctx.actual_submissions_stored} submissions, "
                                f"{ctx.comments_stored} comments stored "
                                f"({ctx.duplicates_skipped} duplicates skipped)"
                            )
                    finally:
                        db.close()
                else:
                    pid = _start_background_collect(
                        source=source_type,
                        subreddits=selected_subs,
                        listing=listing_type,
                        limit=int(limit_val),
                        search=search_q,
                        max_comments=max_comments_arg,
                        headed=headed,
                        captcha_wait=(
                            float(captcha_wait_val)
                            if wait_for_captcha and headed
                            else 0.0
                        ),
                    )
                    st.write(
                        f"Live scrape started (pid {pid}) for "
                        + ", ".join(f"r/{s}" for s in selected_subs)
                        + ". Use Pause / Stop below."
                    )
                    status_widget.update(label="Scrape running in background", state="running")
                    st.session_state["logs"] = logs
                    st.session_state["last_results"] = results
                    st.rerun()
                status_widget.update(label="Collection complete", state="complete")

        except Exception as exc:
            st.error(f"Collection failed: {exc}")
            logs.append(f"ERROR — {exc}")
        finally:
            root_logger.removeHandler(handler)

        st.session_state["logs"] = logs
        st.session_state["last_results"] = results

    @st.fragment(run_every=1.5)
    def _live_scrape_panel() -> None:
        running = scrape_is_running()
        ctrl = get_control()
        st.subheader("Live scrape")
        if running:
            state = "paused" if ctrl == "pause" else "running"
            st.info(f"Scraper is **{state}**. Pause waits between posts; Stop ends the run.")
            b1, b2, b3 = st.columns(3)
            if b1.button("Pause", disabled=ctrl == "pause", key="scrape_pause"):
                set_control("pause")
            if b2.button("Resume", disabled=ctrl == "run", key="scrape_resume"):
                set_control("run")
            if b3.button("Stop", type="primary", key="scrape_stop"):
                request_stop_and_kill()
            warn_slot = st.empty()
            img_slot = st.empty()
            _refresh_captcha_preview(warn_slot, img_slot)
            if LOG_FILE.exists():
                lines = LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines()
                with st.expander(f"Live log ({len(lines)} lines)", expanded=True):
                    st.code("\n".join(lines[-50:]), language=None)
        else:
            st.caption("No live scrape running. Start one from the sidebar.")

    _live_scrape_panel()

    # ---- Results summary ----
    prev_results: list[dict[str, Any]] = st.session_state["last_results"]
    if prev_results:
        c1, c2, c3 = st.columns(3)
        c1.metric("Submissions stored", sum(r["submissions"] for r in prev_results))
        c2.metric("Comments stored", sum(r["comments"] for r in prev_results))
        c3.metric("Duplicates skipped", sum(r["duplicates"] for r in prev_results))

        st.markdown("---")
        for r in prev_results:
            st.markdown(
                f"**r/{r['subreddit']}** — "
                f"{r['submissions']} submissions · "
                f"{r['comments']} comments · "
                f"{r['duplicates']} duplicates skipped"
            )
    elif not run_btn:
        st.info("Configure subreddits in the sidebar and click **Start Collection**.")

    # ---- Log output ----
    log_lines: list[str] = st.session_state["logs"]
    if log_lines:
        with st.expander(f"Logs ({len(log_lines)} lines)", expanded=False):
            st.code("\n".join(log_lines), language=None)


# ===========================================================================
# Tab 2: Data Browser
# ===========================================================================
with tab_data:
    records = _load_jsonl_records()

    with st.expander("Clear previous scrapes", expanded=False):
        st.caption(
            "Deletes all JSONL, manifests, and the SQLite index. "
            "Does not delete the Chrome profile."
        )
        if scrape_is_running():
            st.warning("Stop the live scrape before clearing.")
        else:
            confirm_clear = st.text_input(
                "Type CLEAR to enable delete",
                key="clear_confirm",
            )
            if st.button(
                "Clear previous scrapes",
                type="primary",
                disabled=confirm_clear.strip() != "CLEAR",
            ):
                stats = clear_collected_data(DATA_DIR)
                st.session_state["last_results"] = []
                st.session_state["logs"] = []
                st.success(
                    f"Deleted {stats['jsonl_files']} JSONL files, "
                    f"{stats['manifests']} manifests, "
                    f"{stats['db_files']} DB files."
                )
                st.rerun()

    if not records:
        st.info("No records yet. Run a collection first.")
    else:
        all_subs = sorted({r.get("subreddit", "") for r in records if r.get("subreddit")})

        fcol1, fcol2, fcol3 = st.columns([3, 2, 1])
        filter_subs = fcol1.multiselect("Subreddit", all_subs, default=all_subs)
        filter_type = fcol2.radio(
            "Record type", ["all", "submission", "comment"], horizontal=True
        )
        fcol3.metric("Total records", len(records))

        filtered = [
            r
            for r in records
            if r.get("subreddit") in filter_subs
            and (filter_type == "all" or r.get("record_type") == filter_type)
        ]

        posts_by_id: dict[str, dict[str, Any]] = {}
        for rec in records:
            if rec.get("record_type") == "submission":
                sid = rec.get("id") or rec.get("reddit_id")
                if sid:
                    posts_by_id[str(sid)] = rec
        enriched = [_enrich_relationships(rec, posts_by_id) for rec in filtered]
        enriched.sort(
            key=lambda r: (
                str(r.get("post_id") or r.get("id") or ""),
                0 if r.get("record_type") == "submission" else 1,
                int(r.get("depth") or 0),
                str(r.get("parent_id") or ""),
                str(r.get("created_utc") or ""),
            )
        )

        if filter_type == "submission":
            columns = _SUBMISSION_BROWSER_COLS
        elif filter_type == "comment":
            columns = _COMMENT_BROWSER_COLS
        else:
            columns = _ALL_BROWSER_COLS

        rows = [_browser_row(rec, columns) for rec in enriched]
        df = pd.DataFrame(rows, columns=columns)
        st.caption(
            f"{len(df)} records shown. Comments include post_id / post_url "
            "(which post) and parent_comment_id / parent_url (reply-to-reply)."
        )
        st.dataframe(
            df,
            width="stretch",
            height=560,
            column_config={
                "title": st.column_config.TextColumn("title", width="medium"),
                "selftext": st.column_config.TextColumn("selftext", width="medium"),
                "body": st.column_config.TextColumn("body", width="medium"),
                "post_title": st.column_config.TextColumn("post_title", width="medium"),
                "permalink": st.column_config.LinkColumn("permalink"),
                "url": st.column_config.LinkColumn("url"),
                "post_url": st.column_config.LinkColumn("post_url"),
                "parent_url": st.column_config.LinkColumn("parent_url"),
                "comment_url": st.column_config.LinkColumn("comment_url"),
            },
        )

        thread_src = enriched if filter_type == "all" else [
            _enrich_relationships(rec, posts_by_id)
            for rec in records
            if rec.get("subreddit") in filter_subs
        ]
        thread_text = _thread_lines(thread_src)
        if thread_text:
            with st.expander("Thread tree (post → replies → replies-to-replies)", expanded=False):
                st.text(thread_text)

        csv_bytes = df.to_csv(index=False).encode("utf-8")
        st.download_button(
            "Download as CSV",
            data=csv_bytes,
            file_name="uyam_export.csv",
            mime="text/csv",
        )


# ===========================================================================
# Tab 3: Run History
# ===========================================================================
with tab_history:
    runs = _load_runs()
    if not runs:
        st.info("No collection runs yet.")
    else:
        df_runs = pd.DataFrame(runs)
        # Shorten UUIDs so the table fits
        df_runs["run_id"] = df_runs["collection_run_id"].str[:8] + "…"
        df_runs = df_runs.drop(columns=["collection_run_id", "manifest_path"], errors="ignore")
        # Reorder for readability
        preferred_cols = [
            "run_id", "source_type", "subreddit", "started_at_utc", "finished_at_utc",
            "submissions_stored", "comments_stored", "duplicates_skipped",
            "submissions_seen", "comments_seen", "validation_failures",
        ]
        df_runs = df_runs[[c for c in preferred_cols if c in df_runs.columns]]
        st.dataframe(df_runs, width="stretch")
        st.caption(f"{len(df_runs)} runs total")


# ===========================================================================
# Tab 4: Stats
# ===========================================================================
with tab_stats:
    stats = _load_stats()
    if not stats:
        st.info("No data collected yet.")
    else:
        cols = st.columns(max(len(stats), 1))
        for i, s in enumerate(stats):
            with cols[i]:
                st.metric(
                    f"r/{s['subreddit']}",
                    f"{s['submissions']} posts",
                    delta=f"{s['comments']} comments",
                )

        st.divider()
        st.subheader("Submissions vs Comments")
        df_chart = pd.DataFrame(stats).set_index("subreddit")[["submissions", "comments"]]
        st.bar_chart(df_chart)

        st.divider()
        st.subheader("Last collection times")
        df_last = pd.DataFrame(stats)[["subreddit", "last_collection"]]
        df_last["last_collection"] = df_last["last_collection"].str[:19]
        st.dataframe(df_last, width="stretch", hide_index=True)
