"""Streamlit "Reminders" tab: Gio's to-do list with persistent done-state.

Definitions come from config/reminders.yaml; ticks and Gio's own notes are
written to data/reminders.json the moment a box changes (on_change), so the
list is the same on every device viewing the app.
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

import streamlit as st

from uyam.reminders import Reminder, ReminderState, load_reminders, run_check

_SECTION_ORDER = (
    "Before any real run",
    "Data collection",
    "Annotation",
    "Handoff to leische",
    "Thesis manuscript",
    "Optional",
    "My notes",
)


@st.cache_data(ttl=30, show_spinner=False)
def _hint(check: str | None, data_dir: str) -> str | None:
    """Detectors read the DB/manifests; cache briefly so reruns stay snappy."""
    return run_check(check, Path(data_dir))


def pending_count(config_path: Path, state_path: Path) -> int:
    state = ReminderState(state_path)
    items = load_reminders(config_path) + state.custom_reminders()
    return sum(1 for r in items if r.id not in state.done)


def _toggle(state_path: Path, reminder_id: str, key: str) -> None:
    ReminderState(state_path).set_done(reminder_id, bool(st.session_state[key]))


def _delete(state_path: Path, reminder_id: str) -> None:
    ReminderState(state_path).delete_custom(reminder_id)


def _render_item(r: Reminder, state: ReminderState, state_path: Path, data_dir: Path) -> None:
    done_at = state.done.get(r.id)
    key = f"reminder_{r.id}"
    st.session_state[key] = done_at is not None
    cols = st.columns([0.92, 0.08]) if r.custom else [st.container()]
    with cols[0]:
        st.checkbox(
            f"~~{r.title}~~" if done_at else f"**{r.title}**",
            key=key,
            on_change=_toggle,
            args=(state_path, r.id, key),
        )
    if r.custom:
        cols[1].button(
            "✕",
            key=f"del_{r.id}",
            help="Delete this note",
            on_click=_delete,
            args=(state_path, r.id),
        )
    hint = _hint(r.check, str(data_dir))
    if done_at:
        st.caption(f"Done {done_at.replace('T', ' ')[:16]} UTC")
    if hint:
        st.caption(f"Detected: {hint}")
    if r.details or r.command:
        with st.expander("Details", expanded=False):
            if r.details:
                st.markdown(r.details)
            if r.command:
                st.code(r.command, language="bash")


def render_reminders_tab(config_path: Path, state_path: Path, data_dir: Path) -> None:
    try:
        reminders = load_reminders(config_path)
    except (OSError, ValueError) as exc:
        st.error(f"Could not read {config_path.name}: {exc}")
        return
    state = ReminderState(state_path)
    items = reminders + state.custom_reminders()
    n_done = sum(1 for r in items if r.id in state.done)

    st.subheader("Reminders")
    st.caption(
        "Things to do on your end. Tick a box when it's done; it's saved on this "
        "machine and shows the same from any device. \"Detected\" lines are hints "
        "from the data, not ticks."
    )
    if items:
        st.progress(n_done / len(items), text=f"{n_done} of {len(items)} done")

    show = st.radio(
        "Show",
        ["Pending", "All", "Done"],
        horizontal=True,
        key="reminders_filter",
        label_visibility="collapsed",
    )
    visible = [r for r in items if show == "All" or (show == "Done") == (r.id in state.done)]
    if not visible:
        st.success("Nothing pending. 🎉" if show == "Pending" else "Nothing here yet.")

    by_section: dict[str, list[Reminder]] = defaultdict(list)
    for r in visible:
        by_section[r.section].append(r)
    order = [s for s in _SECTION_ORDER if s in by_section] + sorted(
        s for s in by_section if s not in _SECTION_ORDER
    )
    for section in order:
        st.markdown(f"#### {section}")
        for r in by_section[section]:
            _render_item(r, state, state_path, data_dir)

    st.divider()
    with st.form("add_reminder", clear_on_submit=True):
        st.markdown("**Add your own reminder**")
        title = st.text_input("Title", placeholder="e.g. Ask adviser about the κ threshold")
        details = st.text_area("Details (optional)", height=80)
        if st.form_submit_button("Add") and title.strip():
            ReminderState(state_path).add_custom(title, details)
            st.rerun()
