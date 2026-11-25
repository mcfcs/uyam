# ruff: noqa: E501
"""Progress notes tab: where the thesis data and model stand (consultation / defense notes).

The prose is written for Gio and his adviser/panel. Figures that change (gold
progress, agreement with the gold, the v5 run, dataset cards) are computed
live and read-only by uyam.progress_notes; fixed figures cite their source.
No Reddit text appears here (the repository is public).
"""

from __future__ import annotations

from collections.abc import Callable
from functools import partial
from pathlib import Path
from typing import Any

import pandas as pd  # type: ignore[import-untyped]
import streamlit as st

from uyam.progress_notes import (
    GUIDE,
    card_facts,
    default_sources,
    gold_agreement,
    load_gold,
    run_progress,
    vote_rule_table,
)

V5_KEYS = ["qwen3", "sealion", "llamasealion"]
V5_TARGET = 21000
V5_STAGE1 = 16362

# ---------------------------------------------------------------------------
# Diagrams (Graphviz DOT, rendered in the browser by st.graphviz_chart)
# ---------------------------------------------------------------------------

_STYLE = (
    'graph [rankdir=LR, fontname="Helvetica", fontsize=11, nodesep=0.3, ranksep=0.45, bgcolor="transparent"];\n'
    'node [shape=box, style="rounded,filled", fillcolor="#eef3fb", color="#5b7db1", fontname="Helvetica", fontsize=10];\n'
    'edge [color="#5b7db1", fontname="Helvetica", fontsize=9];\n'
)

PIPELINE_DOT = (
    "digraph pipeline {\n" + _STYLE + """
  subgraph cluster_uyam { label="uyam (data + annotation, laptop, 8 GB GPU)"; color="#9db4d8";
    src [label="Reddit posts\\nArctic Shift archive (history)\\n+ Shreddit scraper (Aug-Sep core, Nov 8-10)"];
    raw [label="Raw corpus\\n588,507 records\\nJun 1 - Nov 10, 2026"];
    filt [label="Filters + pseudonymised authors\\n512,471 eligible"];
    sel [label="Target selection\\n21,000 posts"];
    pre [label="Language ID (rule)\\n+ literal sentiment"];
    llm [label="3 equal-size LLM annotators\\n(qwen3 8B, SEA-LION 9B,\\nLlama-SEA-LION 8B)"];
    agg [label="Majority vote\\n= training label"];
    exp [label="Export bundle\\ndataset + card + corpus dump"];
    src -> raw -> filt -> sel -> pre -> llm -> agg -> exp;
  }
  gold [label="Gold set: 300 posts\\nlabeled by Gio\\n(guideline v1.1)", fillcolor="#fff4d6", color="#c79a2b"];
  subgraph cluster_leische { label="leische (model, gaming PC, 24 GB GPU)"; color="#9db4d8";
    train [label="XLM-R + context channels\\n5-fold CV, 3 seeds"];
    res [label="Results\\nF1 vs LLM labels\\n+ F1 / kappa vs gold"];
    train -> res;
  }
  tsis [label="tsis: manuscript\\n(UYAM_LATEST + LEISCHE_LATEST)", fillcolor="#e9f6ec", color="#4c9a5f"];
  exp -> train [label="Taildrop\\n(never GitHub)"];
  gold -> agg [label="evaluation only", style=dashed];
  gold -> res [label="answer key", style=dashed];
  exp -> tsis [style=dotted]; res -> tsis [style=dotted];
}"""
)

LINEAGE_DOT = (
    "digraph lineage {\n" + _STYLE + """
  v1 [label="v1 (Sep 15)\\n15,000 posts, Aug-Sep\\n3 mixed-size LLMs + 32B judge\\nunanimous rule, 10.7% sarcastic\\n= manuscript"];
  v2 [label="v2 (Nov 22)\\nsame 15,000 + same labels\\ncorpus 5 weeks -> 5 months\\n(more author history)"];
  v3 [label="v3 (Nov 23)\\n21,000 posts (+6,000 Jun/Jul/Oct/Nov)\\n3 equal-size 8-9B LLMs\\nmajority vote, 19.1% sarcastic"];
  v4 [label="v4 pilot (Nov 24)\\n5,000 posts (300 gold + 4,700)\\nguideline prompt\\n22.1% sarcastic", fillcolor="#fff4d6", color="#c79a2b"];
  v5 [label="v5 (running, due Thu Nov 26)\\n21,000 posts, guideline prompt\\nplanned final dataset", fillcolor="#e9f6ec", color="#4c9a5f"];
  v1 -> v2 [label="+ context"];
  v2 -> v3 [label="+ equal-size\\nmajority vote\\n+ new months"];
  v3 -> v4 [label="+ guideline\\nprompt (test)"];
  v4 -> v5 [label="scale up"];
  v3 -> v5 [label="same 21,000 posts", style=dashed];
}"""
)

