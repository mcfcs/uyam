"""Streamlit "Annotation" tab — run and monitor every annotation pass from the UI.

Fast passes (index, select, aggregate, gold-sample, export) run inline; long
passes (LID first-run download, GPU sentiment, LLM annotators, adjudication)
run as detached background jobs via uyam.annotate.jobs, with live log tails.

Imported lazily by app.py so the collector UI works without the annotate
extras installed (background jobs surface missing-dependency errors in their
own logs).

Performance: the status header only runs COUNT queries; the job controls live
in a slow fragment so their enabled/disabled state follows running jobs; the
annotated-data table is opt-in, cached, and paginated (it is the one payload
that is many MB over Tailscale).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import streamlit as st

from uyam.annotate import jobs
from uyam.annotate.config import AnnotationConfig, load_annotation_config
from uyam.annotate.db import AnnotationDatabase
from uyam.annotate.pipeline import PIPELINE_JOB, lane_job_name

_PAGE_SIZE = 200


def _run_job_key(annotator_key: str) -> str:
    return f"run-{annotator_key}"


def _pipeline_snapshot(cfg: AnnotationConfig, *, target: int | None = None) -> dict[str, Any]:
    """One cheap read (COUNT queries only) of everything the tab displays."""
    snap: dict[str, Any] = {
        "db_exists": cfg.db_path.exists(),
        "corpus": {"total": 0, "submissions": 0, "comments": 0},
        "eligible": 0,
        "lid_missing": 0,
        "tx_missing": 0,
        "progress": [],
        "failures": {},
        "aggregates": {},
        "escalated_pending": 0,
        "queue_gold": 0,
        "queue_low_conf": 0,
        "target": None,
    }
    if not snap["db_exists"]:
        return snap
    with AnnotationDatabase(cfg.db_path) as db:
        snap["corpus"] = db.corpus_counts()
        snap["target"] = db.target_progress(
            cfg.prompt_version,
            int(target or cfg.pipeline.target_items),
            [a.key for a in cfg.annotators],
        )
        snap["eligible"] = db.eligible_count()
        snap["lid_missing"] = db.missing_lid_count()
        snap["tx_missing"] = db.missing_tx_sentiment_count()
        snap["progress"] = db.model_progress(cfg.prompt_version)
        snap["failures"] = {
            (f["model_key"], f["role"]): f["failed"]
            for f in db.failure_counts(cfg.prompt_version)
        }
        rows = db.conn.execute(
            """
            SELECT COALESCE(resolved_by, 'unresolved') AS status, COUNT(*) AS n
            FROM aggregates WHERE prompt_version = ? GROUP BY status
            """,
            (cfg.prompt_version,),
        ).fetchall()
        snap["aggregates"] = {str(r["status"]): int(r["n"]) for r in rows}
        snap["escalated_pending"] = db.pending_adjudicator_count(
            cfg.adjudicator.key, cfg.prompt_version
        )
        queue = db.review_queue_counts()
        snap["queue_gold"] = queue["gold"]
        snap["queue_low_conf"] = queue["low_confidence"]
    return snap


def _job_button(
    *,
    label: str,
    job_name: str,
    args: list[str],
    help_text: str,
    extra_disabled: bool = False,
    stop_tree: bool = False,
) -> None:
    """A start/stop button pair for one background job."""
    running = jobs.is_running(job_name)
    col_run, col_stop = st.columns([4, 1])
    with col_run:
        if st.button(
            label, key=f"start_{job_name}", disabled=running or extra_disabled, help=help_text
        ):
            jobs.start_job(job_name, args)
            st.rerun()
    with col_stop:
        if running and st.button("Stop", key=f"stop_{job_name}"):
            if stop_tree:
                jobs.stop_job_tree(job_name)
            else:
                jobs.stop_job(job_name)
            st.rerun()
    if running:
        st.caption(f"⏳ running (pid {jobs.read_meta(job_name).get('pid')}) — resume-safe")


def _paginate(df: Any, key: str, page_size: int = _PAGE_SIZE) -> Any:
    import math

    total = len(df)
    pages = max(1, math.ceil(total / page_size))
    if pages == 1:
        return df
    if int(st.session_state.get(key, 1) or 1) > pages:
        st.session_state[key] = pages
    c1, c2 = st.columns([1, 5])
    page = int(c1.number_input("Page", min_value=1, max_value=pages, value=1, key=key))
    start = (page - 1) * page_size
    end = min(total, start + page_size)
    c2.caption(f"rows {start + 1}–{end} of {total} ({page_size} per page)")
    return df.iloc[start:end]


@st.cache_resource(show_spinner="Loading annotated data…", ttl=600, max_entries=2)
def _annotated_frame(db_path: str, prompt_version: str, nonce: int) -> Any:
    """Aggregated labels + one 'reason: <model>' column per annotator.

    Cached (per nonce) because it joins the whole aggregates table with every
    rationale — the heaviest read in the app. Callers must not mutate it.
    """
    import pandas as pd

    with AnnotationDatabase(Path(db_path)) as db:
        df = pd.read_sql_query(
            """
            SELECT g.reddit_fullname, c.subreddit, c.record_type,
                   c.text,
                   g.sarcastic_final AS sarcastic, g.language_final AS language,
                   g.literal_final AS literal, g.intended_final AS intended,
                   g.sarcasm_votes AS votes,
                   COALESCE(g.resolved_by, 'unresolved') AS resolved_by,
                   g.mean_confidence AS confidence,
                   c.sampling_strategy, g.prompt_version,
                   c.permalink, c.parent_fullname, c.submission_fullname,
                   s.title AS post_title, s.permalink AS post_permalink
            FROM aggregates g
            JOIN corpus_index c USING (reddit_fullname)
            LEFT JOIN corpus_index s ON s.reddit_fullname = c.submission_fullname
            WHERE g.prompt_version = ?
            ORDER BY g.sarcastic_final DESC, g.reddit_fullname
            """,
            db.conn,
            params=(prompt_version,),
        )
        rationales = pd.read_sql_query(
            """
            SELECT reddit_fullname, prompt_version, model_key, role,
                   sarcastic, literal_sentiment, intended_sentiment, rationale
            FROM llm_annotations WHERE prompt_version = ?
            """,
            db.conn,
            params=(prompt_version,),
        )
    if df.empty:
        return df

    df["sarcastic"] = df["sarcastic"].map({1: True, 0: False})

    # One "reason: <model>" column per annotator (+ adjudicator): the
    # model's verdict, its literal->intended sentiment, and its rationale.
    if not rationales.empty:
        rationales["column"] = [
            "reason: adjudicator" if role == "adjudicator" else f"reason: {key}"
            for role, key in zip(rationales["role"], rationales["model_key"], strict=True)
        ]
        rationales["reason"] = [
            f"[{'sarcastic' if sarc else 'not sarcastic'}; {lit}→{intended}] {rat or ''}"
            for sarc, lit, intended, rat in zip(
                rationales["sarcastic"],
                rationales["literal_sentiment"],
                rationales["intended_sentiment"],
                rationales["rationale"],
                strict=True,
            )
        ]
        pivot = rationales.pivot_table(
            index=["reddit_fullname", "prompt_version"],
            columns="column",
            values="reason",
            aggfunc="first",
        ).reset_index()
        df = df.merge(pivot, on=["reddit_fullname", "prompt_version"], how="left")

    # Post-to-reply links, mirroring the Data Browser enrichment: the item's
    # own URL, its immediate parent, and the thread's submission.
    def _abs_url(permalink: Any) -> str | None:
        if not permalink:
            return None
        pl = str(permalink)
        return pl if pl.startswith("http") else f"https://www.reddit.com{pl}"

    urls: list[str | None] = []
    parent_urls: list[str | None] = []
    post_urls: list[str | None] = []
    reply_tos: list[str | None] = []
    for r in df.itertuples(index=False):
        url = _abs_url(r.permalink)
        if r.record_type == "submission":
            urls.append(url)
            parent_urls.append(None)
            post_urls.append(url)
            reply_tos.append(None)
            continue
        sub_id = str(r.submission_fullname or "")[3:]
        post_url = _abs_url(r.post_permalink) or (
            f"https://www.reddit.com/r/{r.subreddit}/comments/{sub_id}/" if sub_id else None
        )
        parent = str(r.parent_fullname or "")
        if parent.startswith("t1_"):
            reply_tos.append("comment")
            parent_urls.append(
                f"https://www.reddit.com/r/{r.subreddit}/comments/{sub_id}/comment/{parent[3:]}/"
            )
        else:
            reply_tos.append("post")
            parent_urls.append(post_url)
        urls.append(url)
        post_urls.append(post_url)
    df["url"] = urls
    df["parent_url"] = parent_urls
    df["post_url"] = post_urls
    df["reply_to"] = reply_tos

    leading = [
        "reddit_fullname", "subreddit", "record_type", "reply_to", "post_title",
        "text", "url", "parent_url", "post_url",
        "sarcastic", "language", "literal", "intended", "votes",
        "resolved_by", "confidence", "sampling_strategy", "prompt_version",
    ]
    reason_cols = sorted(c for c in df.columns if str(c).startswith("reason: "))
    return df[[*leading, *reason_cols]]


def _render_annotated_data(cfg: AnnotationConfig, snap: dict[str, Any]) -> None:
    labeled = sum(int(n) for n in snap["aggregates"].values())
    head_l, head_r = st.columns([5, 1])
    with head_l:
        show = st.toggle(
            f"Show annotated data table ({labeled} labeled items)",
            value=False,
            key="show_annotated",
            help="Loads the full aggregates table with every model rationale. "
            "Cached for 10 minutes; use Refresh to pull new labels.",
        )
    with head_r:
        if st.button("Refresh", key="annotated_refresh", use_container_width=True):
            st.session_state["annotated_nonce"] = (
                int(st.session_state.get("annotated_nonce", 0)) + 1
            )
    if not show:
        return
    if not snap["db_exists"] or labeled == 0:
        st.info("No aggregated labels yet — run the annotators, then Aggregate votes.")
        return

    df = _annotated_frame(
        str(cfg.db_path), cfg.prompt_version, int(st.session_state.get("annotated_nonce", 0))
    )
    if df.empty:
        st.info("No aggregated labels yet — run the annotators, then Aggregate votes.")
        return

    f1, f2, f3, f4 = st.columns([1, 1, 1, 2])
    with f1:
        sarc_filter = st.selectbox("Sarcastic", ["all", "yes", "no"], key="tbl_sarc")
    with f2:
        lang_filter = st.multiselect(
            "Language", sorted(df["language"].dropna().unique()), key="tbl_lang"
        )
    with f3:
        res_filter = st.multiselect(
            "Resolved by", sorted(df["resolved_by"].unique()), key="tbl_res"
        )
    with f4:
        text_query = st.text_input("Search text", key="tbl_query")

    view = df
    if sarc_filter != "all":
        view = view[view["sarcastic"] == (sarc_filter == "yes")]
    if lang_filter:
        view = view[view["language"].isin(lang_filter)]
    if res_filter:
        view = view[view["resolved_by"].isin(res_filter)]
    if text_query:
        view = view[view["text"].str.contains(text_query, case=False, na=False, regex=False)]

    n_sarc = int(df["sarcastic"].eq(True).sum())
    st.caption(
        f"{len(view)} of {len(df)} labeled items — "
        f"{n_sarc} sarcastic overall ({100 * n_sarc / max(1, len(df)):.0f}%)"
    )
    column_config = {
        "text": st.column_config.TextColumn("text", width="large"),
        "confidence": st.column_config.NumberColumn(format="%.2f"),
        "post_title": st.column_config.TextColumn("post_title", width="medium"),
        "url": st.column_config.LinkColumn(
            "url", display_text="open", help="This submission/comment on Reddit"
        ),
        "parent_url": st.column_config.LinkColumn(
            "parent_url", display_text="parent", help="The comment/post being replied to"
        ),
        "post_url": st.column_config.LinkColumn(
            "post_url", display_text="post", help="The thread's submission"
        ),
    }
    for col in view.columns:
        if str(col).startswith("reason: "):
            column_config[str(col)] = st.column_config.TextColumn(
                str(col),
                width="large",
                help="This model's verdict, literal→intended sentiment, and rationale. "
                "Click a cell to read the full text.",
            )
    st.dataframe(
        _paginate(view, key="annotated_page"),
        width="stretch",
        hide_index=True,
        height=400,
        column_config=column_config,
    )
    if st.button("Prepare CSV of filtered rows", key="annotated_csv_prepare"):
        st.download_button(
            "Download filtered rows (CSV)",
            view.to_csv(index=False).encode("utf-8-sig"),
            file_name="annotated-review.csv",
            mime="text/csv",
            key="annotated_csv_download",
        )


def render_annotation_tab(config_path: Path | None = None) -> None:
    try:
        cfg = load_annotation_config(config_path)
    except (OSError, ValueError) as exc:
        st.error(f"Could not load config/annotation.yaml: {exc}")
        return

    snap = _pipeline_snapshot(cfg)
    local_annotators = [a for a in cfg.annotators if a.endpoint == "local"]
    endpoints_in_use = list(dict.fromkeys(a.endpoint for a in cfg.annotators))
    run_all_job_names = [f"run-all-{ep}" for ep in endpoints_in_use]

    # ------------------------------------------------------------------
    # Status header (cheap COUNT queries; slow timer)
    # ------------------------------------------------------------------
    @st.fragment(run_every=10.0)
    def _live_status() -> None:
        import pandas as pd

        ui_target = int(st.session_state.get("target_items") or cfg.pipeline.target_items)
        live = _pipeline_snapshot(cfg, target=ui_target)
        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("Corpus records", live["corpus"]["total"])
        c2.metric("Eligible candidates", live["eligible"])
        c3.metric("LID / sentiment missing", f"{live['lid_missing']} / {live['tx_missing']}")
        resolved = sum(n for s, n in live["aggregates"].items() if s != "unresolved")
        c4.metric("Resolved labels", resolved)
        c5.metric("Escalated pending", live["escalated_pending"])

        tp = live["target"]
        if tp:
            t1, t2 = st.columns([1, 3])
            t1.metric(
                f"Complete of {tp['in_target']:,} target items",
                tp["complete"],
                help="Target-set items that carry EVERY annotator's vote. "
                "The Start ALL chain stops when each annotator reaches the target.",
            )
            with t2:
                st.dataframe(
                    pd.DataFrame(
                        [
                            {
                                "annotator": key,
                                "done in target": m["done"],
                                "failed": m["failed"],
                                "pending to target": m["pending"],
                            }
                            for key, m in tp["models"].items()
                        ]
                    ),
                    width="stretch",
                    hide_index=True,
                )

        if live["progress"]:
            rows = []
            for p in live["progress"]:
                key, role = str(p["model_key"]), str(p["role"])
                done = int(p["done"])
                pending = max(0, live["eligible"] - done) if role == "annotator" else "-"
                rows.append(
                    {
                        "model": key,
                        "role": role,
                        "done": done,
                        "failed": live["failures"].get((key, role), 0),
                        "pending": pending,
                        "avg s/item": round((p["avg_ms"] or 0) / 1000, 1),
                    }
                )
            st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)
        active = jobs.running_jobs()
        st.caption(
            "Running jobs: " + (", ".join(active) if active else "none")
            + " — refreshes every 10 s"
        )

    _live_status()
    st.divider()

    # ------------------------------------------------------------------
    # 1. Prepare data (index/select inline; lid/sentiment background)
    # ------------------------------------------------------------------
    st.subheader("1 · Prepare data")
    col_a, col_b = st.columns(2)
    with col_a:
        if st.button(
            "Index corpus",
            help="Load every raw JSONL record into the annotation database (idempotent).",
        ):
            from uyam.annotate.corpus import index_corpus

            with st.spinner("Indexing raw JSONL…"), AnnotationDatabase(cfg.db_path) as db:
                stats = index_corpus(
                    db, cfg.data_dir, extra_bot_authors=cfg.candidate_filters.exclude_authors
                )
            st.success(f"Indexed {stats['records']} records from {stats['files']} files")
        if st.button(
            "Select candidates",
            help="Apply eligibility filters: bots, deleted, link posts, too short.",
        ):
            from uyam.annotate.candidates import select_candidates

            with st.spinner("Selecting candidates…"), AnnotationDatabase(cfg.db_path) as db:
                select_candidates(db, cfg.candidate_filters)
                counts = db.candidate_counts()
            st.success(f"Selection done — {counts.get('eligible', 0)} eligible")
            st.json(counts)
    with col_b:
        _job_button(
            label=f"Run language ID ({snap['lid_missing']} missing)",
            job_name="lid",
            args=["lid"],
            help_text="Two-stage fastText LID. First run downloads lid.176.bin (~130 MB).",
        )
        _job_button(
            label=f"Run transformer sentiment ({snap['tx_missing']} missing)",
            job_name="sentiment",
            args=["sentiment"],
            help_text="GPU pass — do not run while a LOCAL LLM annotator is running "
            "(they share the 8 GB card).",
        )

    st.divider()

    # ------------------------------------------------------------------
    # 2. LLM annotators — a fragment so enabled/disabled follows the jobs
    # ------------------------------------------------------------------
    st.subheader("2 · LLM annotators")

    @st.fragment(run_every=5.0)
    def _annotator_controls() -> None:
        local_lanes = [lane_job_name(ep) for ep in endpoints_in_use if cfg.is_local_endpoint(ep)]
        local_run_jobs = [_run_job_key(a.key) for a in local_annotators] + local_lanes
        pipeline_running = jobs.is_running(PIPELINE_JOB)
        local_llm_busy = any(jobs.is_running(j) for j in local_run_jobs)
        sentiment_busy = jobs.is_running("sentiment")
        any_annotator_busy = local_llm_busy or any(
            jobs.is_running(j)
            for j in [_run_job_key(a.key) for a in cfg.annotators] + run_all_job_names
        )
        chain_busy = (
            pipeline_running
            or any_annotator_busy
            or sentiment_busy
            or jobs.is_running("adjudicate")
        )
        if sentiment_busy and local_llm_busy and not pipeline_running:
            st.warning("Sentiment and a local LLM annotator are BOTH running — VRAM contention!")

        # ---- Start ALL: the whole chain to the target ----------------------
        t_col, s_col = st.columns([1, 2])
        with t_col:
            target = int(
                st.number_input(
                    "Target items per annotator",
                    min_value=1,
                    value=int(cfg.pipeline.target_items),
                    step=500,
                    key="target_items",
                    help="Every annotator labels the SAME set of this many eligible items "
                    "(items that already have votes first, then by id). On each machine "
                    "the least-annotated model runs first, and each model stops once it "
                    "has this many done. Default: annotation.yaml pipeline.target_items.",
                )
            )
        with s_col:
            o1, o2, o3, o4 = st.columns(4)
            skip_prep = o1.checkbox(
                "skip prep", key="pl_skip_prep", help="Skip index / select / language ID."
            )
            skip_sent = o2.checkbox(
                "skip sentiment",
                key="pl_skip_sent",
                help="Skip the GPU transformer-sentiment pass over the target set.",
            )
            skip_adj = o3.checkbox(
                "skip adjudicate", key="pl_skip_adj", help="Stop after aggregation."
            )
            retry_failed = o4.checkbox(
                "retry failed",
                key="pl_retry",
                help="Clear recorded failures and retry those items in every pass.",
            )
            if pipeline_running:
                if st.button("■ Stop ALL", type="primary", key="stop_pipeline"):
                    jobs.stop_job_tree(PIPELINE_JOB)
                    st.rerun()
                kids = [j for j in jobs.child_jobs(PIPELINE_JOB) if jobs.is_running(j)]
                st.caption(
                    f"⏳ pipeline running (pid {jobs.read_meta(PIPELINE_JOB).get('pid')}) — "
                    f"child jobs: {', '.join(kids) or 'none yet'} — "
                    "phases and progress in the pipeline job log below"
                )
            else:
                if st.button(
                    f"▶ Start ALL — full chain to {target:,} items per annotator",
                    type="primary",
                    key="start_pipeline",
                    disabled=chain_busy,
                    help="index → select → language ID → transformer sentiment on the "
                    "target set (local GPU) → annotators (remote lane concurrently; local "
                    "lane least-done-first, each to the target) → aggregate → adjudicate "
                    "→ aggregate → gold sample. Resumable: Start again to continue.",
                ):
                    args = ["pipeline", "--target", str(target)]
                    if skip_prep:
                        args.append("--skip-prep")
                    if skip_sent:
                        args.append("--skip-sentiment")
                    if skip_adj:
                        args.append("--skip-adjudicate")
                    if retry_failed:
                        args.append("--retry-failed")
                    jobs.start_job(PIPELINE_JOB, args)
                    st.rerun()
                if chain_busy:
                    st.caption(
                        "Start ALL waits until the running annotation job finishes "
                        "(or stop it below)."
                    )

        st.divider()

        # ---- Annotators only: same target, one lane per machine ------------
        st.markdown("**Annotators only** — same target set, no prep or adjudication:")
        all_col, count_col = st.columns([2, 1])
        with count_col:
            items_to_label = int(
                st.number_input(
                    "Max items per annotator this run",
                    min_value=0,
                    value=0,
                    step=50,
                    key="items_per_annotator",
                    help="0 = keep going until the target. Otherwise stop after this many "
                    "items this run (resumable).",
                )
            )
        with all_col:
            if st.button(
                f"▶ Run ALL annotators to {target:,}",
                key="run_all_annotators",
                disabled=chain_busy,
                help="One background job per machine: the remote annotator runs "
                "concurrently while the local annotators run one after the other, "
                "least-done first. Resumable — stop or rerun anytime.",
            ):
                for endpoint in endpoints_in_use:
                    args = ["run"]
                    for ann in cfg.annotators:
                        if ann.endpoint == endpoint:
                            args.extend(["--annotator", ann.key])
                    args.extend(["--target", str(target)])
                    if items_to_label > 0:
                        args.extend(["--limit", str(items_to_label)])
                    jobs.start_job(lane_job_name(endpoint), args)
                st.rerun()
            running_all = [j for j in run_all_job_names if jobs.is_running(j)]
            if running_all and not pipeline_running:
                if st.button("Stop all", key="stop_run_all"):
                    for j in running_all:
                        jobs.stop_job(j)
                    st.rerun()
                st.caption(f"⏳ running: {', '.join(running_all)} — see Job logs below")

        st.caption(
            "Or run annotators individually (to the target above). The two local models "
            "cannot share the 8 GB GPU — run them one after the other. Every run is "
            "resumable: stopping mid-pass loses nothing."
        )
        ann_cols = st.columns(len(cfg.annotators))
        for col, ann in zip(ann_cols, cfg.annotators, strict=True):
            with col:
                endpoint_run_all_busy = jobs.is_running(lane_job_name(ann.endpoint))
                other_local_busy = cfg.is_local_endpoint(ann.endpoint) and (
                    any(jobs.is_running(j) for j in local_lanes)
                    or any(
                        jobs.is_running(_run_job_key(a.key))
                        for a in local_annotators
                        if a.key != ann.key
                    )
                )
                st.markdown(f"**{ann.key}** · `{ann.model}` · {ann.endpoint}")
                if other_local_busy:
                    st.caption("waiting: another local model holds the GPU")
                elif endpoint_run_all_busy:
                    st.caption("covered by the lane job")
                run_args = ["run", "--annotator", ann.key, "--target", str(target)]
                if items_to_label > 0:
                    run_args.extend(["--limit", str(items_to_label)])
                _job_button(
                    label=f"Run {ann.key}",
                    job_name=_run_job_key(ann.key),
                    args=run_args,
                    help_text=f"Annotate target-set items with {ann.model} on {ann.endpoint} "
                    "until it reaches the target (or the per-run cap above).",
                    extra_disabled=endpoint_run_all_busy or pipeline_running,
                )

    _annotator_controls()

    st.divider()

    # ------------------------------------------------------------------
    # 3. Aggregate, adjudicate, review, export
    # ------------------------------------------------------------------
    st.subheader("3 · Aggregate → adjudicate → review → export")
    col_1, col_2, col_3 = st.columns(3)
    with col_1:
        if st.button(
            "Aggregate votes",
            help="Majority vote + escalation queue. Run before AND after adjudication.",
        ):
            from uyam.annotate.aggregate import run_aggregation

            with st.spinner("Aggregating votes…"):
                stats = run_aggregation(cfg, cfg.prompt_version)
            st.success(f"Aggregated {stats['items']} items")
            st.json(stats)
        if st.button("Agreement report", help="Fleiss' kappa / Krippendorff / Cohen (gold)."):
            from uyam.annotate.aggregate import compute_agreement

            with st.spinner("Computing agreement…"):
                st.json(compute_agreement(cfg, cfg.prompt_version))
    with col_2:
        _job_button(
            label=f"Adjudicate ({snap['escalated_pending']} escalated)",
            job_name="adjudicate",
            args=["adjudicate"],
            help_text=f"Blind re-annotation of disagreements by {cfg.adjudicator.model} "
            f"on {cfg.adjudicator.endpoint}. Aggregate again afterwards.",
        )
        if st.button(
            f"Draw gold sample ({cfg.review.gold_size})",
            help="Stratified human-validation subset -> 'Annotation Review' tab.",
        ):
            from uyam.annotate.review import sample_gold_subset

            stats = sample_gold_subset(cfg, cfg.prompt_version)
            st.success(
                f"Sampled {stats['sampled']} items across {stats['strata']} strata — "
                "label them in the Annotation Review tab"
            )
        st.caption(
            f"Human queue: {snap['queue_gold']} gold, {snap['queue_low_conf']} low-confidence"
        )
    with col_3:
        version = st.text_input("Dataset version", value="v1", key="export_version")
        if st.button("Export dataset", help="dataset.jsonl + parquet + corpus dump + card."):
            from uyam.annotate.export import run_export

            with st.spinner("Exporting…"):
                result = run_export(cfg, cfg.prompt_version, dataset_version=version)
            st.success(f"Exported {result['rows']} rows")
            st.json(result)

    st.divider()

    # ------------------------------------------------------------------
    # 4. Annotated data (opt-in, cached, paginated)
    # ------------------------------------------------------------------
    st.subheader("4 · Annotated data")
    _render_annotated_data(cfg, snap)

    st.divider()

    # ------------------------------------------------------------------
    # Job logs (one fragment: picking a job reruns only this block)
    # ------------------------------------------------------------------
    st.subheader("Job logs")
    job_names = [
        PIPELINE_JOB,
        "lid",
        "sentiment",
        *run_all_job_names,
        *[_run_job_key(a.key) for a in cfg.annotators],
        "adjudicate",
    ]

    @st.fragment(run_every=5.0)
    def _live_log() -> None:
        active = jobs.running_jobs()
        chosen = st.selectbox(
            "Job",
            job_names,
            index=job_names.index(active[0]) if active and active[0] in job_names else 0,
            key="job_log_choice",
            format_func=lambda n: f"{n} {'● running' if n in active else ''}",
        )
        tail = jobs.tail_log(chosen)
        st.code(tail or "(no log yet)", language="text")

    _live_log()
