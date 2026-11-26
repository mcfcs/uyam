# ruff: noqa: E501
"""Annotation EDA page: how the LLMs voted, their confidence, and how it compares to the gold.

Everything is computed live and read-only (uyam.annotation_eda). The written
notes interpret the figures for the consultation and the defense; numbers in
them are read from the same frames, so they stay true as data changes. No
Reddit text appears here (the repository is public).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd  # type: ignore[import-untyped]
import streamlit as st

from uyam.annotation_eda import (
    compare_gold_to_all,
    confidence_histogram,
    confidence_vs_gold,
    estimate_human_rate,
    load_post_meta,
    load_votes,
    model_summary,
    pairwise_kappas,
    version_table,
    vote_count_vs_gold,
    vote_wide,
)
from uyam.progress_notes import card_facts, load_gold

GUIDE, OLD = "guide-v1.1", "sarc-v2"


@st.cache_data(ttl=3600, show_spinner="Computing the annotation EDA…")
def _compute(data_dir: str) -> dict[str, Any]:
    base = Path(data_dir)
    db = base / "db"
    gold, _ = load_gold(db / "annotation.sqlite3")
    meta_path = base / "annotated" / "dataset-v5.jsonl"
    if not meta_path.exists():
        meta_path = base / "annotated" / "dataset-v3.jsonl"
    meta = load_post_meta(meta_path)
    out: dict[str, Any] = {"gold_n": len(gold), "gold_pos": sum(g.sarcastic for g in gold.values())}
    for key, path, pv in (
        ("v5", db / "annotation-v5.sqlite3", GUIDE),
        ("v3", db / "annotation-v3.sqlite3", OLD),
    ):
        votes = load_votes(path, pv)
        wide = vote_wide(votes)
        by_votes = vote_count_vs_gold(wide, gold)
        out[key] = {
            "n_posts": len(wide),
            "summary": model_summary(votes),
            "pairs": pairwise_kappas(wide),
            "by_votes": by_votes,
            "estimate": estimate_human_rate(by_votes),
            "conf_gold": confidence_vs_gold(votes, gold),
            "conf_hist": confidence_histogram(votes),
            "language": compare_gold_to_all(meta, wide, gold, "language"),
            "post_type": compare_gold_to_all(meta, wide, gold, "post_type"),
        }
    cards = card_facts(base / "annotated")
    out["versions"] = version_table(
        {
            "v1/v2": (db / "annotation.sqlite3", OLD),
            "v3": (db / "annotation-v3.sqlite3", OLD),
            "v4 (pilot)": (db / "annotation-v4.sqlite3", GUIDE),
            "v5": (db / "annotation-v5.sqlite3", GUIDE),
        },
        gold,
        meta,
        {
            "v1/v2": cards.get("v2") or cards.get("v1") or {},
            "v3": cards.get("v3") or {},
            "v4 (pilot)": cards.get("v4") or {},
            "v5": cards.get("v5") or {},
        },
    )
    return out


def _pct(x: Any) -> str:
    return "–" if x is None or pd.isna(x) else f"{100 * float(x):.0f}%"


def render_annotation_eda(data_dir: Path) -> None:
    st.header("Annotation EDA")
    st.caption(
        "How the three LLM annotators voted, how confident they said they were, and how that compares with "
        "Gio's gold labels. Computed live and read-only from the annotation databases (cached for an hour)."
    )
    if st.button("Recompute", key="eda_refresh"):
        _compute.clear()
    d = _compute(str(data_dir))
    v5, v3 = d["v5"], d["v3"]
    if not v5["n_posts"]:
        st.info("No v5 votes found yet.")
        return
    est = v5["estimate"]
    by5 = v5["by_votes"].set_index("'sarcastic' votes")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Posts labeled (v5)", f"{v5['n_posts']:,}")
    c2.metric("LLM label: sarcastic", _pct(by5.loc[["2 of 3", "3 of 3"], "posts (all)"].sum() / v5["n_posts"]))
    c3.metric("Gio's rate on the gold", _pct(d["gold_pos"] / max(d["gold_n"], 1)))
    c4.metric("Estimated human rate (whole set)", _pct(est["estimated human rate"]))

    with st.expander("1 · How each model votes (v5 guideline prompt vs v3 old prompt)", expanded=True):
        st.markdown("**v5 (guideline prompt)**")
        st.dataframe(v5["summary"], hide_index=True, width="stretch")
        st.markdown("**v3 (old prompt, same models, same posts)**")
        st.dataframe(v3["summary"], hide_index=True, width="stretch")
        rates = pd.DataFrame(
            {
                "v3 old prompt": v3["summary"].set_index("model")["sarcastic rate"],
                "v5 guideline": v5["summary"].set_index("model")["sarcastic rate"],
            }
        )
        st.bar_chart(rates, height=260)
        st.caption(f"For reference: Gio's sarcasm rate on the gold is {_pct(d['gold_pos'] / max(d['gold_n'], 1))}.")
        st.markdown(
            """