ANNOTATION_DOT = (
    "digraph annotation {\n" + _STYLE.replace("rankdir=LR", "rankdir=TB") + """
  subgraph cluster_old { label="Before (v1/v2)"; color="#c9a0a0";
    o1 [label="gemma3 27B"]; o2 [label="qwen3 8B"]; o3 [label="SEA-LION 9B"];
    ou [label="All 3 say sarcastic?", shape=diamond, fillcolor="#fbeeee", color="#b06060"];
    oj [label="qwen3 32B adjudicator\\ndecides splits (33% of rows)"];
    ol [label="label: 10.7% sarcastic"];
    o1 -> ou; o2 -> ou; o3 -> ou;
    ou -> ol [label="yes / all no"]; ou -> oj [label="split"]; oj -> ol;
  }
  subgraph cluster_new { label="Now (v3, v4, v5)"; color="#9fc7a8";
    n1 [label="qwen3 8B"]; n2 [label="SEA-LION 9B"]; n3 [label="Llama-SEA-LION 8B"];
    nm [label="Majority vote (2 of 3)\\nties on 3-class labels:\\nmost confident vote", shape=diamond, fillcolor="#e9f6ec", color="#4c9a5f"];
    nl [label="label: 19-22% sarcastic\\n(no adjudicator)"];
    n1 -> nm; n2 -> nm; n3 -> nm; nm -> nl;
  }
}"""
)

FUNNEL_DOT = (
    "digraph funnel {\n" + _STYLE + """
  c [label="Collected\\n588,507 records\\n(40,125 posts, 548,382 comments)"];
  e [label="Eligible targets\\n512,471\\n(7 exclusion reasons)"];
  t [label="Labeled targets\\n21,000 (about 4%)"];
  h [label="Temporal context pool\\n352,176 records by labeled authors\\n(corpus dump)"];
  cv [label="Conversational context\\npost + up to 6 parents + 3 replies\\n(6,000 chars, frozen snapshot)"];
  r [label="Retrieval context\\n3 sarcastic + 3 non-sarcastic\\nneighbours from the training fold"];
  m [label="Model input per target"];
  c -> e -> t -> m;
  c -> h [label="same authors"]; h -> m [label="<= 5 posts, 48 h before\\n(50% of targets have >= 1)"];
  cv -> m; r -> m;
}"""
)

MODEL_DOT = (
    "digraph model {\n" + _STYLE + """
  tgt [label="Target post"];
  conv [label="Conversation\\n(post, parents, replies)"];
  tmp [label="Same-author posts\\n(<= 48 h before)"];
  ret [label="Similar labeled examples\\n(MiniLM, training fold only)"];
  enc [label="XLM-R encoder\\n(shared)"];
  a1 [label="attention\\nover items"]; a2 [label="attention\\n+ time decay"]; a3 [label="nearest 3 + 3"];
  gate [label="Gate: one weight per channel\\n(sums to 1, per post)", shape=diamond, fillcolor="#fff4d6", color="#c79a2b"];
  clf [label="Classifier\\nsarcastic?"];
  rq3 [label="RQ3: literal vs intended\\nsentiment heads"];
  tgt -> enc; conv -> enc -> a1; tmp -> a2; ret -> a3;
  enc -> a2 [style=dotted];
  a1 -> gate; a2 -> gate; a3 -> gate; enc -> clf [label="target vector"];
  gate -> clf [label="fused context"]; clf -> rq3 [style=dashed];
}"""
)

ROUTING_DOT = (
    "digraph routing {\n" + _STYLE + """
  t [label="Target"];
  c [label="conv channel\\n(item encoded WITH the target)"];
  m [label="temporal channel\\n(target-aware)"];
  r [label="retrieval as prototypes\\n(sarcastic vs not)"];
  n [label="NULL channel\\n= 'no context helps'", fillcolor="#fff4d6", color="#c79a2b"];
  s [label="self-report: how many items,\\nhow recent, how similar"];
  g [label="Gate sees target, channel,\\nmatch features + self-report\\n(channel dropout in training)", shape=diamond, fillcolor="#e9f6ec", color="#4c9a5f"];
  o [label="Classifier on\\n[t, c, t*c, |t-c|]\\n+ one head per annotator"];
  t -> g; c -> g; m -> g; r -> g; n -> g; s -> g; g -> o;
}"""
)

STEPS_DOT = (
    "digraph steps {\n" + _STYLE + """
  A [label="A: initial\\nv1 data + v1 labels\\nF1 0.16 (context model)"];
  B [label="B: + more history\\n(v2)\\nF1 0.25"];
  C [label="C: + equal-size LLMs,\\nmajority vote (v3)\\nF1 0.29"];
  D0 [label="D0: v3 labels\\non v4's posts\\nF1 0.32"];
  D [label="D: current\\nguideline labels (v4)\\nF1 0.35", fillcolor="#e9f6ec", color="#4c9a5f"];
  A -> B [label="+0.09 (noise-level;\\nkappa +0.12 clears 0)"];
  B -> C [label="+0.05\\n(text-only: +0.19, clears 0)"];
  D0 -> D [label="+0.03\\n(blind items: +0.22, clears 0)"];
  A -> D [label="+0.195 [+0.04, +0.35]", color="#4c9a5f", fontcolor="#2d6b3c", penwidth=2];
}"""
)

TIMELINE_DOT = (
    "digraph timeline {\n" + _STYLE + """
  t1 [label="Tue Nov 24\\nv5 started; gold labeling\\n(finish tonight)"];
  t2 [label="Wed Nov 25\\nadviser consultation"];
  t3 [label="Thu Nov 26\\nv5 done (stage 1 ~05:00,\\nstage 2 ~19:00)"];
  t4 [label="Export v5\\n(+ gold sync, gold_queue flag)\\nsend to phillmyeol"];
  t5 [label="leische trains on v5\\n~20 GPU-h (around games)"];
  t6 [label="Results vs full gold\\n(300 items)"];
  t7 [label="Manuscript revisions\\n(tsis)"];
  t8 [label="Final defense", fillcolor="#e9f6ec", color="#4c9a5f"];
  t1 -> t2 -> t3 -> t4 -> t5 -> t6 -> t7 -> t8;
}"""
)

