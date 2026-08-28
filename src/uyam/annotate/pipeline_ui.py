"""Streamlit "Annotation" tab — run and monitor every annotation pass from the UI.

Fast passes (index, select, aggregate, gold-sample, export) run inline; long
passes (LID first-run download, GPU sentiment, LLM annotators, adjudication)
run as detached background jobs via uyam.annotate.jobs, with live log tails.

Imported lazily by app.py so the collector UI works without the annotate
extras installed (background jobs surface missing-dependency errors in their
own logs).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from uyam.annotate import jobs
from uyam.annotate.config import AnnotationConfig, load_annotation_config
from uyam.annotate.db import AnnotationDatabase

_PREP_JOBS = ("lid", "sentiment")


def _run_job_key(annotator_key: str) -> str:
    return f"run-{annotator_key}"


def _pipeline_snapshot(cfg: AnnotationConfig) -> dict[str, Any]:
    """One cheap read of everything the tab displays."""
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
    }
    if not snap["db_exists"]:
        return snap
    with AnnotationDatabase(cfg.db_path) as db:
        snap["corpus"] = db.corpus_counts()
        snap["eligible"] = len(db.eligible_fullnames())
        snap["lid_missing"] = len(db.missing_lid())
        snap["tx_missing"] = len(db.missing_tx_sentiment())
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
        snap["escalated_pending"] = len(
            db.pending_adjudicator_items(cfg.adjudicator.key, cfg.prompt_version)
        )
        queue = db.review_queue_items()
        snap["queue_gold"] = sum(1 for q in queue if q["reason"] == "gold")
        snap["queue_low_conf"] = len(queue) - snap["queue_gold"]
    return snap


def _job_button(
    st: Any,
    *,
    label: str,
    job_name: str,
    args: list[str],
    help_text: str,
    extra_disabled: bool = False,
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
            jobs.stop_job(job_name)
            st.rerun()
    if running:
        st.caption(f"⏳ running (pid {jobs.read_meta(job_name).get('pid')}) — resume-safe")


def render_annotation_tab(config_path: Path | None = None) -> None:
    import streamlit as st

    try:
        cfg = load_annotation_config(config_path)
    except (OSError, ValueError) as exc:
        st.error(f"Could not load config/annotation.yaml: {exc}")
        return

    snap = _pipeline_snapshot(cfg)
    local_annotators = [a for a in cfg.annotators if a.endpoint == "local"]
    endpoints_in_use = list(dict.fromkeys(a.endpoint for a in cfg.annotators))
    run_all_job_names = [f"run-all-{ep}" for ep in endpoints_in_use]
    local_run_jobs = [_run_job_key(a.key) for a in local_annotators] + ["run-all-local"]
    local_llm_busy = any(jobs.is_running(j) for j in local_run_jobs)
    sentiment_busy = jobs.is_running("sentiment")
    any_annotator_busy = local_llm_busy or any(
        jobs.is_running(j)
        for j in [_run_job_key(a.key) for a in cfg.annotators] + run_all_job_names
    )

    # ------------------------------------------------------------------
    # Status header
    # ------------------------------------------------------------------
    @st.fragment(run_every=5.0)
    def _live_status() -> None:
        live = _pipeline_snapshot(cfg)
        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("Corpus records", live["corpus"]["total"])
        c2.metric("Eligible candidates", live["eligible"])
        c3.metric("LID / sentiment missing", f"{live['lid_missing']} / {live['tx_missing']}")
        resolved = sum(n for s, n in live["aggregates"].items() if s != "unresolved")
        c4.metric("Resolved labels", resolved)
        c5.metric("Escalated pending", live["escalated_pending"])

        if live["progress"]:
            import pandas as pd

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

            with AnnotationDatabase(cfg.db_path) as db:
                stats = index_corpus(
                    db, cfg.data_dir, extra_bot_authors=cfg.candidate_filters.exclude_authors
                )
            st.success(f"Indexed {stats['records']} records from {stats['files']} files")
        if st.button(
            "Select candidates",
            help="Apply eligibility filters: bots, deleted, link posts, too short.",
        ):
            from uyam.annotate.candidates import select_candidates

            with AnnotationDatabase(cfg.db_path) as db:
                select_candidates(db, cfg.candidate_filters)
                counts = db.candidate_counts()
            st.success(f"Selection done — {counts.get('eligible', 0)} eligible")
            st.json(counts)
    with col_b:
        _job_button(
            st,
            label=f"Run language ID ({snap['lid_missing']} missing)",
            job_name="lid",
            args=["lid"],
            help_text="Two-stage fastText LID. First run downloads lid.176.bin (~130 MB).",
        )
        _job_button(
            st,
            label=f"Run transformer sentiment ({snap['tx_missing']} missing)",
            job_name="sentiment",
            args=["sentiment"],
            help_text="GPU pass — do not run while a LOCAL LLM annotator is running "
            "(they share the 8 GB card).",
        )
        if sentiment_busy and local_llm_busy:
            st.warning("Sentiment and a local LLM annotator are BOTH running — VRAM contention!")

    st.divider()

    # ------------------------------------------------------------------
    # 2. LLM annotators
    # ------------------------------------------------------------------
    st.subheader("2 · LLM annotators")

    # One-click run: every annotator, grouped per endpoint — sequential on the
    # same machine (shared GPU), concurrent across machines.
    all_col, count_col = st.columns([2, 1])
    with count_col:
        items_to_label = st.number_input(
            "Items per annotator",
            min_value=0,
            value=0,
            step=50,
            help="How many pending items each annotator labels this run. 0 = ALL pending "
            "(everything still required).",
        )
    with all_col:
        pending_note = "all pending" if items_to_label == 0 else f"{items_to_label} items"
        if st.button(
            f"▶ Run ALL annotators ({pending_note} each)",
            type="primary",
            disabled=any_annotator_busy,
            help="Starts one background job per machine: the remote annotator runs "
            "concurrently while the local annotators run one after the other. "
            "Resumable — stop or rerun anytime.",
        ):
            for endpoint in endpoints_in_use:
                keys = [a.key for a in cfg.annotators if a.endpoint == endpoint]
                args = ["run"]
                for key in keys:
                    args.extend(["--annotator", key])
                if items_to_label > 0:
                    args.extend(["--limit", str(int(items_to_label))])
                jobs.start_job(f"run-all-{endpoint}", args)
            st.rerun()
        if any(jobs.is_running(j) for j in run_all_job_names):
            running_all = [j for j in run_all_job_names if jobs.is_running(j)]
            if st.button("Stop all", key="stop_run_all"):
                for j in running_all:
                    jobs.stop_job(j)
                st.rerun()
            st.caption(f"⏳ running: {', '.join(running_all)} — see Job logs below")

    st.caption(
        "Or run annotators individually. The two local models cannot share the 8 GB GPU — "
        "run them one after the other. Every run is resumable: stopping mid-pass loses "
        "nothing."
    )
    ann_cols = st.columns(len(cfg.annotators))
    for col, ann in zip(ann_cols, cfg.annotators, strict=True):
        with col:
            endpoint_run_all_busy = jobs.is_running(f"run-all-{ann.endpoint}")
            other_local_busy = ann.endpoint == "local" and (
                jobs.is_running("run-all-local")
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
                st.caption("covered by the Run-ALL job")
            run_args = ["run", "--annotator", ann.key]
            if items_to_label > 0:
                run_args.extend(["--limit", str(int(items_to_label))])
            _job_button(
                st,
                label=f"Run {ann.key}",
                job_name=_run_job_key(ann.key),
                args=run_args,
                help_text=f"Annotate pending items with {ann.model} on {ann.endpoint}. "
                "Respects the items-per-annotator count above (0 = all).",
                extra_disabled=endpoint_run_all_busy,
            )

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

            stats = run_aggregation(cfg, cfg.prompt_version)
            st.success(f"Aggregated {stats['items']} items")
            st.json(stats)
        if st.button("Agreement report", help="Fleiss' kappa / Krippendorff / Cohen (gold)."):
            from uyam.annotate.aggregate import compute_agreement

            st.json(compute_agreement(cfg, cfg.prompt_version))
    with col_2:
        _job_button(
            st,
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

            result = run_export(cfg, cfg.prompt_version, dataset_version=version)
            st.success(f"Exported {result['rows']} rows")
            st.json(result)

    st.divider()

    # ------------------------------------------------------------------
    # 4. Annotated data (live from the DB — the export files are snapshots)
    # ------------------------------------------------------------------
    st.subheader("4 · Annotated data")
    if not snap["db_exists"]:
        df = None
    else:
        import pandas as pd

        with AnnotationDatabase(cfg.db_path) as db:
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
                ORDER BY g.sarcastic_final DESC, g.reddit_fullname
                """,
                db.conn,
            )
            rationales = pd.read_sql_query(
                """
                SELECT reddit_fullname, prompt_version, model_key, role,
                       sarcastic, literal_sentiment, intended_sentiment, rationale
                FROM llm_annotations
                """,
                db.conn,
            )
    if df is None or df.empty:
        st.info("No aggregated labels yet — run the annotators, then Aggregate votes.")
    else:
        df["sarcastic"] = df["sarcastic"].map({1: True, 0: False})

        # One "reason: <model>" column per annotator (+ adjudicator): the
        # model's verdict, its literal->intended sentiment, and its rationale.
        if not rationales.empty:
            rationales["column"] = rationales.apply(
                lambda r: "reason: adjudicator"
                if r["role"] == "adjudicator"
                else f"reason: {r['model_key']}",
                axis=1,
            )
            rationales["reason"] = rationales.apply(
                lambda r: (
                    f"[{'sarcastic' if r['sarcastic'] else 'not sarcastic'}; "
                    f"{r['literal_sentiment']}→{r['intended_sentiment']}] {r['rationale'] or ''}"
                ),
                axis=1,
            )
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

        def _link_row(r: Any) -> pd.Series:
            url = _abs_url(r["permalink"])
            if r["record_type"] == "submission":
                return pd.Series({"reply_to": None, "url": url, "parent_url": None,
                                  "post_url": url})
            sub_id = str(r["submission_fullname"] or "")[3:]
            post_url = _abs_url(r["post_permalink"]) or (
                f"https://www.reddit.com/r/{r['subreddit']}/comments/{sub_id}/"
                if sub_id
                else None
            )
            parent = str(r["parent_fullname"] or "")
            if parent.startswith("t1_"):
                reply_to = "comment"
                parent_url = (
                    f"https://www.reddit.com/r/{r['subreddit']}/comments/"
                    f"{sub_id}/comment/{parent[3:]}/"
                )
            else:
                reply_to = "post"
                parent_url = post_url
            return pd.Series(
                {"reply_to": reply_to, "url": url, "parent_url": parent_url,
                 "post_url": post_url}
            )

        df = pd.concat([df, df.apply(_link_row, axis=1)], axis=1)
        leading = [
            "reddit_fullname", "subreddit", "record_type", "reply_to", "post_title",
            "text", "url", "parent_url", "post_url",
            "sarcastic", "language", "literal", "intended", "votes",
            "resolved_by", "confidence", "sampling_strategy", "prompt_version",
        ]
        reason_cols = sorted(c for c in df.columns if str(c).startswith("reason: "))
        df = df[[*leading, *reason_cols]]

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
            view = view[view["text"].str.contains(text_query, case=False, na=False)]

        st.caption(
            f"{len(view)} of {len(df)} labeled items — "
            f"{int(df['sarcastic'].sum())} sarcastic overall "
            f"({100 * df['sarcastic'].mean():.0f}%)"
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
            view,
            width="stretch",
            hide_index=True,
            height=400,
            column_config=column_config,
        )
        st.download_button(
            "Download filtered rows (CSV)",
            view.to_csv(index=False).encode("utf-8-sig"),
            file_name="annotated-review.csv",
            mime="text/csv",
        )

    st.divider()

    # ------------------------------------------------------------------
    # Job logs
    # ------------------------------------------------------------------
    st.subheader("Job logs")
    job_names = [
        "lid",
        "sentiment",
        *run_all_job_names,
        *[_run_job_key(a.key) for a in cfg.annotators],
        "adjudicate",
    ]
    active = jobs.running_jobs()
    chosen = st.selectbox(
        "Job",
        job_names,
        index=job_names.index(active[0]) if active and active[0] in job_names else 0,
        format_func=lambda n: f"{n} {'● running' if n in active else ''}",
    )

    @st.fragment(run_every=3.0)
    def _live_log() -> None:
        tail = jobs.tail_log(chosen)
        st.code(tail or "(no log yet)", language="text")

    _live_log()
