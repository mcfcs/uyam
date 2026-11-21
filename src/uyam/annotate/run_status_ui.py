"""Streamlit panel: live status of annotation runs (e.g. the dataset-v3 re-annotation).

Shown at the top of the Annotation tab. It reads every config/annotation*.yaml
whose database exists, shows the runs that are not the default dataset (and
the default one only while its lane is running), and refreshes on its own.
Everything is read-only (run_status opens the DB with mode=ro).
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from uyam.annotate.config import load_annotation_config
from uyam.annotate.run_status import discover_runs, lane_log_tail, run_status

_CONFIG_DIR = Path(__file__).resolve().parent.parent.parent.parent / "config"


def _ago(when: datetime | None, now: datetime) -> str:
    if when is None:
        return "never"
    seconds = int((now - when).total_seconds())
    if seconds < 90:
        return f"{max(seconds, 0)} s ago"
    if seconds < 5400:
        return f"{seconds // 60} min ago"
    if seconds < 172800:
        return f"{seconds // 3600} h ago"
    return when.astimezone().strftime("%Y-%m-%d")


def status_label(status: dict[str, Any]) -> str:
    if all(m.pending == 0 for m in status["models"]):
        return "✅ complete"
    if status["stalled"]:
        return "⚠️ stalled (running, but nothing written for 10+ min)"
    if status["running"]:
        return "🟢 running"
    return "⏸️ not running (resume with the same command)"


def render_status(st: Any, status: dict[str, Any], *, now: datetime, log: str = "") -> None:
    """Draw one run's status (separate from data loading, so it is testable)."""
    import pandas as pd  # type: ignore[import-untyped]

    st.markdown(f"**{status['db']}** · {status_label(status)}")
    target = status["target"] or 1
    st.progress(
        min(1.0, status["complete"] / target),
        text=f"{status['complete']:,} of {status['target']:,} items carry every annotator's vote",
    )
    c1, c2, c3, c4 = st.columns(4)
    current = next((m for m in status["models"] if m.key == status["current"]), None)
    c1.metric("Now annotating", status["current"] or "-")
    c2.metric(
        f"Items / min ({status['window_minutes']:.0f} min)",
        f"{current.rate_per_min:.1f}" if current else "0",
    )
    c3.metric(
        "Work left",
        f"{status['remaining_hours']} h" if status["remaining_hours"] is not None else "?",
    )
    eta = status["eta"]
    c4.metric("Estimated finish", eta.astimezone().strftime("%a %d %b %H:%M") if eta else "-")
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "annotator": m.key,
                    "model": m.model,
                    "done": m.done,
                    "failed": m.failed,
                    "pending": m.pending,
                    "items/min": m.rate_per_min,
                    "s/item": m.sec_per_item,
                    "last write": _ago(m.last_write, now),
                }
                for m in status["models"]
            ]
        ),
        width="stretch",
        hide_index=True,
    )
    lanes = ", ".join(
        f"{name}: {'running' if up else 'stopped'}" for name, up in status["lanes"].items()
    )
    st.caption(
        f"Lane jobs: {lanes}. The ETA assumes annotators run one after another on the "
        "local GPU at their recent (or past average) pace."
    )
    if log:
        with st.expander("Lane log (last lines)"):
            st.code(log, language=None)


def render_run_status_panel(config_dir: Path | None = None) -> None:
    import streamlit as st

    @st.fragment(run_every=30.0)
    def _panel() -> None:
        now = datetime.now(UTC)
        shown = 0
        for path in discover_runs(config_dir or _CONFIG_DIR):
            try:
                cfg = load_annotation_config(path)
                status = run_status(cfg, now=now)
            except Exception as exc:  # noqa: BLE001 - a broken run must not break the tab
                st.warning(f"{path.name}: status unavailable ({exc})")
                continue
            if path.stem == "annotation" and not status["running"]:
                continue  # the default dataset only while a run is live
            shown += 1
            with st.container(border=True):
                render_status(st, status, now=now, log=lane_log_tail(cfg))
        if not shown:
            st.caption("No annotation run in progress.")
        st.caption(f"Read-only · refreshes every 30 s · {datetime.now().strftime('%H:%M:%S')}")

    st.subheader("Current annotation runs")
    _panel()
