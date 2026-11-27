# ruff: noqa: E501
"""False-positive tagging page (8502): Gio tags why the LLMs over-called a gold post."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import streamlit as st

from uyam.annotate.config import load_annotation_config
from uyam.annotate.db import AnnotationDatabase
from uyam.annotate.fp_tagging import (
    TAGS,
    false_positives,
    load_tags,
    model_votes,
    save_tag,
    summarize,
)

NAMES = {"qwen3": "qwen3 8B", "sealion": "Gemma-SEA-LION 9B", "llamasealion": "Llama-SEA-LION 8B"}


def _media_flags(db: AnnotationDatabase, out_dir: Path, items: list[str]) -> dict[str, bool | None]:
    from uyam.annotate.review_ui import _media_for

    flags: dict[str, bool | None] = {}
    for f in items:
        media = _media_for(db, out_dir, f)
        flags[f] = None if media is None else bool(media.get("text_only"))
    return flags


def render_fp_tagging(config_path: Path, tags_path: Path) -> None:
    st.header("False-positive tagging")
    st.caption(
        "Gold posts the v5 label calls sarcastic but you labeled not sarcastic. Tag why the LLMs over-called "
        "each one (pick all that apply). Tags are saved to data/fp_tags.json only; your gold labels and the "
        "database are never changed."
    )
    cfg = load_annotation_config(config_path)
    if not cfg.db_path.exists():
        st.info(f"No database at {cfg.db_path}.")
        return
    items = false_positives(cfg.db_path, cfg.prompt_version)
    if not items:
        st.info("No false positives found.")
        return
    tags = load_tags(tags_path)
    db = AnnotationDatabase(cfg.db_path)
    try:
        media = _media_flags(db, cfg.export.out_dir, items)
        summary = summarize(items, tags, media)
        c1, c2, c3 = st.columns(3)
        c1.metric("False positives", summary["false positives"])
        c2.metric("Tagged", f"{summary['tagged']} / {summary['false positives']}")
        c3.metric("Reply to unseen media", summary["replies to unseen media (data flag)"])

        only_untagged = st.toggle("Show untagged only", value=False, key="fp_only_untagged")
        shown = [f for f in items if not (only_untagged and (tags.get(f) or {}).get("tags"))]
        if not shown:
            st.success("Every false positive is tagged.")
        else:
            current_pos = st.session_state.get("fp_select", 0)
            if not isinstance(current_pos, int) or not 0 <= current_pos < len(shown):
                st.session_state["fp_select"] = 0

            def label(i: int) -> str:
                f = shown[i]
                done = "✓ " if (tags.get(f) or {}).get("tags") else ""
                return f"{i + 1}. {done}{f}"

            pos = int(st.selectbox("Post", range(len(shown)), format_func=label, key="fp_select"))
            fullname = shown[pos]
            _render_item(db, cfg, fullname, tags.get(fullname) or {}, tags_path, media, len(shown), pos)

        with st.expander("Summary (for UYAM_LATEST and the slides)", expanded=summary["tagged"] > 0):
            rows = [{"tag": TAGS[k], "posts": n} for k, n in summary["per tag"].items()]
            st.dataframe(rows, hide_index=True, width="stretch")
            st.caption(
                f"{summary['tagged']} of {summary['false positives']} tagged; a post can carry several tags. "
                f"{summary['replies to unseen media (data flag)']} reply to unseen images/video (from the data, "
                "independent of your tags)."
            )
    finally:
        db.close()


def _save(tags_path: Path, fullname: str, chosen: list[str], note: str, pos: int, total: int) -> None:
    """Button callback: runs before the rerun, so it may move the post selector.

    With "untagged only" on, the saved post leaves the list and the next one
    slides into the same position, so the selector stays put.
    """
    save_tag(tags_path, fullname, chosen, note)
    if not st.session_state.get("fp_only_untagged") and pos + 1 < total:
        st.session_state["fp_select"] = pos + 1


def _render_item(
    db: AnnotationDatabase,
    cfg: Any,
    fullname: str,
    current: dict[str, Any],
    tags_path: Path,
    media: dict[str, bool | None],
    total: int,
    pos: int,
) -> None:
    from uyam.annotate.review_ui import _media_for, _render_context

    left, right = st.columns([3, 2])
    with left:
        _render_context(st, db, fullname, media=_media_for(db, cfg.export.out_dir, fullname), show_link=True)
    with right:
        st.markdown("**Model votes (guideline prompt)**")
        for vote in model_votes(cfg.db_path, cfg.prompt_version, fullname):
            verdict = "sarcastic" if vote["sarcastic"] else "not"
            conf = vote["confidence"]
            st.markdown(
                f"- **{NAMES.get(vote['model'], vote['model'])}**: {verdict}"
                + (f" ({conf:.2f})" if conf is not None else "")
            )
            if vote["rationale"]:
                st.caption(str(vote["rationale"]))
        if media.get(fullname) is False:
            st.info("Replies to a post with an image/video nobody saw.")
        st.markdown("**Why did the LLMs over-call it?**")
        chosen = [
            key
            for key, text in TAGS.items()
            if st.checkbox(text, value=key in (current.get("tags") or []), key=f"fp_{fullname}_{key}")
        ]
        note = st.text_input("Note (optional)", value=current.get("note") or "", key=f"fp_{fullname}_note")
        st.button(
            "Save tags and go to the next",
            type="primary",
            key=f"fp_{fullname}_save",
            disabled=not chosen,
            on_click=_save,
            args=(tags_path, fullname, chosen, note, pos, total),
        )
        if current.get("tags"):
            st.caption(f"Saved {current.get('updated_at', '')}")