- The guideline prompt **spread the models apart**: under the old prompt all three said "sarcastic" about 20–24% of
  the time; under the guideline Llama-SEA-LION became strict (close to Gio's own rate), qwen3 stayed in the middle,
  and SEA-LION became very liberal.
- That spread is why the annotators agree less with each other under the guideline (section 2), while their
  majority agrees more with Gio.
"""
        )

    with st.expander("2 · Do the annotators agree with each other? (inter-annotator agreement)"):
        col_a, col_b = st.columns(2)
        col_a.markdown("**v5 (guideline)**")
        col_a.dataframe(v5["pairs"], hide_index=True, width="stretch")
        col_b.markdown("**v3 (old prompt)**")
        col_b.dataframe(v3["pairs"], hide_index=True, width="stretch")
        versions = d["versions"]
        if not versions.empty:
            st.dataframe(
                versions[["version", "Fleiss κ (LLMs)", "gold κ"]],
                hide_index=True,
                width="stretch",
            )
        st.markdown(
            """
- **Cohen's κ** compares two annotators; **Fleiss' κ** all three at once. Both correct for chance agreement
  (most posts are not sarcastic, so raw agreement is high even for random guesses).
- **Agreement between LLMs is not correctness.** The models may share training data, so they can agree on the same
  mistake. That's why the gold set (a human) is the yardstick: under the guideline the LLMs agree *less* with each
  other but their majority agrees *more* with Gio.
"""
        )

    with st.expander("3 · How the three votes line up with Gio (the most useful signal)", expanded=True):
        st.dataframe(v5["by_votes"], hide_index=True, width="stretch")
        chart = v5["by_votes"].set_index("'sarcastic' votes")[["P(sarcastic per Gio)"]]
        st.bar_chart(chart, height=240)
        st.markdown("**Same table, v3 (old prompt):**")
        st.dataframe(v3["by_votes"], hide_index=True, width="stretch")
        st.markdown(
            """
- **The number of "sarcastic" votes is strongly informative.** The more models say sarcastic, the more likely Gio
  agrees, and under the guideline a 3-of-3 vote is much more trustworthy than under the old prompt.
- **2-of-3 posts are mostly not sarcastic to Gio.** That's where the majority label's false positives come from.
  leische's unanimous-label variant (3 of 3 only) tests exactly this trade-off.
- `labels.sarcasm_vote_share` in the export carries this signal, so the model can be trained or analysed with it.
"""
        )

    with st.expander("4 · What the gold implies for the whole set (estimate)"):
        e = est
        st.markdown(
            f"""
| | value |
|---|---|
| posts | {e['posts']:,} |
| posts the majority label calls sarcastic | {e['majority-label positives']:,} |
| **estimated posts Gio would call sarcastic** | **{e['estimated human-sarcastic posts']:,} ({_pct(e['estimated human rate'])})** |
| estimated precision of the majority label | {_pct(e['estimated precision of the majority label'])} |
| estimated recall of the majority label | {_pct(e['estimated recall of the majority label'])} |

**How:** the gold was not a uniform sample (sarcastic items were weighted ×2), so its raw rate isn't the set's rate.
Gio's sarcasm rate within each vote count (section 3) is multiplied by how common that vote count is in the whole set.
It's an **estimate** with wide uncertainty (few gold positives per vote count), useful to say: *the LLM labels call
roughly 2–3× more posts sarcastic than a careful human would; most true sarcasm is still caught.*
"""
        )

    with st.expander("5 · Is the models' self-reported confidence useful?"):
        st.dataframe(v5["conf_gold"], hide_index=True, width="stretch")
        st.markdown("**How the models spread their confidence (share of each model's votes):**")
        hist = v5["conf_hist"]
        if not hist.empty:
            st.bar_chart(hist, height=260)
        st.markdown(
            """
- **Barely.** Every model reports 0.8–0.95 confidence almost always, and its confidence is about the same when it
  matches Gio as when it doesn't. A "very confident" sarcastic vote is not much more likely to be right.
- So the dataset relies on **votes** (agreement between models), not on confidence. The export keeps confidence but
  the dataset card warns it is weakly calibrated.
"""
        )

    with st.expander("6 · Big set vs gold: language (code-switching) and media"):
        st.markdown("**By language** (rule-based `language_rule`, κ 0.84 with Gio)")
        st.dataframe(v5["language"], hide_index=True, width="stretch")
        st.markdown("**By post type** (what the comment replies to)")
        st.dataframe(v5["post_type"], hide_index=True, width="stretch")
        st.markdown(
            """
- **Code-switching:** Taglish posts dominate both the set and the gold. Compare each language's LLM rate with Gio's
  rate to see where over-calling is worst (cells with few gold posts are noisy).
- **Media:** sarcasm (by the LLMs and by Gio) concentrates in replies to image and video posts, which neither the
  annotators nor the model can see. The text-only slice has few gold positives, so its results are fragile.
- The gold's mix differs a little from the whole set because of how it was drawn (stratified, sarcastic ×2).
"""
        )

    with st.expander("7 · Comparison across annotation versions (labels, code-switching, context)", expanded=True):
        st.dataframe(d["versions"], hide_index=True, width="stretch")
        st.caption(
            "Gold κ/F1/precision/recall: each version's training label vs Gio (297 usable gold posts). Language is the "
            "rule-based label for every version. v1/v2 share labels; v4 is the 5,000-post pilot (its gold numbers equal "
            "v5's because the gold posts were in the pilot). Author history: share of rows with ≥ 1 same-author post "
            "within 48 h."
        )

    with st.expander("8 · Notes for the defense"):
        b3 = v5["by_votes"].set_index("'sarcastic' votes")
        p3 = b3.loc["3 of 3", "P(sarcastic per Gio)"]
        p2 = b3.loc["2 of 3", "P(sarcastic per Gio)"]
        p01 = (
            (b3.loc["0 of 3", "Gio says sarcastic"] + b3.loc["1 of 3", "Gio says sarcastic"])
            / max(b3.loc["0 of 3", "gold posts"] + b3.loc["1 of 3", "gold posts"], 1)
        )
        st.markdown(
            f"""
**Claims the data supports:**
1. *"The annotation was checked against a human."* 300 gold posts, labeled under a written guideline; every label set
   is scored against them (κ, F1, precision, recall with confidence intervals).
2. *"The guideline prompt improved the labels."* Same models, same posts: guideline labels agree with the human
   better than the old prompt's (paired Δκ about +0.11, interval above zero).
3. *"Agreement between models is not enough."* Under the guideline the models agree less with each other but more
   with the human (sections 2–3).
4. *"Vote count is a reliability signal; confidence is not."* When all three say sarcastic, Gio agrees
   {_pct(p3)} of the time; with 2 of 3, {_pct(p2)}; with 0–1, {_pct(p01)}. Self-reported confidence barely separates
   right from wrong (section 5).
5. *"The LLMs over-call sarcasm."* The majority label marks about {_pct(est['majority-label positives'] / max(est['posts'], 1))}
   of posts sarcastic; the estimated human rate is about {_pct(est['estimated human rate'])}. Reported as a limitation,
   tested with the unanimous-label variant.

**Limitations to state before they're asked:**
- One human annotator (no inter-annotator κ for humans); many first passes were made with Claude's suggestion
  visible (reported separately as "blind").
- Media: most human-judged sarcasm sits in replies to images/video that no model sees.
- Language: annotators' own language votes are unreliable; a rule-based label is used instead (κ 0.84).
- The whole-set human rate is an estimate from a stratified sample.

**If asked "why not just use the strictest model?"** Llama-SEA-LION alone matches Gio's rate and has the best single
κ, but one model is one opinion: the ensemble is more stable, and its vote share keeps the graded signal.
"""
        )