GOLD_USE_DOT = (
    "digraph golduse {\n" + _STYLE + """
  g [label="Gold set: 300 posts\\nlabeled by Gio", fillcolor="#fff4d6", color="#c79a2b"];
  dev [label="DEV split (~100)\\nallowed to look at"];
  test [label="TEST split (~200)\\nfrozen, never tuned on"];
  ea [label="Error analysis\\nwhere do LLMs and Gio disagree?"];
  rule [label="Vote rule\\n(majority / unanimous /\\ndrop an annotator)\\nminutes, no model run"];
  gl [label="Guideline v1.2\\n+ prompt guide-v1.2\\n(re-annotation ~2 days)"];
  lab [label="Chosen training labels\\n(v5 or a v5 variant)"];
  lei [label="leische trains"];
  rep [label="Reported scores:\\nlabels and models vs TEST\\n(+ all 300 for context)", fillcolor="#e9f6ec", color="#4c9a5f"];
  g -> dev; g -> test;
  dev -> ea -> rule -> lab; ea -> gl -> lab [style=dashed];
  lab -> lei -> rep; test -> rep;
}"""
)

ALL_DIAGRAMS = {
    "gold_use": GOLD_USE_DOT,
    "pipeline": PIPELINE_DOT,
    "lineage": LINEAGE_DOT,
    "annotation": ANNOTATION_DOT,
    "funnel": FUNNEL_DOT,
    "model": MODEL_DOT,
    "routing": ROUTING_DOT,
    "steps": STEPS_DOT,
    "timeline": TIMELINE_DOT,
}


# ---------------------------------------------------------------------------
# Live data (cached, read-only)
# ---------------------------------------------------------------------------


@st.cache_data(ttl=300, show_spinner=False)
def _live(data_dir: str) -> dict[str, Any]:
    base = Path(data_dir)
    db_dir = base / "db"
    gold, gold_stats = load_gold(db_dir / "annotation.sqlite3")
    return {
        "gold": {
            "queue": gold_stats["queue"],
            "labeled": gold_stats["labeled"],
            "votes_seen": gold_stats["votes_seen"],
            "scored": len(gold),
            "positives": sum(g.sarcastic for g in gold.values()),
            "blind": sum(1 for g in gold.values() if g.blind),
            "blind_positives": sum(g.sarcastic for g in gold.values() if g.blind),
        },
        "agreement": gold_agreement(gold, default_sources(db_dir)),
        "v5": run_progress(db_dir / "annotation-v5.sqlite3", GUIDE, V5_KEYS, V5_TARGET),
        "vote_rules": vote_rule_table(
            gold,
            db_dir
            / (
                "annotation-v5.sqlite3"
                if (db_dir / "annotation-v5.sqlite3").exists()
                else "annotation-v4.sqlite3"
            ),
            GUIDE,
        ),
        "cards": card_facts(base / "annotated"),
    }


def _pct(x: Any) -> str:
    return "–" if x is None else f"{100 * float(x):.1f}%"


# ---------------------------------------------------------------------------
# Sections
# ---------------------------------------------------------------------------


def _glance(live: dict[str, Any]) -> None:
    g = live["gold"]
    v5 = live["v5"]
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Gold labeled", f"{g['labeled']} / {g['queue'] or 300}")
    c2.metric("Gold sarcastic (scored)", f"{g['positives']} of {g['scored']}")
    c3.metric("v5 annotation", f"{100 * v5['share']:.0f}%" if v5 else "not started")
    c4.metric("Dataset versions", "5 (v1-v5)")
    st.markdown(
        """
**The story in four sentences.** The thesis asks whether context (the thread, the author's recent posts,
similar examples) helps a model detect sarcasm in Filipino-English Reddit posts. The first round (v1, in the
manuscript) found context did **not** help, but every score was agreement with LLM labels that agree with a
human reader only weakly (κ about 0.2–0.3 on the gold set; live table in section 3). Since October the data side has been rebuilt around the adviser's
comments: a 5-month corpus, equal-size annotator models with majority vote, a written sarcasm guideline,
media flags, and a human gold set of 300 posts. An exploratory comparison already shows the current setup
agrees with the human gold clearly better than the initial one (F1 0.16 → 0.35, exploratory); the final
guideline dataset (v5) is being labeled now.
"""
    )
    st.graphviz_chart(PIPELINE_DOT)


def _adviser(live: dict[str, Any]) -> None:
    g = live["gold"]
    rows = [
        ("1", "Finish the gold set (31 → 300) under a written guideline", "Gio (uyam)",
         f"Guideline v1.1 written (docs/GUIDELINE.md: definition, Says/Means test, cue rules, 14 hard cases). "
         f"{g['labeled']} / {g['queue'] or 300} labeled; finishing tonight."),
        ("2", "A second human annotator", "Gio",
         "Deferred (doesn't fit the time). Stated as a limitation: one human annotator, no inter-annotator κ. "
         "Claude's suggestion was shown on many first passes; those count as Gio's labels, with a blind-only κ as a check."),
        ("3", "Re-run the 3 main conditions on majority-vote labels", "leische",
         "Data ready: v3/v5 labels ARE majority vote. Exploratory run done (run C below); the full runs wait for v5."),
        ("4", "Zero-shot LLM and simple text-concatenation baselines", "leische",
         "Not started. Score them against the human gold, with a model outside the annotator lineup (needs Gio's OK to run)."),
        ("5", "Re-annotate with equal-size LLMs (no hidden model-size effect)", "uyam",
         "Done: v3 (8B / 9B / 8B, majority vote, 21,000 posts). Then the guideline prompt: v4 pilot, v5 running."),
        ("6", "RQ3 across all folds with proper controls", "leische", "Not started (fold 0 only so far)."),
        ("7", "Re-run on posts that actually have context (don't force retrieval)", "leische",
         "Not started. Data supports it; the routing design (null channel) is the model-side answer."),
        ("8", "Reddit API access", "Gio", "Application pending. Collection is frozen until it is approved."),
        ("9", "Ethics clearance", "Gio",
         "Pending. Must cover the scraper (v1 core), the Arctic Shift archive, pseudonymisation, Claude-assisted gold, retention."),
        ("10", "Release the dataset only after validation and revisions", "all",
         "In place: exports go to leische only, by direct transfer, never GitHub."),
        ("11", "Manuscript: 'Taglish' → 'Filipino'; define code-switching", "tsis",
         "To do in the manuscript. The `taglish` label covers intra- and inter-sentential switching per post."),
    ]
    st.dataframe(
        pd.DataFrame(rows, columns=["#", "Adviser comment (October)", "Owner", "Status (Nov 24)"]),
        hide_index=True,
        width="stretch",
    )


