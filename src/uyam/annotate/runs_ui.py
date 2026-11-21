"""Streamlit "Runs & Datasets" tab: every annotation run and every exported dataset.

Each run lives in its own database (the original dataset-v1/v2 run in
annotation.sqlite3, the dataset-v3 re-annotation in annotation-v3.sqlite3),
so an old run stays usable whatever happens to a new one. This tab shows them
side by side, lets you open one for its progress, how it was made, its labels
or its latest votes, and lists every exported dataset version.

Everything here is read-only: the databases are opened with SQLite mode=ro.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from uyam.annotate.config import AnnotationConfig, load_annotation_config
from uyam.annotate.run_status import (
    discover_runs,
    exported_datasets,
    lane_log_tail,
    run_status,
    run_summary,
)
from uyam.annotate.run_status_ui import _ago, render_status

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_PAGE = 100


def _ro(cfg: AnnotationConfig) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{cfg.db_path.as_posix()}?mode=ro", uri=True, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def label_page(
    cfg: AnnotationConfig, *, page: int = 1, sarcastic_only: bool = False, month: str = ""
) -> tuple[list[dict[str, Any]], int]:
    """One page of a run's shipped labels (read-only), plus the total count."""
    where = ["g.prompt_version = ?", "g.sarcastic_final IS NOT NULL"]
    params: list[Any] = [cfg.prompt_version]
    if sarcastic_only:
        where.append("g.sarcastic_final = 1")
    if month:
        where.append("substr(c.created_utc, 1, 7) = ?")
        params.append(month)
    sql_where = " AND ".join(where)
    conn = _ro(cfg)
    try:
        total = conn.execute(
            f"SELECT COUNT(*) FROM aggregates g JOIN corpus_index c USING (reddit_fullname) "
            f"WHERE {sql_where}",
            params,
        ).fetchone()[0]
        rows = conn.execute(
            f"""
            SELECT g.reddit_fullname, c.subreddit, substr(c.created_utc, 1, 10) AS day,
                   g.sarcastic_final AS sarcastic, g.sarcasm_votes AS votes,
                   g.language_final AS language, g.intended_final AS intended,
                   COALESCE(g.resolved_by, '-') AS resolved_by, c.text
            FROM aggregates g JOIN corpus_index c USING (reddit_fullname)
            WHERE {sql_where}
            ORDER BY g.reddit_fullname LIMIT ? OFFSET ?
            """,
            [*params, _PAGE, (max(1, page) - 1) * _PAGE],
        ).fetchall()
    finally:
        conn.close()
    out = []
    for r in rows:
        row = dict(r)
        row["sarcastic"] = bool(row["sarcastic"])
        text = str(row.pop("text") or "")
        row["text"] = text if len(text) <= 240 else text[:240] + "…"
        out.append(row)
    return out, int(total)


def latest_votes(cfg: AnnotationConfig, limit: int = 30) -> list[dict[str, Any]]:
    """The newest annotator votes of this run's lineup, with the item text (read-only)."""
    marks = ",".join("?" for _ in cfg.annotator_keys)
    conn = _ro(cfg)
    try:
        rows = conn.execute(
            f"""
            SELECT a.created_at, a.model_key, a.reddit_fullname, a.sarcastic, a.language,
                   a.intended_sentiment AS intended, a.confidence, c.text
            FROM llm_annotations a JOIN corpus_index c USING (reddit_fullname)
            WHERE a.role = 'annotator' AND a.prompt_version = ? AND a.model_key IN ({marks})
            ORDER BY a.created_at DESC LIMIT ?
            """,
            (cfg.prompt_version, *cfg.annotator_keys, limit),
        ).fetchall()
    finally:
        conn.close()
    out = []
    for r in rows:
        row = dict(r)
        row["sarcastic"] = bool(row["sarcastic"])
        text = str(row.pop("text") or "")
        row["text"] = text if len(text) <= 160 else text[:160] + "…"
        out.append(row)
    return out


