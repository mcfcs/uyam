"""Streamlit "Annotation Review" tab.

Serves two queues from review_queue:
- gold: the stratified validation subset — labeled BLIND (model votes hidden
  by default) so the human gold standard is independent of the ensemble
- low_confidence: items the adjudicator was unsure about — model votes shown,
  the human label becomes final

Both queues follow the same blind-first workflow (rules in review_workflow.py):
1. label the item; the first save also snapshots the labels into
   first_pass_json. Nothing from assist_suggestions is read before this.
2. the item stays on screen: "Show Claude's take" (only when a suggestion
   exists) reveals it and persists assist_seen = 1; "Revise" re-saves the
   labels but never replaces first_pass_json; "Next item" moves on.

Imported lazily by app.py so the collector UI works without the annotate
extras installed.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from uyam.annotate.config import load_annotation_config
from uyam.annotate.db import AnnotationDatabase
from uyam.annotate.review_workflow import (
    LABEL_KEYS,
    assist_defaults,
    build_review_row,
    highlight_spans,
    reveal_assist,
    revealable_assist,
    span_from_selection,
    split_sentences,
    upfront_assist,
)

# (key, label, definition, worked example) — definitions mirror the cue taxonomy in
# annotate/prompts.py so human gold labels and model votes are graded on one rubric.
_CUES = (
    (
        "cue_polarity_inversion",
        "Polarity inversion",
        "The stated sentiment is the opposite of the intended one.",
        '"Ang galing mo naman" said to someone who just broke something — praise on the '
        "surface, criticism underneath.",
    ),
    (
        "cue_rhetorical_intent",
        "Rhetorical intent",
        "Mock praise, feigned agreement, or a rhetorical question not seeking an answer.",
        '"Edi wow", "sana all" used pointedly, "talaga lang ha" — the form is praise or a '
        "question, the function is a jab.",
    ),
    (
        "cue_contextual_incongruity",
        "Contextual incongruity",
        "The literal reading clashes with the thread context or the situation described.",
        'A rant about a 6-hour outage, replied to with "Reliable service as always" — only '
        "the surrounding thread makes the mismatch visible.",
    ),
    (
        "cue_hyperbole",
        "Hyperbole",
        "Exaggeration or stacked intensifiers signaling non-literal intent.",
        '"Sobrang THRILLED talaga ako", "best day ever!!!" — the intensity overshoots the '
        "situation on purpose.",
    ),
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
        url = (
            permalink if str(permalink).startswith("http") else f"https://www.reddit.com{permalink}"
        )
        st.caption(f"[view on reddit]({url})")


def _label_form(
    st: Any,
    *,
    prefix: str,
    sentences: list[str],
    defaults: dict[str, Any] | None,
) -> tuple[bool, dict[str, Any], str]:
    """The label form, prefilled from ``defaults`` (a saved row when revising, or
    Claude's suggestion when the upfront toggle is on).

    Not an st.form: "Sarcastic?" must enable or disable the rest as it changes.
    When it is "no", the sentence ticks and cues are disabled and saved as
    empty/false (GUIDELINE §3 step 7), and the intended sentiment follows the
    literal one (S3) unless "differs" is ticked.

    Returns (submitted, labels keyed by LABEL_KEYS, notes).
    """
    d = defaults or {}

    def idx(options: list[str], value: Any) -> int:
        return options.index(value) if value in options else 0

    saved_span = set(str(d.get("sarcastic_span") or "").split("\n"))
    sarcastic = st.radio(
        "Sarcastic?",
        ["no", "yes"],
        index=int(bool(d.get("sarcastic"))),
        horizontal=True,
        key=f"{prefix}_sarcastic",
    )
    is_sarcastic = sarcastic == "yes"
    if sentences:
        st.caption(
            "Tick the sentence(s) of the target that carry the sarcasm."
            if is_sarcastic
            else "Sentence ticks and cues are off while Sarcastic = no."
        )
    ticked = [
        st.checkbox(
            sentence,
            # A one-sentence target is its own span (GUIDELINE T2); when revising a
            # sarcastic label, re-tick the saved span.
            value=(sentence in saved_span)
            if d.get("sarcastic")
            else len(sentences) == 1,
            disabled=not is_sarcastic,
            key=f"{prefix}_span_{i}",
        )
        for i, sentence in enumerate(sentences)
    ]
    language = st.radio(
        "Language",
        _LANGUAGES,
        index=idx(_LANGUAGES, d.get("language")),
        horizontal=True,
        key=f"{prefix}_language",
    )
    literal = st.radio(
        "Literal sentiment",
        _SENTIMENTS,
        index=idx(_SENTIMENTS, d.get("literal_sentiment")),
        horizontal=True,
        key=f"{prefix}_literal",
    )
    def intended_choice() -> str:
        return str(
            st.radio(
                "Intended sentiment",
                _SENTIMENTS,
                index=idx(_SENTIMENTS, d.get("intended_sentiment")),
                horizontal=True,
                key=f"{prefix}_intended",
            )
        )
    if is_sarcastic:
        intended = intended_choice()
    else:
        differs = st.checkbox(
            "Intended sentiment differs from literal (rare when not sarcastic; say why in notes)",
            value=bool(d.get("intended_sentiment"))
            and d.get("intended_sentiment") != d.get("literal_sentiment"),
            key=f"{prefix}_intended_differs",
        )
        if differs:
            intended = intended_choice()
        else:
            intended = literal
            st.caption(f"Intended sentiment = literal ({literal}), GUIDELINE S3.")
    st.caption(
        "Sarcasm cues present in the target:"
        if is_sarcastic
        else "Sarcasm cues (only for sarcastic items):"
    )
    cue_values: dict[str, bool] = {}
    cue_cols = st.columns(len(_CUES))
    for col, (cue_key, cue_label, definition, example) in zip(cue_cols, _CUES, strict=True):
        with col:
            cue_values[cue_key] = st.checkbox(
                cue_label,
                value=bool(d.get(cue_key)),
                help=f"{definition}\n\n*Example:* {example}",
                disabled=not is_sarcastic,
                key=f"{prefix}_{cue_key}",
            )
    with st.expander("Cue guide"):
        for _, cue_label, definition, example in _CUES:
            st.markdown(f"**{cue_label}** — {definition}")
            st.caption(f"Example: {example}")
    unsure = st.checkbox(
        "Unsure about this label", value=bool(d.get("unsure")), key=f"{prefix}_unsure"
    )
    notes = st.text_input(
        "Notes (optional)", value=str(d.get("notes") or ""), key=f"{prefix}_notes"
    )
    submitted = st.button("Save label", type="primary", key=f"{prefix}_save")

    labels: dict[str, Any] = {
        "sarcastic": int(is_sarcastic),
        "language": language,
        "literal_sentiment": literal,
        "intended_sentiment": intended,
        # Cues only describe a sarcastic span; all false otherwise (§3 step 7).
        **{k: int(v and is_sarcastic) for k, v in cue_values.items()},
        "sarcastic_span": span_from_selection(is_sarcastic, sentences, ticked),
        "unsure": int(unsure),
    }
    return bool(submitted), labels, str(notes)


def _labels_summary(labels: dict[str, Any]) -> str:
    cues = [label for key, label, _, _ in _CUES if labels.get(key)]
    parts = [
        f"sarcastic **{'yes' if labels.get('sarcastic') else 'no'}**",
        f"language {labels.get('language')}",
        f"literal {labels.get('literal_sentiment')} → intended {labels.get('intended_sentiment')}",
        f"cues: {', '.join(cues) or 'none'}",
    ]
    if labels.get("unsure"):
        parts.append("**unsure**")
    return " · ".join(parts)


def _render_assist(st: Any, assist: dict[str, Any], target_text: str) -> None:
    with st.container(border=True):
        st.markdown("#### Claude's take")
        verdict = assist.get("sarcastic")
        verdict_text = (
            "no verdict" if verdict is None else ("sarcastic" if verdict else "not sarcastic")
        )
        confidence = assist.get("confidence")
        conf_text = "n/a" if confidence is None else f"{float(confidence):.2f}"
        st.markdown(f"**Verdict:** {verdict_text} (confidence {conf_text})")
        if assist.get("language") or assist.get("literal_sentiment"):
            cues = [label for key, label, _, _ in _CUES if assist.get(key)]
            st.markdown(
                f"**Language:** {assist.get('language') or '-'} · **Sentiment:** literal "
                f"{assist.get('literal_sentiment') or '-'} → intended "
                f"{assist.get('intended_sentiment') or '-'} · **Cues:** "
                f"{', '.join(cues) or 'none'}"
            )
        span = str(assist.get("sarcastic_span") or "")
        if span.strip():
            marked, missing = highlight_spans(target_text, span)
            if len(missing) < len([p for p in span.split("\n") if p.strip()]):
                st.markdown("**Span, highlighted in the target:**")
                st.markdown(marked, unsafe_allow_html=True)
            for part in missing:
                st.markdown("**Span (not found verbatim in the target):**")
                st.text(part)
        if assist.get("rationale"):
            st.markdown(f"**Rationale:** {assist['rationale']}")
        st.caption(
            f"source: {assist.get('source')} · guideline: "
            f"{assist.get('guideline_version') or 'n/a'}"
        )


def _set_state(key: str, value: Any) -> None:
    import streamlit as st

    st.session_state[key] = value


def _next_item(active_key: str, edit_key: str) -> None:
    import streamlit as st

    st.session_state.pop(active_key, None)
    st.session_state.pop(edit_key, None)


def _reveal(db_path: Path, fullname: str) -> None:
    with AnnotationDatabase(db_path) as db:
        reveal_assist(db, fullname)


def _open_labeled(active_key: str, select_key: str) -> None:
    import streamlit as st

    choice = st.session_state.get(select_key)
    if choice:
        st.session_state[active_key] = str(choice)


def _render_saved_item(
    st: Any,
    db: AnnotationDatabase,
    db_path: Path,
    fullname: str,
    existing: dict[str, Any],
    target_text: str,
    *,
    active_key: str,
    edit_key: str,
) -> None:
    """After the first save: saved labels, the optional assist reveal, Revise, Next item."""
    st.success("Label saved.")
    st.markdown(f"**Current label:** {_labels_summary(existing)}")
    if existing.get("sarcastic_span"):
        st.caption("Your span: " + " / ".join(str(existing["sarcastic_span"]).split("\n")))
    first_pass_raw = existing.get("first_pass_json")
    if first_pass_raw:
        first_pass = json.loads(str(first_pass_raw))
        if any(first_pass.get(k) != existing.get(k) for k in LABEL_KEYS):
            st.caption(f"Blind first pass (kept on record): {_labels_summary(first_pass)}")

    # Only reached once a human_reviews row exists; revealable_assist re-checks
    # that before it reads assist_suggestions.
    assist = revealable_assist(db, fullname)
    if assist is None:
        st.caption("No Claude suggestion for this item.")
    elif existing.get("assist_seen"):
        _render_assist(st, assist, target_text)
    else:
        st.button(
            "Show Claude's take",
            key=f"reveal_{fullname}",
            help="Shows a pre-computed Claude suggestion. Your first-pass label stays on "
            "record unchanged.",
            on_click=_reveal,
            args=(db_path, fullname),
        )

    c1, c2 = st.columns(2)
    c1.button("Revise", key=f"revise_btn_{fullname}", on_click=_set_state, args=(edit_key, True))
    c2.button("Next item", key=f"next_{fullname}", on_click=_next_item, args=(active_key, edit_key))


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
        all_items = db.review_queue_items(only_pending=False)
        queue = [q for q in all_items if not q["completed"]]
        gold_pending = [q for q in queue if q["reason"] == "gold"]
        lc_pending = [q for q in queue if q["reason"] == "low_confidence"]
        done = len(all_items) - len(queue)

        c1, c2, c3 = st.columns(3)
        c1.metric("Gold pending", len(gold_pending))
        c2.metric("Low-confidence pending", len(lc_pending))
        c3.metric("Reviewed", done)

        reason = st.radio(
            "Queue",
            ["gold", "low_confidence"],
            horizontal=True,
            key="review_queue_reason",
            help="Gold items are labeled blind (validation subset); low-confidence items "
            "are final-call adjudications.",
        )
        upfront = st.toggle(
            "Show Claude's suggestion before I label (second judgement)",
            key="review_assist_upfront",
            help="Off = blind first pass (the suggestion can be revealed after you save). "
            "On = the suggestion shows next to each unlabeled item; items you save with it "
            "showing are recorded as Claude-informed (assist_before_first_pass).",
        )
        pending = gold_pending if reason == "gold" else lc_pending
        pending_names = [str(q["reddit_fullname"]) for q in pending]
        active_key = f"review_active_{reason}"

        labeled = [
            str(q["reddit_fullname"]) for q in all_items if q["reason"] == reason and q["completed"]
        ]
        if labeled:
            with st.expander(f"Revisit a labeled item ({len(labeled)})"):
                select_key = f"review_revisit_{reason}"
                st.selectbox("Labeled item", labeled, key=select_key)
                st.button(
                    "Open",
                    key=f"review_open_{reason}",
                    on_click=_open_labeled,
                    args=(active_key, select_key),
                )

        # The item on screen: the one just saved or reopened (kept until "Next
        # item"), else the head of the pending queue.
        active = st.session_state.get(active_key)
        if active is not None and db.get_human_review(str(active)) is None:
            active = None
        if active is not None:
            fullname = str(active)
        elif pending_names:
            fullname = pending_names[0]
        else:
            st.success(f"No pending items in the {reason} queue.")
            return
        edit_key = f"review_editing_{fullname}"

        status = "" if fullname in pending_names else "labeled; "
        st.caption(f"Item {fullname} — {status}{len(pending_names)} left in this queue")
        _render_context(st, db, fullname)

        show_votes = reason == "low_confidence" or st.checkbox(
            "Reveal model votes (breaks blindness: an unsaved item then leaves the gold κ)",
            value=False,
            key=f"votes_{fullname}",
        )
        votes_seen_key = f"votes_seen_{fullname}"
        if show_votes:
            # Sticky for the session: unticking the box doesn't make it blind again.
            st.session_state[votes_seen_key] = True
            agg = db.get_aggregate(fullname)
            if agg:
                st.json(
                    {
                        "sarcasm_votes": agg.get("sarcasm_votes"),
                        "resolved_by": agg.get("resolved_by"),
                        "votes": json.loads(str(agg.get("votes_json") or "{}")),
                    }
                )

        record = db.get_record(fullname)
        target_text = str(record["text"]) if record else ""
        existing = db.get_human_review(fullname)
        editing = existing is not None and bool(st.session_state.get(edit_key))

        shown_upfront = False
        assist: dict[str, Any] | None = None
        if existing is None or editing:
            if existing is None and upfront:
                # Opt-in second judgement: read the suggestion before the first save.
                assist = upfront_assist(db, fullname)
                if assist is None:
                    st.caption("No Claude suggestion for this item.")
                else:
                    _render_assist(st, assist, target_text)
                    shown_upfront = True
                    st.caption(
                        "The form below starts from Claude's take; change anything you "
                        "disagree with. Saving records this first pass as Claude-informed."
                    )
            elif existing is None:
                # Blind first pass: assist_suggestions is never read on this path.
                st.caption(
                    "Claude's take, if one exists for this item, can be revealed after you "
                    "save, or shown before labeling with the toggle above. Your first saved "
                    "label is kept as the first pass."
                )
            if editing:
                form_prefix, form_defaults = f"revise_{fullname}", existing
            elif shown_upfront:
                # Own widget keys, so turning the toggle on refills the form.
                form_prefix, form_defaults = f"reviewc_{fullname}", assist_defaults(assist)
            else:
                form_prefix, form_defaults = f"review_{fullname}", None
            submitted, labels, notes = _label_form(
                st,
                prefix=form_prefix,
                sentences=split_sentences(target_text),
                defaults=form_defaults,
            )
            if editing:
                st.button("Cancel revision", on_click=_set_state, args=(edit_key, False))
            if submitted:
                db.upsert_human_review(
                    build_review_row(
                        fullname,
                        labels,
                        notes=notes,
                        is_gold=reason == "gold",
                        existing=existing,
                        votes_seen=bool(st.session_state.get(votes_seen_key)),
                        assist_upfront=shown_upfront,
                    )
                )
                st.session_state[active_key] = fullname
                st.session_state[edit_key] = False
                st.rerun()
        else:
            _render_saved_item(
                st,
                db,
                cfg.db_path,
                fullname,
                existing,
                target_text,
                active_key=active_key,
                edit_key=edit_key,
            )

        with st.expander("Agreement statistics"):
            if st.button("Compute agreement report"):
                from uyam.annotate.aggregate import compute_agreement

                st.json(compute_agreement(cfg, cfg.prompt_version))
    finally:
        db.close()