def _collection(live: dict[str, Any]) -> None:
    excl = (live["cards"].get("v3") or {}).get("exclusions") or {}
    st.markdown(
        """
**Where the data comes from.**
- **Arctic Shift** (arctic-shift.photon-reddit.com) is a public, third-party archive of Reddit. uyam queried its API
  by subreddit and day for **June 1 – November 10, 2026**, for r/Philippines, r/CasualPH and r/OffMyChestPH: every
  post plus its comments (a comment stream with a ≥ 3-day reply horizon). It throttles heavily (often ~1 request
  per minute); the backfill took several days in November.
- **Shreddit** (a headed-browser scraper of Reddit's own pages) collected the original Aug–Sep core that all
  15,000 v1 labels come from, and the latest days (Nov 8–10). Reddit's JSON listing returns 403 now, so it scrolls
  the page itself.
- **Not the official Reddit API.** The API application is pending. Collection is **frozen** until it is approved
  and ethics clearance is done. The archive is not a way around that: the ethics text must state both sources.
  If the API is approved, re-fetching the labeled posts through it is a provenance and deletion check.
- **Privacy:** usernames are replaced by an HMAC hash on ingest (never stored or exported); the key never changes,
  so author history stays linkable. Scores from the archive are archive-time scores.

**Corpus:** 588,507 records (40,125 posts, 548,382 comments). Collection grew about 29× from v1's 20,573 records
(5 weeks). Every record is `natural` (no keyword oversampling in the data).
"""
    )
    if excl:
        st.caption("Why posts are excluded as targets (they stay usable as context):")
        st.dataframe(
            pd.DataFrame(
                [(k.replace("_", " "), v) for k, v in excl.items()],
                columns=["reason", "records"],
            ),
            hide_index=True,
        )


def _models(live: dict[str, Any]) -> None:
    st.graphviz_chart(ANNOTATION_DOT)
    st.markdown(
        """
| | Before (v1/v2) | Now (v3 / v4 / v5) |
|---|---|---|
| Annotators | gemma3 **27B**, qwen3 8B, SEA-LION v3 9B | qwen3 **8B**, SEA-LION v3 **9B**, Llama-SEA-LION v3 **8B** |
| Families | Gemma 3, Qwen3, Gemma 2 | Qwen3, Gemma 2, Llama 3.1 (two Filipino-tuned) |
| Size | mixed (27B dominated) | equal (8–9B, 4-bit), the adviser's item 5 |
| Rule | unanimous for "sarcastic"; else a qwen3 **32B** adjudicator (33% of rows) | majority (2 of 3); 3-class ties by confidence; no adjudicator |
| Hardware | gaming PC (24 GB) for the 27B / 32B | this laptop's 8 GB GPU (RTX 4070), all local |
| Speed | — | ~2.3–2.5 s/item (qwen3, Llama), ~5.8 s (SEA-LION, partly on CPU); v3 took 30.4 h, v4 13.8 h |
| Prompt | sarc-v2 (written before the guideline) | v3: sarc-v2; v4/v5: **guide-v1.1** (the condensed guideline) |

**Why the change matters:** under the old rule a single dissent sent the item to the 32B judge, which kept
labels conservative (10.7% sarcastic). With majority vote, 2-1 splits count, so v3 has 19.1% sarcastic.
Gio's own rate on the gold is about 10%, so the LLMs still over-call relative to him (SEA-LION most).
"""
    )
    st.markdown("**How each annotator and label set agrees with Gio's gold** (live; F1 and Cohen's κ on sarcasm)")
    agree = pd.DataFrame(live["agreement"])
    if agree.empty:
        st.info("No gold labels or label sets found.")
    else:
        st.dataframe(agree, hide_index=True, width="stretch")
        st.caption(
            "'all' = every gold item Gio labeled (Claude's suggestion was showing on many; Gio counts them as his labels). "
            "'blind' = first passes with no suggestion showing (a sensitivity check). Items whose model votes were shown "
            "first are left out. The v5 row equals v4 until v5 is aggregated (the gold posts are in the pilot)."
        )
    st.markdown(
        """
**Reading it (the table is live; the ranking has shifted as gold labels came in):**
- **The guideline labels (v4/v5) agree with Gio best**, but the lead has narrowed as the gold set grew. At 111 items
  it looked like a doubling (κ 0.24 → 0.48); at 237 items it is about 0.33 vs 0.27 (v1) and 0.21 (v3). On the 32
  blind items it is still 0.64 vs 0.18.
- **Every LLM label set over-calls sarcasm** (2–3× Gio's rate). That is why a stricter vote rule on the same votes
  matches Gio better (section 10), with no new model run.
- Llama-SEA-LION with the guideline is the best single model; SEA-LION with the guideline over-calls most. The three
  models agree **less** with each other under the guideline (Fleiss κ 0.40 → 0.26).
- Part of the human–LLM gap is the definition of sarcasm (the guideline helps), and part is over-calling (the vote
  rule helps).
"""
    )


