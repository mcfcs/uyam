"""Re-aggregate stored annotator votes under stricter rules and score them on the gold.

leische's 2026-11-27 reply: the v5 majority label over-calls sarcasm (78 of
297 gold posts flagged, Gio 26). Before any new LLM run, check whether a
stricter rule over the SAME votes agrees better with Gio. No model runs here.

Rules (all from stored votes; none uses the gold):
- ``majority``: >= 2 of 3 say sarcastic (the shipped `labels.sarcastic`).
- ``unanimous``: all 3 (the pre-named candidate; Gio's sensitivity check).
- ``qwen3_and_llama``: qwen3 and Llama-SEA-LION both, SEA-LION ignored.
- ``confidence_floor``: a sarcastic vote counts only at confidence >= 0.85
  (fixed in advance, about the median), and >= 2 counted votes are needed.
- ``dawid_skene``: each annotator's sensitivity and specificity are learned by
  EM from all posts' votes (unsupervised; Dawid & Skene 1979), and votes are
  combined by those weights. A down-weighted SEA-LION, learned, not tuned.

Only ``unanimous`` is a pre-named candidate; the others are exploratory, since
picking the best of several on the same gold would be tuning on it.
"""

from __future__ import annotations

import math
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np

from uyam.progress_notes import GoldItem, cohen_kappa

KEYS = ("qwen3", "sealion", "llamasealion")
CONFIDENCE_FLOOR = 0.85
PRE_NAMED = "unanimous"

Votes = dict[str, dict[str, tuple[int, float]]]  # fullname -> model -> (sarcastic, confidence)


def load_votes(db: Path, prompt_version: str) -> Votes:
    """Stored annotator votes of the three-model lineup, posts with all three only."""
    conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True, timeout=10)
    try:
        rows = conn.execute(
            "SELECT reddit_fullname, model_key, sarcastic, confidence FROM llm_annotations "
            "WHERE prompt_version = ? AND role = 'annotator' AND sarcastic IS NOT NULL",
            (prompt_version,),
        ).fetchall()
    finally:
        conn.close()
    votes: Votes = {}
    for name, key, value, conf in rows:
        if key in KEYS:
            votes.setdefault(str(name), {})[str(key)] = (int(value), float(conf or 0.0))
    return {f: v for f, v in votes.items() if len(v) == len(KEYS)}


def _yes(v: dict[str, tuple[int, float]]) -> int:
    return sum(s for s, _ in v.values())


def rule_majority(v: dict[str, tuple[int, float]]) -> int:
    return int(_yes(v) >= 2)


def rule_unanimous(v: dict[str, tuple[int, float]]) -> int:
    return int(_yes(v) == len(KEYS))


def rule_qwen3_and_llama(v: dict[str, tuple[int, float]]) -> int:
    return int(v["qwen3"][0] == 1 and v["llamasealion"][0] == 1)


def rule_confidence_floor(v: dict[str, tuple[int, float]]) -> int:
    return int(sum(1 for s, c in v.values() if s == 1 and c >= CONFIDENCE_FLOOR) >= 2)


def dawid_skene(votes: Votes, *, iterations: int = 200, tol: float = 1e-8) -> dict[str, Any]:
    """Binary Dawid-Skene EM over all posts' votes. Returns labels and per-annotator rates."""
    names = sorted(votes)
    y = np.array([[votes[f][k][0] for k in KEYS] for f in names], dtype=float)
    t = y.mean(axis=1)  # start from the vote share
    eps = 1e-6
    sens = spec = np.zeros(len(KEYS))
    prior = 0.5
    for _ in range(iterations):
        prior = float(np.clip(t.mean(), eps, 1 - eps))
        sens = np.clip((t[:, None] * y).sum(0) / max(t.sum(), eps), eps, 1 - eps)
        spec = np.clip(((1 - t)[:, None] * (1 - y)).sum(0) / max((1 - t).sum(), eps), eps, 1 - eps)
        log_odds = (
            math.log(prior / (1 - prior))
            + (y * np.log(sens / (1 - spec))).sum(1)
            + ((1 - y) * np.log((1 - sens) / spec)).sum(1)
        )
        new = 1 / (1 + np.exp(-log_odds))
        if float(np.abs(new - t).max()) < tol:
            t = new
            break
        t = new
    return {
        "labels": {f: int(p >= 0.5) for f, p in zip(names, t, strict=True)},
        "posterior": {f: float(p) for f, p in zip(names, t, strict=True)},
        "prior": prior,
        "sensitivity": {k: float(s) for k, s in zip(KEYS, sens, strict=True)},
        "specificity": {k: float(s) for k, s in zip(KEYS, spec, strict=True)},
    }


SIMPLE_RULES: dict[str, Callable[[dict[str, tuple[int, float]]], int]] = {
    "majority": rule_majority,
    "unanimous": rule_unanimous,
    "qwen3_and_llama": rule_qwen3_and_llama,
    "confidence_floor": rule_confidence_floor,
}


