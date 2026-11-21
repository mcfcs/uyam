"""Uyam gold labeling: a stand-alone Streamlit site with only the Annotation Review tab.

Launched by run-label-app.bat on port 8502 (the main app stays on 8501). It
loads nothing else (no corpus, no collection or pipeline panels), so every
click re-runs only the review page. Same database, same blind-first rules,
same saves as the main app's Annotation Review tab.
"""

from __future__ import annotations

import streamlit as st

st.set_page_config(page_title="Uyam · Gold labeling", page_icon="🏷️", layout="wide")

try:
    from uyam.annotate.review_ui import render_review_tab

    render_review_tab()
except ImportError as exc:
    st.info(f"Annotation review needs the annotate extras: `pip install -e .[annotate]` ({exc})")