def _datasets(live: dict[str, Any]) -> None:
    st.graphviz_chart(LINEAGE_DOT)
    cards = live["cards"]

    def card(v: str, key: str) -> Any:
        return (cards.get(v) or {}).get(key)

    v5 = live["v5"]
    v5_state = (
        f"running: {100 * v5['share']:.0f}% of votes" if v5 else "not started"
    )
    rows = [
        ("v1", "Sep 15", "15,000", "1,601 (10.7%)", "3 mixed-size + 32B judge, unanimous", "sarc-v2", "37%", "manuscript; leische results"),
        ("v2", "Nov 22", "15,000 (same)", "1,601 (same)", "same labels as v1", "sarc-v2", _pct(card("v2", "history 48 h")), "more author history (5-month corpus); phatic flag; majority field"),
        ("v3", "Nov 23", "21,000", f"{card('v3', 'sarcastic') or 4011:,} (19.1%)", "3 equal-size, majority", "sarc-v2", _pct(card("v3", "history 48 h")), "adviser item 5; +6,000 posts from Jun/Jul/Oct/Nov; media flags; rule-based language"),
        ("v4", "Nov 24", "5,000 (pilot)", f"{card('v4', 'sarcastic') or 1104:,} (22.1%)", "3 equal-size, majority", "guide-v1.1", _pct(card("v4", "history 48 h")), "guideline prompt test; 300 gold + 4,700 stratified"),
        ("v5", "Thu Nov 26 (due)", "21,000", "–", "3 equal-size, majority", "guide-v1.1", "~50%", f"planned final dataset; {v5_state}"),
    ]
    st.dataframe(
        pd.DataFrame(
            rows,
            columns=["version", "exported", "labeled posts", "sarcastic", "annotation", "prompt", "author history ≤ 48 h", "what it is / changed"],
        ),
        hide_index=True,
        width="stretch",
    )
    st.markdown(
        """
**Per iteration:**
1. **v1 → v2: more context, same labels.** The corpus grew from 5 weeks to 5 months, so 46.5% of labeled posts have
   a same-author post in the 48 h before (was 36.7%). Labels were frozen so leische can isolate the context effect.
2. **v2 → v3: who labels and how.** Equal-size models and majority vote (adviser), plus 6,000 posts from other months.
3. **v3 → v4: what "sarcastic" means.** Same models, but the prompt is the written guideline. Tested on 5,000 posts.
4. **v4 → v5: scale.** The guideline setup on the same 21,000 posts as v3, so v3 and v5 differ only in the prompt.

**Fixed across v3–v5:** the posts' context snapshots (what the annotators saw), the gold set, the filters,
and the language label (the rule-based field, κ 0.84 with Gio, replaces the LLMs' language votes for analysis).
"""
    )


def _usage(live: dict[str, Any]) -> None:
    st.graphviz_chart(FUNNEL_DOT)
    cards = live["cards"]
    rows = []
    for v in ("v2", "v3", "v4"):
        c = cards.get(v) or {}
        if c:
            rows.append(
                (v, c.get("rows"), c.get("corpus dump"), _pct(c.get("history 48 h")), _pct(c.get("history 7 d")), _pct(c.get("history 30 d")))
            )
    if rows:
        st.dataframe(
            pd.DataFrame(rows, columns=["version", "labeled posts", "context records shipped", "≥ 1 author post ≤ 48 h", "≤ 7 days", "≤ 30 days"]),
            hide_index=True,
        )
    st.markdown(
        """
- **Most of the corpus is context, not training targets.** About 4% of eligible posts are labeled; the rest supplies
  threads and author history. The model's temporal rule (≤ 5 same-author posts in the 48 h before) reaches about
  half the targets; a wider window (7 or 30 days) would reach two-thirds to four-fifths. Widening it is a leische /
  method decision.
- **Conversational context** comes from a frozen snapshot per item (the same block the annotators saw): the post,
  up to 6 parent comments and up to 3 replies, capped at 6,000 characters. About half the targets have no thread
  turn beyond the post.
- **Retrieval context** is built inside each training fold only (3 sarcastic + 3 non-sarcastic nearest examples), so
  it never leaks test labels.
"""
    )


