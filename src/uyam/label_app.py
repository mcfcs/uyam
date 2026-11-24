"""Uyam gold labeling: a stand-alone Streamlit site with only the Annotation Review tab.

Launched by run-label-app.bat on port 8502 (the main app stays on 8501). It
loads nothing else (no corpus, no collection or pipeline panels), so every
click re-runs only the review page. A second, lazy tab shows the Progress
notes (the main app's notes page, read-only). Same database, same blind-first rules,
same saves as the main app's Annotation Review tab.
"""

from __future__ import annotations

from pathlib import Path

import streamlit as st

st.set_page_config(page_title="Uyam · Gold labeling", page_icon="🏷️", layout="wide")

# Lazy tabs: only the open tab runs, so labeling never pays for the notes.
tab_label, tab_notes = st.tabs(
    key="label_site_tabs", on_change="rerun", tabs=["Gold labeling", "Progress notes"]
)

with tab_label:
    if tab_label.open:
        try:
            from uyam.annotate.review_ui import render_review_tab

            render_review_tab()
        except ImportError as exc:
            st.info(
                f"Annotation review needs the annotate extras: `pip install -e .[annotate]` ({exc})"
            )

with tab_notes:
    if tab_notes.open:
        try:
            from uyam.config import load_config
            from uyam.progress_notes_ui import render_progress_notes

            _repo = Path(__file__).resolve().parents[2]
            _data = load_config().data_dir
            render_progress_notes(_data if _data.is_absolute() else _repo / _data)
        except Exception as exc:  # noqa: BLE001 - never take the labeling site down
            st.error(f"Progress notes unavailable: {exc}")