def render_runs_tab(config_dir: Path | None = None, annotated_dir: Path | None = None) -> None:
    import pandas as pd  # type: ignore[import-untyped]
    import streamlit as st

    now = datetime.now(UTC)
    paths = discover_runs(config_dir or _REPO_ROOT / "config")
    configs: dict[str, AnnotationConfig] = {}
    summaries = []
    for path in paths:
        try:
            cfg = load_annotation_config(path)
            summary = run_summary(cfg)
        except Exception as exc:  # noqa: BLE001 - one broken run must not hide the others
            st.warning(f"{path.name}: unavailable ({exc})")
            continue
        configs[summary["run"]] = cfg
        summaries.append(summary)

    st.subheader("Annotation runs")
    st.caption(
        "Each run has its own database, so runs never share or overwrite labels: "
        "the original run stays usable whatever happens to a newer one."
    )
    if summaries:
        st.dataframe(
            pd.DataFrame(
                [
                    {k: v for k, v in s.items() if k != "fork"}
                    | {"last activity": _ago(s["last activity"], now)}
                    for s in summaries
                ]
            ),
            width="stretch",
            hide_index=True,
        )
    else:
        st.info("No annotation database found.")
        return

    choice = st.selectbox("Open a run", list(configs), key="runs_tab_choice")
    cfg = configs[choice]
    summary = next(s for s in summaries if s["run"] == choice)
    with st.container(border=True):
        try:
            render_status(st, run_status(cfg, now=now), now=now, log=lane_log_tail(cfg))
        except Exception as exc:  # noqa: BLE001
            st.warning(f"Progress unavailable: {exc}")
        st.caption(
            f"Config: {cfg.source_path.name if cfg.source_path else '-'} · "
            f"database: {cfg.db_path.name} · prompt {cfg.prompt_version} · "
            f"annotators: "
            + ", ".join(f"{a.key} = {a.model} ({a.endpoint})" for a in cfg.annotators)
        )
        if summary["fork"]:
            fork = summary["fork"]
            with st.expander("How this run was made (fork)"):
                st.markdown(
                    f"Forked from `{Path(str(fork.get('source', '-'))).name}` on "
                    f"{fork.get('created_at', '-')}. Votes kept: "
                    f"{', '.join(fork.get('kept_annotators') or [])}. "
                    f"Target set: {fork.get('base')} source-labeled items + extension "
                    f"{fork.get('extension')} = {fork.get('target_total')} (seed "
                    f"{fork.get('seed')})."
                )

        if summary["labeled"]:
            st.markdown("**Labels**")
            c1, c2, c3 = st.columns([1, 1, 2])
            sarcastic_only = c1.checkbox("Sarcastic only", key=f"runs_sarc_{choice}")
            month = c2.text_input("Month (YYYY-MM)", value="", key=f"runs_month_{choice}")
            page = int(c3.number_input("Page", min_value=1, value=1, key=f"runs_page_{choice}"))
            rows, total = label_page(
                cfg, page=page, sarcastic_only=sarcastic_only, month=month.strip()
            )
            st.caption(f"{total:,} labels match · {_PAGE} per page")
            st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)
        else:
            st.info(
                "No final labels yet: they are computed (aggregate) when the run finishes. "
                "Latest votes so far:"
            )
            st.dataframe(pd.DataFrame(latest_votes(cfg)), width="stretch", hide_index=True)

    st.subheader("Exported datasets")
    st.caption(
        "Every exported version keeps its own files and card in data/annotated/. Which "
        "version each repo uses is in UYAM_LATEST.md §1."
    )
    datasets = exported_datasets(annotated_dir or _REPO_ROOT / "data" / "annotated")
    if datasets:
        st.dataframe(
            pd.DataFrame(
                [
                    d | {"exported": d["exported"].astimezone().strftime("%Y-%m-%d %H:%M")}
                    if d["exported"]
                    else d
                    for d in datasets
                ]
            ),
            width="stretch",
            hide_index=True,
        )
    else:
        st.info("No dataset exported yet.")