def _media(live: dict[str, Any]) -> None:
    v3 = live["cards"].get("v3") or {}
    st.markdown(
        f"""
**The blind spot.** Image-only posts were excluded as targets, but **comments under image and video posts were not**,
and nobody (annotators or model) sees the image. In the 15,000 v1/v2 labels, **43% of rows reply to an image or
video post and they hold 63% of the sarcastic labels**. In v3, replies to image posts are 24% sarcastic and to video
posts 36%, against 13% for text posts. Of Gio's 22 sarcastic gold posts (at 237 labeled), 18 sit under unseen media.

**What was done (Nov 22):** keep everything, flag it, report slices (rebuilding a text-only dataset would remove most
positives). Every exported row (v3 on) carries `media_context`: post type, whether the post/parent/target has media,
and a `text_only` flag. v2 has a side file. v3: **{v3.get('text-only rows') or 9810:,} text-only rows,
{v3.get('text-only sarcastic') or 1227:,} sarcastic**. Gold labeling follows the models: labeled from the text alone
(guideline 6.3b), with the Reddit link hidden until the first save.

**Is it used in leische?** Yes, as a *reporting slice*, not a model input:
- The loader reads `media_context.text_only` (v3 on) into every prediction file, so any condition can be scored
  overall and text-only. The v2 side file has been received but isn't wired into the v1/v2 runs yet.
- **Results so far (exploratory only):** on media-dependent gold posts (47 items, 9 sarcastic), F1 is 0.17–0.26 for
  the initial setups and 0.40–0.47 for the current ones. The text-only gold slice has only 2 sarcastic posts,
  too few to interpret.
- **For the thesis:** a stated limitation (models judge text only) and a reporting rule: every result overall and
  on text-only. An optional ablation (a "context incomplete" tag) is possible but could become a shortcut.
"""
    )


def _results(live: dict[str, Any]) -> None:
    st.graphviz_chart(MODEL_DOT)
    st.markdown(
        """
**The model (manuscript, v1).** XLM-R encodes the target; three context channels (conversation, same-author posts,
retrieved examples) are attention-pooled and combined by a per-post gate whose weights sum to 1; a classifier
decides sarcastic or not; RQ3 adds literal vs intended sentiment. 5-fold cross-validation grouped by thread,
3 seeds for the key conditions.

**Manuscript results (v1, scored against the LLM labels):**
| question | result |
|---|---|
| RQ1: does context help? | **No.** Baseline F1 0.368 ± 0.026 vs full model 0.355 ± 0.024 (ΔF1 −0.006, CI [−0.016, +0.004]). |
| RQ2: which channel? | All 8 conditions within F1 0.350–0.370; none clearly above the baseline at threshold 0.5. |
| RQ3: sentiment re-reading | Narrowly positive: 0.341 → 0.471 accuracy on sarcastic rows whose literal and intended sentiment differ (fold 0, 208 rows). |
| label ceiling | The annotator LLMs themselves score F1 0.49–0.58 against the shipped label. |

Every manuscript F1 is **agreement with the LLM ensemble**, on held-out folds, not with a human (the manuscript says so;
31 gold items were too few).
"""
    )
    st.markdown("**Exploratory: initial vs current setup, scored on Gio's gold (leische, Nov 24)**")
    st.graphviz_chart(STEPS_DOT)
    st.markdown(
        """
| run | data → labels | train rows | text-only F1 | context F1 |
|---|---|---|---|---|
| A initial | v1 → v1 | 9,379 | 0.191 | 0.158 |
| B + more history | v2 → v1 | 9,379 | 0.127 | 0.245 |
| C + equal-size, majority | v3 → v3 (A's posts) | 9,379 | 0.316 | 0.294 |
| D0 control | v3 → v3 (v4's posts) | 3,176 | 0.341 | 0.321 |
| **D current** | **v4 → v4 (guideline)** | **3,176** | 0.311 | **0.353** |

- **Headline:** the current setup's context model agrees with Gio better than the initial one: **+0.195 F1
  [+0.04, +0.35], κ 0.03 → 0.25**, while training on a third as many rows. Exploratory: scored on the first 111 gold
  items (11 human positives), one of 16 step tests. leische should re-score on the full gold set.
- **The initial models missed most of Gio's sarcasm** (recall ≈ 0.15): the unanimous rule made labels too
  conservative. Majority labels lift recall to ≈ 0.67.
- **Labels are the bigger lever than corpus size.** The equal-size/majority step is the clearest single gain
  (text-only +0.19); the guideline step clears zero on blind items; more author history is near the noise floor
  (≈ 0.06–0.10 F1 between identical runs).
- **Context vs text-only is still not consistently better** within a run, in line with the manuscript's RQ1.
- **Ceiling:** at 111 items the best model's κ with Gio (0.25) was about half its own labels' κ (0.48); more training rows (v5: ~4×) should help.
"""
    )


def _routing() -> None:
    st.graphviz_chart(ROUTING_DOT)
    st.markdown(
        """
**What leische plans** (`suggestions/GATED_FUSION_V2.md`, a design draft, not yet run):
- **Target-aware item encoding:** each context item is encoded together with the target, so the mismatch that makes
  sarcasm visible is seen at token level (early fusion). In the exploration, early fusion beat the baseline on 5 of 5
  folds; late fusion (the manuscript's) never did.
- **A null channel ("routing"):** the gate can put its weight on "no context helps this post", so the text-only model
  is recoverable per post. Today's gate can't say "none": it settled on a fixed prior (retrieval 50%, temporal 33%,
  conversation 16%) even though retrieval never helped.
- **Self-report scalars** (how many items, how recent, how similar), **channel dropout** in training, matching
  features in the classifier, retrieval as prototypes, one head per annotator with vote-share labels, and the
  `contextual_incongruity` cue as free supervision for the conversation gate.
- **Condition 9** (early fusion + per-annotator heads + vote-share labels) is the exploration's best recipe:
  F1 0.405 ± 0.010 vs the baseline's 0.368 (single seed, not a thesis condition).

**Is routing context a good idea?** Yes, in principle: it fits the data and the adviser's item 7.
- **For:** about half the targets have no author history and about half no thread turn, so forcing context adds
  noise; a null option makes "context didn't help here" an output you can report (how often the model chooses no
  context), which turns the negative RQ1 into an analysis rather than a dead end.
- **Against / risks:** it is a design draft (no run yet); gains are hard to show when the human answer key has few
  positives; and time before the defense is short.
- **Suggested order:** first train the proven setup on v5 (the 3 main conditions, 3 seeds, ~20 GPU-h). Then, if time
  allows, one routing row (fold 0, one seed) as an added experiment; otherwise present routing as the main future-work
  design with the gate statistics as motivation.
"""
    )


