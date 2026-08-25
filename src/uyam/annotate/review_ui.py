"""Streamlit "Annotation Review" tab.

Serves two queues from review_queue:
- gold: the stratified validation subset — labeled BLIND (model votes hidden
  by default) so the human gold standard is independent of the ensemble
- low_confidence: items the adjudicator was unsure about — model votes shown,
  the human label becomes final

Imported lazily by app.py so the collector UI works without the annotate
extras installed.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from uyam.annotate.config import load_annotation_config
from uyam.annotate.db import AnnotationDatabase

_CUES = (
    ("cue_polarity_inversion", "Polarity inversion"),
    ("cue_rhetorical_intent", "Rhetorical intent"),
    ("cue_contextual_incongruity", "Contextual incongruity"),
    ("cue_hyperbole", "Hyperbole"),
)
_SENTIMENTS = ["positive", "neutral", "negative"]
_LANGUAGES = ["english", "tagalog", "taglish"]


def _render_context(st: Any, db: AnnotationDatabase, fullname: str) -> None:
    ctx_row = db.get_context(fullname)
    record = db.get_record(fullname)
    if record is None:
        st.warning("Record missing from corpus index.")
        return
    if ctx_row is not None:
        ctx = json.loads(str(ctx_row["context_json"]))
        submission = ctx.get("submission")
        if submission:
            st.markdown(f"**Submission:** {submission.get('title') or ''}")
            if submission.get("selftext"):
                st.caption(submission["selftext"])
        for parent in ctx.get("parent_chain", []):
            op = " *(OP)*" if parent.get("is_submitter") else ""
            st.markdown(f"> **parent{op}:** {parent.get('text') or ''}")
    st.markdown(f"### Target ({record['record_type']})")
    st.info(str(record["text"]))
    if ctx_row is not None:
        for i, reply in enumerate(json.loads(str(ctx_row["context_json"])).get("replies", []), 1):
            st.markdown(f"> **reply {i}:** {reply.get('text') or ''}")
    permalink = record.get("permalink")
    if permalink:
        url = permalink if str(permalink).startswith("http") else f"https://www.reddit.com{permalink}"
        st.caption(f"[view on reddit]({url})")


def render_review_tab(config_path: Path | None = None) -> None:
    import streamlit as st

    cfg = load_annotation_config(config_path)
    if not cfg.db_path.exists():
        st.info(
            "No annotation database yet. Run `uyam annotate index` / `select` / `run` first, "
            "then `uyam annotate aggregate` and `uyam annotate gold-sample`."
        )
        return

    db = AnnotationDatabase(cfg.db_path)
    try:
        queue = db.review_queue_items(only_pending=True)
        gold_pending = [q for q in queue if q["reason"] == "gold"]
        lc_pending = [q for q in queue if q["reason"] == "low_confidence"]
        done = len(db.review_queue_items(only_pending=False)) - len(queue)

        c1, c2, c3 = st.columns(3)
        c1.metric("Gold pending", len(gold_pending))
        c2.metric("Low-confidence pending", len(lc_pending))
        c3.metric("Reviewed", done)

        reason = st.radio(
            "Queue",
            ["gold", "low_confidence"],
            horizontal=True,
            help="Gold items are labeled blind (validation subset); low-confidence items "
            "are final-call adjudications.",
        )
        pending = gold_pending if reason == "gold" else lc_pending
        if not pending:
            st.success(f"No pending items in the {reason} queue.")
            return

        fullname = str(pending[0]["reddit_fullname"])
        st.caption(f"Item {fullname} — {len(pending)} left in this queue")
        _render_context(st, db, fullname)

        show_votes = reason == "low_confidence" or st.checkbox(
            "Reveal model votes (breaks blindness for this item)", value=False
        )
        if show_votes:
            agg = db.get_aggregate(fullname)
            if agg:
                st.json(
                    {
                        "sarcasm_votes": agg.get("sarcasm_votes"),
                        "resolved_by": agg.get("resolved_by"),
                        "votes": json.loads(str(agg.get("votes_json") or "{}")),
                    }
                )

        with st.form(key=f"review_{fullname}"):
            sarcastic = st.radio("Sarcastic?", ["no", "yes"], horizontal=True)
            language = st.radio("Language", _LANGUAGES, horizontal=True)
            literal = st.radio("Literal sentiment", _SENTIMENTS, horizontal=True)
            intended = st.radio("Intended sentiment", _SENTIMENTS, horizontal=True)
            st.caption("Sarcasm cues present in the target:")
            cue_values: dict[str, bool] = {}
            cue_cols = st.columns(len(_CUES))
            for col, (cue_key, cue_label) in zip(cue_cols, _CUES, strict=True):
                with col:
                    cue_values[cue_key] = st.checkbox(cue_label)
            notes = st.text_input("Notes (optional)")
            submitted = st.form_submit_button("Save label", type="primary")

        if submitted:
            db.upsert_human_review(
                {
                    "reddit_fullname": fullname,
                    "sarcastic": int(sarcastic == "yes"),
                    "language": language,
                    "literal_sentiment": literal,
                    "intended_sentiment": intended,
                    **{k: int(v) for k, v in cue_values.items()},
                    "notes": notes or None,
                    "is_gold": int(reason == "gold"),
                }
            )
            st.success(f"Saved {fullname}")
            st.rerun()

        with st.expander("Agreement statistics"):
            if st.button("Compute agreement report"):
                from uyam.annotate.aggregate import compute_agreement

                st.json(compute_agreement(cfg, cfg.prompt_version))
    finally:
        db.close()
