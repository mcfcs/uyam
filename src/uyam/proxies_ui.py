"""Streamlit "Proxies" tab: see, test, replace and restore proxies.txt.

Passwords are never rendered — lists show provider counts and masked URLs,
test results show host:port. See uyam.proxies_admin for the write rules
(backup first, atomic replace).
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import streamlit as st

from uyam import proxies_admin as pa


def _results_rows(results: list[pa.ProbeResult]) -> list[dict[str, Any]]:
    # All strings: mixed int/"-" columns break Arrow serialisation.
    return [
        {
            "proxy": r.proxy,
            "working": "✅" if r.ok else "❌",
            "HTTP": str(r.status) if r.status is not None else "-",
            "ms": str(r.ms) if r.ms is not None else "-",
            "error": r.error or "",
        }
        for r in results
    ]


def _summary(results: list[pa.ProbeResult]) -> str:
    ok = sum(r.ok for r in results)
    return f"{ok} of {len(results)} sampled proxies reached Reddit"


def _show_results(key: str) -> None:
    results = st.session_state.get(key)
    if not results:
        return
    ok = sum(r.ok for r in results)
    (st.success if ok == len(results) else st.warning if ok else st.error)(_summary(results))
    st.dataframe(_results_rows(results), hide_index=True, width="stretch")


def _provider_table(parsed: pa.ParsedList) -> list[dict[str, Any]]:
    return [{"provider": p, "proxies": n} for p, n in parsed.providers.most_common()]


def render_proxies_tab(proxies_path: Path, backup_dir: Path) -> None:
    from uyam.scrape_status import scrape_is_running

    st.subheader("Proxies")
    st.caption(
        "Shreddit scrapes route every browser through `proxies.txt`. It is read when a "
        "collection starts, so changes here apply to the next run. Passwords are never "
        "shown. The archive source (`--source archive`) does not use proxies."
    )
    if scrape_is_running():
        st.warning("A scrape is running right now. It keeps its current proxies until it ends.")
    flash = st.session_state.pop("proxy_flash", None)
    if flash:
        st.success(flash)

    # ---------------------------------------------------------------- current
    current = pa.read_file(proxies_path)
    st.markdown("#### Current list")
    if not proxies_path.exists():
        st.error("No `proxies.txt` yet. Add your provider's list below.")
    else:
        modified = datetime.fromtimestamp(proxies_path.stat().st_mtime, tz=UTC)
        c1, c2, c3 = st.columns(3)
        c1.metric("Proxies", f"{len(current.urls):,}")
        c2.metric("Providers", len(current.providers))
        c3.metric("Last changed", modified.date().isoformat())
        if current.invalid_lines:
            st.caption(f"{len(current.invalid_lines)} unparseable lines are ignored.")
        if current.urls:
            st.dataframe(_provider_table(current), hide_index=True, width="stretch")
            with st.expander("Masked sample"):
                st.code("\n".join(pa.mask(u) for u in current.urls[:5]), language="text")

            st.markdown("**Test the current list**")
            t1, t2 = st.columns([0.3, 0.7])
            sample = t1.number_input("Sample size", 1, 50, 10, key="proxy_test_sample")
            if t2.button("Test current proxies", key="proxy_test_current"):
                with st.spinner(f"Testing {sample} proxies against reddit.com…"):
                    st.session_state["proxy_results_current"] = pa.probe_proxies(
                        current.urls, sample=int(sample)
                    )
            _show_results("proxy_results_current")

            with st.expander("Remove a provider (e.g. an expired plan)"):
                gone = st.multiselect("Providers to remove", list(current.providers))
                confirm = st.checkbox(
                    "I understand these proxies will be removed (a backup is kept)",
                    key="proxy_remove_confirm",
                )
                if st.button("Remove", disabled=not (gone and confirm), key="proxy_remove"):
                    out = pa.remove_providers(proxies_path, backup_dir, set(gone))
                    st.session_state.pop("proxy_results_current", None)
                    st.session_state["proxy_flash"] = (
                        f"Removed {out['removed']:,} proxies; {out['written']:,} left. "
                        f"Backup: {Path(str(out['backup'])).name}"
                    )
                    st.rerun()

    # ---------------------------------------------------------------- new list
    st.markdown("#### Add or replace proxies")
    st.caption(
        "Paste your provider's list or upload its .txt file, one proxy per line. Accepted: "
        "`host:port:user:pass`, `user:pass:host:port`, `user:pass@host:port`, "
        "`http(s)://user:pass@host:port`, `socks5://host:port`, `host:port`."
    )
    upload = st.file_uploader("Proxy list (.txt)", type=["txt"], key="proxy_upload")
    pasted = st.text_area("…or paste here", height=140, key="proxy_paste")
    text = (upload.getvalue().decode("utf-8", errors="replace") if upload else "") + (
        "\n" + pasted if pasted else ""
    )
    if not text.strip():
        _render_backups(proxies_path, backup_dir)
        return

    new = pa.parse_text(text)
    p1, p2, p3 = st.columns(3)
    p1.metric("Valid", f"{len(new.urls):,}")
    p2.metric("Invalid lines", len(new.invalid_lines))
    p3.metric("Duplicates dropped", new.duplicates)
    if new.invalid_lines:
        st.warning(
            "Lines not recognised as proxies: "
            + ", ".join(str(n) for n in new.invalid_lines[:20])
            + (" …" if len(new.invalid_lines) > 20 else "")
        )
    if new.socks_with_auth:
        st.warning(
            f"{new.socks_with_auth} SOCKS proxies have a username/password. Chrome cannot "
            "authenticate to SOCKS proxies, so Shreddit will fail on them. Ask the "
            "provider for HTTP(S) endpoints instead."
        )
    if not new.urls:
        _render_backups(proxies_path, backup_dir)
        return
    st.dataframe(_provider_table(new), hide_index=True, width="stretch")
    with st.expander("Masked preview"):
        st.code("\n".join(pa.mask(u) for u in new.urls[:5]), language="text")

    if st.button("Test the new list first (10 random)", key="proxy_test_new"):
        with st.spinner("Testing new proxies against reddit.com…"):
            st.session_state["proxy_results_new"] = pa.probe_proxies(new.urls, sample=10)
    _show_results("proxy_results_new")

    mode = st.radio(
        "Save as",
        ["Replace the whole list", "Append to the current list"],
        horizontal=True,
        key="proxy_mode",
    )
    if st.button("Save to proxies.txt", type="primary", key="proxy_save"):
        out = pa.save(
            proxies_path,
            backup_dir,
            new,
            mode="replace" if mode.startswith("Replace") else "append",
        )
        for k in ("proxy_results_current", "proxy_results_new"):
            st.session_state.pop(k, None)
        backup_note = f" Backup: {Path(str(out['backup'])).name}" if out["backup"] else ""
        st.session_state["proxy_flash"] = (
            f"Saved {out['written']:,} proxies (was {out['before']:,}).{backup_note} "
            "Clear the upload/paste box when you're done."
        )
        st.rerun()

    _render_backups(proxies_path, backup_dir)


def _render_backups(proxies_path: Path, backup_dir: Path) -> None:
    backups = pa.list_backups(backup_dir)
    if not backups:
        return
    with st.expander(f"Backups ({len(backups)})"):
        choice = st.selectbox(
            "Restore a previous proxies.txt",
            backups,
            format_func=lambda p: f"{p.name} — {len(pa.read_file(p).urls):,} proxies",
            key="proxy_backup_choice",
        )
        if st.button("Restore this backup", key="proxy_restore"):
            pa.restore(proxies_path, backup_dir, Path(choice))
            st.session_state["proxy_flash"] = (
                f"Restored {Path(choice).name} (the list it replaced was backed up too)."
            )
            st.rerun()