def _gold(live: dict[str, Any]) -> None:
    g = live["gold"]
    st.markdown(
        f"""
**What it is.** 300 posts drawn from the labeled set, stratified by sarcasm × language × split × length, with
sarcastic and split-vote items weighted ×2 so the answer key has enough positives. Labeled by Gio under
`docs/GUIDELINE.md` v1.1 (definition: says ≠ means, a real attitude behind it, and a cue an ordinary reader can see;
hugot / self-pity / playful praise can count (decision D1); hate speech and situational irony don't).

**Now:** {g['labeled']} labeled of {g['queue'] or 300}; {g['scored']} usable for scoring
({g['votes_seen']} left out because model votes were shown first); **{g['positives']} sarcastic**.
Blind first passes: {g['blind']} ({g['blind_positives']} sarcastic).

**How it's used.** Evaluation only (never training): it is the common answer key that lets different label sets
and models be compared fairly, and it turns "agreement with LLMs" into "agreement with a human reader".

**Claude assistance (Gio's decision, Nov 24):** first passes made with Claude's guideline-based suggestion showing
count as Gio's labels (the primary score). Each label records whether the suggestion was showing, so a blind-only
score is reported as a sensitivity check. Expect a panel question on this.
"""
    )


def _after_gold(live: dict[str, Any]) -> None:
    g = live["gold"]
    st.markdown(
        f"""
**Status:** {g['labeled']} / {g['queue'] or 300} gold posts labeled; {g['scored']} usable; **{g['positives']} sarcastic**
(about {100 * g['positives'] / max(g['scored'], 1):.0f}%; the LLM label sets call 2–3× more posts sarcastic).

### Why the gold set matters (beyond "accuracy vs a human")
1. **Measure.** It turns every score from "agreement with LLMs" into "agreement with a human reader" (the
   manuscript itself names this the most valuable next step).
2. **Choose.** It is the only fair way to pick between label sets (v1–v5), annotators, and vote rules, because each
   label set grades models differently.
3. **Improve — yes, it can improve results.** Your answer is right: disagreements between the LLMs and you show
   *where* the prompt and guideline are wrong, which leads to a better guideline/prompt (v1.2) and a better vote
   rule. The cheapest lever is the vote rule (below): it changes the training labels without running any model.
4. **Evaluate the final model,** including the media and language slices.
5. **Defend.** It answers the panel's first question: *"how do you know the labels are right?"*

**The one rule when using it to improve things:** whatever you tune on the gold can't also be the score you report,
or the score is optimistic. So split it: a **dev** part (~100) for error analysis and choices, and a **test** part
(~200) that stays untouched for the reported numbers. It is never training data (300 posts are too few, and using
them would leak the answer key).
"""
    )
    st.graphviz_chart(GOLD_USE_DOT)
    st.markdown("**Example: same votes, different vote rule** (live, guideline-prompt votes vs your gold; no model run needed)")
    rules = pd.DataFrame(live["vote_rules"])
    if rules.empty:
        st.info("No guideline votes found yet.")
    else:
        st.dataframe(rules, hide_index=True, width="stretch")
        st.caption(
            "The LLMs over-call sarcasm, so a stricter rule matches you better. This table uses ALL your gold labels, "
            "so treat it as a hint: the real choice should be made on the dev split and confirmed on the test split. "
            "It also answers the adviser's question about majority vote vs a 3-0 rule, with human data."
        )
    st.markdown(
        """
### Next actions once the gold set is finished
| # | step | who | cost |
|---|---|---|---|
| 1 | Sync the 300 gold labels into the v3/v4/v5 databases; final agreement table with bootstrap 95% CIs | agent | minutes |
| 2 | **Freeze a dev/test split** of the gold (seeded, stratified by sarcasm × language × media) | Gio decides, agent does | minutes |
| 3 | **Error analysis on the dev split**: list LLM-vs-you disagreements by type (hugot/D1, unseen media, irony, rhetorical questions, language) | agent drafts, Gio checks | ~1 h |
| 4 | **Pick the vote rule on dev** (majority / unanimous / without SEA-LION), confirm on test | agent | minutes, no model run |
| 5 | Export v5 with the chosen rule (+ the `gold_queue` flag), send to phillmyeol | agent + Gio | minutes |
| 6 | leische trains on v5 (3 main conditions × 3 seeds), scored on the test split and on all 300 | leische, Gio's go | ~20 GPU-h |
| 7 | Optional, if time: guideline v1.2 from the error analysis → prompt guide-v1.2 → re-annotate (v6) | agent, Gio's go | ~2 days laptop |
| 8 | D5: re-label your first 31 items blind (2+ weeks later) for an intra-annotator κ | Gio | ~30 min |
| 9 | Thesis text: gold method (sampling, guideline, Claude-assisted vs blind, dev/test split) | tsis chat | — |

**What to expect:** with 300 items and ~25–30 sarcastic posts, the agreement intervals roughly halve compared with
the first 111 items. The ranking of label sets has already shifted as labels came in (the guideline labels' lead
narrowed from κ 0.48 vs 0.24 to about 0.33 vs 0.27), which is exactly why the full set and a held-out test split
matter before any claim goes in the manuscript.
"""
    )