def all_rule_labels(votes: Votes) -> tuple[dict[str, dict[str, int]], dict[str, Any]]:
    labels = {name: {f: fn(v) for f, v in votes.items()} for name, fn in SIMPLE_RULES.items()}
    ds = dawid_skene(votes)
    labels["dawid_skene"] = ds["labels"]
    return labels, ds


def _stats(
    h: np.ndarray[Any, Any], m: np.ndarray[Any, Any]
) -> tuple[float | None, float | None, float | None]:
    k = cohen_kappa(list(h.astype(int)), list(m.astype(int)))
    tp = float((h * m).sum())
    precision = tp / m.sum() if m.sum() else None
    recall = tp / h.sum() if h.sum() else None
    return k, precision, recall


def _ci(values: list[float]) -> tuple[float, float] | None:
    vals = sorted(v for v in values if v is not None and not math.isnan(v))
    if len(vals) < 20:
        return None
    return vals[int(0.025 * len(vals))], vals[int(0.975 * len(vals)) - 1]


def score_rules(
    labels: dict[str, dict[str, int]],
    gold: dict[str, GoldItem],
    *,
    blind_only: bool = False,
    reference: str = "majority",
    n_boot: int = 2000,
    seed: int = 13,
) -> list[dict[str, Any]]:
    """κ, precision, recall, flagged (with bootstrap CIs) and paired Δκ vs the reference rule."""
    ids = [f for f in gold if all(f in lab for lab in labels.values())]
    if blind_only:
        ids = [f for f in ids if gold[f].blind]
    h = np.array([gold[f].sarcastic for f in ids], dtype=float)
    mats = {name: np.array([lab[f] for f in ids], dtype=float) for name, lab in labels.items()}
    rng = np.random.default_rng(seed)
    boots = [rng.integers(0, len(ids), len(ids)) for _ in range(n_boot)] if ids else []
    out = []
    for name, m in mats.items():
        k, p, r = _stats(h, m)
        ks, ps, rs, dk = [], [], [], []
        for idx in boots:
            bk, bp, br = _stats(h[idx], m[idx])
            rk = _stats(h[idx], mats[reference][idx])[0]
            ks.append(bk if bk is not None else math.nan)
            ps.append(bp if bp is not None else math.nan)
            rs.append(br if br is not None else math.nan)
            dk.append(bk - rk if bk is not None and rk is not None else math.nan)
        ref_k = _stats(h, mats[reference])[0]
        out.append(
            {
                "rule": name,
                "pre-named": name == PRE_NAMED,
                "gold posts": len(ids),
                "Gio sarcastic": int(h.sum()),
                "flagged": int(m.sum()),
                "kappa": k,
                "kappa CI": _ci(ks),
                "precision": p,
                "precision CI": _ci(ps),
                "recall": r,
                "recall CI": _ci(rs),
                "delta kappa vs majority": None if k is None or ref_k is None else k - ref_k,
                "delta CI": None if name == reference else _ci(dk),
            }
        )
    return out


def _fmt(x: float | None, ci: tuple[float, float] | None = None, signed: bool = False) -> str:
    if x is None:
        return "–"
    s = f"{x:+.2f}" if signed else f"{x:.2f}"
    if ci:
        lo, hi = ci
        s += f" [{lo:+.2f}, {hi:+.2f}]" if signed else f" [{lo:.2f}, {hi:.2f}]"
    return s


def report_markdown(
    rows_all: list[dict[str, Any]],
    rows_blind: list[dict[str, Any]],
    positives: dict[str, int],
    n_posts: int,
    ds: dict[str, Any],
) -> str:
    lines = [
        "| rule | flagged / Gio (all gold) | κ [95% CI] | precision | recall "
        "| Δκ vs majority [paired CI] | κ blind | positives on all posts |",
        "|---|---|---|---|---|---|---|---|",
    ]
    blind = {r["rule"]: r for r in rows_blind}
    for r in rows_all:
        b = blind.get(r["rule"], {})
        name = f"**{r['rule']}** (pre-named)" if r["pre-named"] else r["rule"]
        delta = (
            "reference"
            if r["rule"] == "majority"
            else _fmt(r["delta kappa vs majority"], r["delta CI"], True)
        )
        lines.append(
            f"| {name} | {r['flagged']} / {r['Gio sarcastic']} | {_fmt(r['kappa'], r['kappa CI'])} "
            f"| {_fmt(r['precision'], r['precision CI'])} | {_fmt(r['recall'], r['recall CI'])} "
            f"| {delta} "
            f"| {_fmt(b.get('kappa'), b.get('kappa CI'))} "
            f"| {positives[r['rule']]:,} ({100 * positives[r['rule']] / n_posts:.1f}%) |"
        )
    sens = ", ".join(
        f"{k} {ds['sensitivity'][k]:.2f}/{ds['specificity'][k]:.2f}" for k in KEYS
    )
    lines.append("")
    lines.append(
        f"Dawid–Skene (learned from all {n_posts:,} posts' votes, no gold): estimated prior "
        f"{ds['prior']:.3f}; sensitivity/specificity per annotator: {sens}."
    )
    return "\n".join(lines)