def _next(live: dict[str, Any]) -> None:
    st.graphviz_chart(TIMELINE_DOT)
    v5 = live["v5"]
    if v5:
        st.caption(
            "v5 votes so far: "
            + ", ".join(f"{k} {v:,}/{V5_TARGET:,}" for k, v in v5["models"].items())
            + f" (stage 1 = first {V5_STAGE1:,} posts per model)."
        )
    st.markdown(
        """
**Before the defense, in order:**
1. **Finish gold labeling.** The intervals in leische's comparison are wide because it was scored on 11 sarcastic posts; the full set roughly doubles that.
2. **Adviser consultation** (Wed): v3 done; exploratory evidence; v5 as the final dataset (decision on the guideline
   re-annotation).
3. **v5 annotation finishes** (Thu ~19:00; the 15,000 v1/v2 posts are done by ~05:00 if an earlier cut is needed).
4. **Export v5** with the full gold synced and a `gold_queue` flag on all 300 gold posts, so leische can keep them out
   of training. Send to phillmyeol by Taildrop.
5. **leische trains on v5:** the 3 main conditions × 3 seeds (~20 GPU-h; the full 8-condition matrix is ~40 GPU-h),
   scored both the manuscript way (held-out LLM labels, folds) and on the 300-item gold.
6. **Manuscript (tsis):** data and annotation method, the guideline appendix, media limitation, language labeling,
   Claude-assisted gold, Arctic Shift ethics, terminology (Filipino / code-switching), new results.

**What results to expect (an informed guess, not a promise):**
- Against the **human gold**: a v5-trained model should at least match the exploratory D (F1 ≈ 0.35, κ ≈ 0.25), likely
  higher with ~4× the training rows, and clearly above the initial setup (≈ 0.16).
- Against **its own LLM labels**: F1 around 0.45–0.50 at a ~22% positive rate. Not comparable to the manuscript's
  0.368 (different labels and base rate); report it only within v5.
- **RQ1 (does context help?)** may stay small or null; the routing design is how to make that a finding.
"""
    )


def _misc() -> None:
    st.markdown(
        """
**Other details worth having at hand:**
- **Language labels:** the LLMs' language votes broke under the guideline prompt (English posts called Taglish;
  κ 0.08). Language for analysis now comes from a rule (the guideline's L-rules on word-level tags from fastText and
  English/Filipino word-frequency lists): **κ 0.84 (91%) with Gio**. v3 by the rule: English 6,537, Tagalog 1,217,
  Taglish 13,246.
- **Taglish definition:** a post that meaningfully mixes both languages (neither ≥ ~90% of counted words), judged
  per post, so it covers intra- and inter-sentential switching; a single particle ("po", "naman") doesn't count.
- **Phatic filter:** greetings, thanks and birthday wishes with nothing else are excluded as targets (1,417); 120
  already-labeled ones are flagged, not dropped.
- **Dataset files** go only to leische by direct transfer (Taildrop); no data is on GitHub, and nothing is released.
- **Reproducibility:** each dataset has a card (counts, filters, agreement, coverage, provenance with model digests
  and prompt version), SHA-256 checksums, frozen folds in leische, and a public code history.
- **Machines:** annotation on the laptop (8 GB RTX 4070, local Ollama); training on the gaming PC (`phillmyeol`,
  24 GB), scheduled around games.

**Questions a panel is likely to ask:**
1. *Your labels are from LLMs. How do you know they're right?* → The 300-item human gold set; the guideline;
   κ with the gold per label set; label sets agree with each other only moderately (κ 0.38–0.62), which is why the
   human key is used.
2. *Only one human annotator?* → Yes, a stated limitation; Claude-assisted vs blind scores reported separately.
3. *Data not from the official API?* → Archive + scraper, pseudonymised, collection frozen until API and ethics.
4. *Context didn't help: is the thesis negative?* → RQ1 is null on v1; labels mattered more; routing (null channel)
   tests when context helps; media blindness is quantified.
5. *Why equal-size models?* → To remove model size as a hidden variable (adviser); all three fit one 8 GB GPU.
"""
    )


def render_progress_notes(data_dir: Path) -> None:
    st.header("Progress notes")
    st.caption(
        "Where the data and model stand, for the adviser consultation and the defense. Live figures update every "
        "5 minutes (read-only); fixed figures come from UYAM_LATEST.md, LEISCHE_LATEST.md and the manuscript."
    )
    if st.button("Refresh live figures", key="progress_refresh"):
        _live.clear()
    live = _live(str(data_dir))

    _glance(live)
    sections: list[tuple[str, Callable[[], None]]] = [
        ("1 · Adviser comments and how each is addressed", partial(_adviser, live)),
        ("2 · How the data is collected (Arctic Shift + Shreddit)", partial(_collection, live)),
        ("3 · Annotation models (8–9B), performance, and before vs now", partial(_models, live)),
        ("4 · The five dataset versions and what changed", partial(_datasets, live)),
        ("5 · How much data is used (targets vs context)", partial(_usage, live)),
        ("6 · Media flags (unseen images and video)", partial(_media, live)),
        ("7 · Model results: manuscript and the new comparison", partial(_results, live)),
        ("8 · leische plans: routing context", _routing),
        ("9 · Gold set (human answer key)", partial(_gold, live)),
        ("10 · After the gold set: next actions, and why it matters", partial(_after_gold, live)),
        ("11 · Next steps before the defense, and expected results", partial(_next, live)),
        ("12 · Other details and likely panel questions", _misc),
    ]
    for title, body in sections:
        with st.expander(title, expanded=False):
            body()
