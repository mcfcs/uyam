"""Vote aggregation, escalation, and agreement statistics.

Rules (see docs/dataset-contract-leische.md):
- sarcasm: unanimous across annotators -> final; any split (incl. 2-1) -> escalate
- 3-class labels (language / literal / intended): >=2 votes -> majority; else escalate
- adjudicator: blind senior annotator — its labels REPLACE all labels on
  escalated items; confidence below the floor sends the item to the human queue
- a human review (non-gold) is always final

Agreement stats: Fleiss' kappa over the base annotators only (the adjudicator
has a different marginal distribution and would bias it), Krippendorff's alpha
as a missing-tolerant robustness check, Cohen's kappa human-vs-ensemble on the
gold subset, and fastText-vs-ensemble language agreement.
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from typing import Any

from uyam.annotate.config import AnnotationConfig
from uyam.annotate.db import AnnotationDatabase

logger = logging.getLogger(__name__)

_CUE_COLS = (
    "cue_polarity_inversion",
    "cue_rhetorical_intent",
    "cue_contextual_incongruity",
    "cue_hyperbole",
)
_LABEL_CODES: dict[str, dict[str, int]] = {
    "sarcastic": {"0": 0, "1": 1},
    "language": {"english": 0, "tagalog": 1, "taglish": 2},
    "literal_sentiment": {"positive": 0, "neutral": 1, "negative": 2},
    "intended_sentiment": {"positive": 0, "neutral": 1, "negative": 2},
}


def _majority_3class(values: list[str]) -> tuple[str | None, bool]:
    """Return (majority value | None, escalate)."""
    if not values:
        return None, True
    top_value, top_count = Counter(values).most_common(1)[0]
    if top_count >= 2:
        return top_value, False
    return None, True


def _sarcasm_vote(values: list[int]) -> tuple[int | None, bool, str]:
    """Return (final | None, escalate, votes_string). Unanimity required."""
    if not values:
        return None, True, "0-0"
    yes = sum(values)
    no = len(values) - yes
    votes = f"{max(yes, no)}-{min(yes, no)}"
    if len(values) >= 2 and (yes == 0 or no == 0):
        return values[0], False, votes
    return None, True, votes


def per_label_resolution(
    annotator_rows: list[dict[str, Any]],
    adjudicator_rows: list[dict[str, Any]],
    human: dict[str, Any] | None,
) -> dict[str, str]:
    """How EACH label was decided: unanimous | majority | adjudicator | human | unresolved.

    `aggregates.resolved_by` is the joint resolution of all four labels; the
    model repository needs the per-label view because sarcasm often resolves
    while language does not (and vice versa).
    """
    if human is not None and not human.get("is_gold"):
        return dict.fromkeys(
            ("sarcastic", "language", "literal_sentiment", "intended_sentiment"), "human"
        )
    if adjudicator_rows:
        return dict.fromkeys(
            ("sarcastic", "language", "literal_sentiment", "intended_sentiment"), "adjudicator"
        )
    out: dict[str, str] = {}
    sarc_values = [int(r["sarcastic"]) for r in annotator_rows if r.get("sarcastic") is not None]
    _, escalate, _ = _sarcasm_vote(sarc_values)
    out["sarcastic"] = "unresolved" if escalate else "unanimous"
    for label in ("language", "literal_sentiment", "intended_sentiment"):
        values = [str(r[label]) for r in annotator_rows if r.get(label) is not None]
        final, escalate = _majority_3class(values)
        if escalate:
            out[label] = "unresolved"
        elif len(set(values)) == 1:
            out[label] = "unanimous"
        else:
            out[label] = "majority"
    return out


def _cue_majority(rows: list[dict[str, Any]], col: str) -> int:
    votes = [int(r[col]) for r in rows if r.get(col) is not None]
    if not votes:
        return 0
    return int(sum(votes) > len(votes) / 2)


def run_aggregation(cfg: AnnotationConfig, prompt_version: str) -> dict[str, int]:
    """Recompute the aggregates table for every item with annotations. Idempotent."""
    stats = {
        "items": 0,
        "unanimous": 0,
        "majority": 0,
        "escalated_pending": 0,
        "adjudicated": 0,
        "human": 0,
        "queued_low_confidence": 0,
    }
    with AnnotationDatabase(cfg.db_path) as db:
        floor = cfg.review.adjudicator_confidence_floor
        for fullname in db.annotated_items(prompt_version):
            rows = db.annotations_for_item(fullname, prompt_version)
            annotator_rows = [r for r in rows if r["role"] == "annotator"]
            adjudicator_rows = [r for r in rows if r["role"] == "adjudicator"]
            if not annotator_rows:
                continue
            stats["items"] += 1

            sarcasm_final, sarcasm_escalate, votes = _sarcasm_vote(
                [int(r["sarcastic"]) for r in annotator_rows]
            )
            language_final, lang_escalate = _majority_3class(
                [str(r["language"]) for r in annotator_rows]
            )
            literal_final, lit_escalate = _majority_3class(
                [str(r["literal_sentiment"]) for r in annotator_rows]
            )
            intended_final, int_escalate = _majority_3class(
                [str(r["intended_sentiment"]) for r in annotator_rows]
            )
            escalate = sarcasm_escalate or lang_escalate or lit_escalate or int_escalate
            cues_final = {col: _cue_majority(annotator_rows, col) for col in _CUE_COLS}

            all_unanimous = not escalate and all(
                len({str(r[col]) for r in annotator_rows}) == 1
                for col in ("sarcastic", "language", "literal_sentiment", "intended_sentiment")
            )

            resolved_by: str | None = None
            needs_human = 0
            mean_confidence: float | None = None

            if not escalate:
                resolved_by = "unanimous" if all_unanimous else "majority"
                voting = [
                    float(r["confidence"])
                    for r in annotator_rows
                    if r.get("confidence") is not None and int(r["sarcastic"]) == sarcasm_final
                ]
                mean_confidence = round(sum(voting) / len(voting), 4) if voting else None
                stats[resolved_by] += 1
            elif adjudicator_rows:
                adj = adjudicator_rows[0]
                sarcasm_final = int(adj["sarcastic"])
                language_final = str(adj["language"])
                literal_final = str(adj["literal_sentiment"])
                intended_final = str(adj["intended_sentiment"])
                cues_final = {col: int(adj[col] or 0) for col in _CUE_COLS}
                resolved_by = "adjudicator"
                adj_conf = adj.get("confidence")
                mean_confidence = None if adj_conf is None else round(float(adj_conf), 4)
                if mean_confidence is None or mean_confidence < floor:
                    needs_human = 1
                stats["adjudicated"] += 1
            else:
                stats["escalated_pending"] += 1

            human = db.get_human_review(fullname)
            if human is not None and not human.get("is_gold"):
                sarcasm_final = human["sarcastic"]
                language_final = human["language"]
                literal_final = human["literal_sentiment"]
                intended_final = human["intended_sentiment"]
                cues_final = {col: int(human.get(col) or 0) for col in _CUE_COLS}
                resolved_by = "human"
                needs_human = 0
                stats["human"] += 1

            votes_json = {
                str(r["model_key"]): {
                    "sarcastic": bool(r["sarcastic"]),
                    "language": r["language"],
                    "literal_sentiment": r["literal_sentiment"],
                    "intended_sentiment": r["intended_sentiment"],
                    "confidence": r["confidence"],
                }
                for r in rows
            }

            db.upsert_aggregate(
                {
                    "reddit_fullname": fullname,
                    "prompt_version": prompt_version,
                    "n_annotators": len(annotator_rows),
                    "sarcasm_votes": votes,
                    "votes_json": json.dumps(votes_json, ensure_ascii=False),
                    "sarcastic_final": sarcasm_final,
                    "language_final": language_final,
                    "literal_final": literal_final,
                    "intended_final": intended_final,
                    "cue_polarity_inversion_final": cues_final["cue_polarity_inversion"],
                    "cue_rhetorical_intent_final": cues_final["cue_rhetorical_intent"],
                    "cue_contextual_incongruity_final": cues_final["cue_contextual_incongruity"],
                    "cue_hyperbole_final": cues_final["cue_hyperbole"],
                    "needs_adjudication": int(escalate),
                    "needs_human": needs_human,
                    "resolved_by": resolved_by,
                    "mean_confidence": mean_confidence,
                }
            )
            if needs_human:
                db.enqueue_review(fullname, "low_confidence")
                stats["queued_low_confidence"] += 1
        db.commit()
    return stats


# ---------------------------------------------------------------------------
# Agreement statistics
# ---------------------------------------------------------------------------

def _label_matrix(
    db: AnnotationDatabase, prompt_version: str, label_col: str
) -> tuple[list[str], list[list[str | None]]]:
    """(model_keys, items x raters label matrix with None for missing)."""
    rows = db.conn.execute(
        """
        SELECT reddit_fullname, model_key, language, literal_sentiment,
               intended_sentiment, sarcastic
        FROM llm_annotations WHERE prompt_version = ? AND role = 'annotator'
        """,
        (prompt_version,),
    ).fetchall()
    model_keys = sorted({str(r["model_key"]) for r in rows})
    by_item: dict[str, dict[str, str]] = {}
    for r in rows:
        value = r[label_col]
        if value is None:
            continue
        by_item.setdefault(str(r["reddit_fullname"]), {})[str(r["model_key"])] = str(value)
    matrix = [
        [labels.get(key) for key in model_keys]
        for labels in by_item.values()
    ]
    return model_keys, matrix


def _fleiss_kappa(matrix: list[list[str | None]], codes: dict[str, int]) -> float | None:
    complete = [row for row in matrix if all(v is not None for v in row)]
    if len(complete) < 2:
        return None
    try:
        import numpy as np
        from statsmodels.stats.inter_rater import aggregate_raters, fleiss_kappa

        data = np.array([[codes[str(v)] for v in row] for row in complete])
        table, _ = aggregate_raters(data, n_cat=len(codes))
        value = float(fleiss_kappa(table, method="fleiss"))
        return None if value != value else round(value, 4)  # NaN guard
    except Exception as exc:  # noqa: BLE001 - stats are best-effort reporting
        logger.warning("fleiss_kappa_failed", extra={"error": str(exc)})
        return None


def _krippendorff_alpha(matrix: list[list[str | None]], codes: dict[str, int]) -> float | None:
    if len(matrix) < 2:
        return None
    try:
        import krippendorff
        import numpy as np

        data = np.array(
            [
                [np.nan if v is None else float(codes[str(v)]) for v in row]
                for row in matrix
            ]
        ).T  # krippendorff expects raters x units
        value = float(
            krippendorff.alpha(reliability_data=data, level_of_measurement="nominal")
        )
        return None if value != value else round(value, 4)
    except Exception as exc:  # noqa: BLE001
        logger.warning("krippendorff_failed", extra={"error": str(exc)})
        return None


def _gold_cohen_kappa(db: AnnotationDatabase) -> dict[str, Any]:
    """Cohen's kappa: human gold labels vs the ensemble finals, per label."""
    pairs: dict[str, tuple[list[str], list[str]]] = {
        "sarcastic": ([], []),
        "language": ([], []),
        "literal_sentiment": ([], []),
        "intended_sentiment": ([], []),
    }
    agg_col = {
        "sarcastic": "sarcastic_final",
        "language": "language_final",
        "literal_sentiment": "literal_final",
        "intended_sentiment": "intended_final",
    }
    n = 0
    for human in db.all_human_reviews():
        if not human.get("is_gold"):
            continue
        agg = db.get_aggregate(str(human["reddit_fullname"]))
        if agg is None:
            continue
        n += 1
        for label, (h_list, e_list) in pairs.items():
            h_val, e_val = human.get(label), agg.get(agg_col[label])
            if h_val is not None and e_val is not None:
                h_list.append(str(h_val))
                e_list.append(str(e_val))

    result: dict[str, Any] = {"n_gold_items": n}
    try:
        from sklearn.metrics import cohen_kappa_score

        for label, (h_list, e_list) in pairs.items():
            result[label] = (
                round(float(cohen_kappa_score(h_list, e_list)), 4) if len(h_list) >= 2 else None
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning("cohen_kappa_failed", extra={"error": str(exc)})
        for label in pairs:
            result[label] = None
    return result


def _lid_agreement(db: AnnotationDatabase) -> dict[str, Any]:
    rows = db.conn.execute(
        """
        SELECT g.language_final AS ensemble, l.language AS lid
        FROM aggregates g JOIN lid_results l ON l.reddit_fullname = g.reddit_fullname
        WHERE g.language_final IS NOT NULL
        """
    ).fetchall()
    if not rows:
        return {"n": 0, "agreement": None}
    agree = sum(1 for r in rows if str(r["ensemble"]) == str(r["lid"]))
    return {"n": len(rows), "agreement": round(agree / len(rows), 4)}


def _lid_vs_human_gold(db: AnnotationDatabase) -> dict[str, Any]:
    """Thesis §3.2.1: accuracy of the automatic LID step against the manual labels."""
    rows = db.conn.execute(
        """
        SELECT h.language AS human, l.language AS lid
        FROM human_reviews h JOIN lid_results l ON l.reddit_fullname = h.reddit_fullname
        WHERE h.is_gold = 1 AND h.language IS NOT NULL
        """
    ).fetchall()
    if not rows:
        return {"n": 0, "accuracy": None}
    agree = sum(1 for r in rows if str(r["human"]) == str(r["lid"]))
    return {"n": len(rows), "accuracy": round(agree / len(rows), 4)}


def compute_agreement(cfg: AnnotationConfig, prompt_version: str) -> dict[str, Any]:
    """Full agreement report — printed by `annotate aggregate --report` and
    embedded in the dataset card at export."""
    with AnnotationDatabase(cfg.db_path) as db:
        report: dict[str, Any] = {"prompt_version": prompt_version, "labels": {}}
        col_map = {
            "sarcastic": "sarcastic",
            "language": "language",
            "literal_sentiment": "literal_sentiment",
            "intended_sentiment": "intended_sentiment",
        }
        for label, col in col_map.items():
            model_keys, matrix = _label_matrix(db, prompt_version, col)
            codes = _LABEL_CODES[label]
            complete = sum(1 for row in matrix if all(v is not None for v in row))
            report["labels"][label] = {
                "raters": model_keys,
                "n_items": len(matrix),
                "n_items_all_raters": complete,
                "fleiss_kappa": _fleiss_kappa(matrix, codes),
                "krippendorff_alpha": _krippendorff_alpha(matrix, codes),
            }
        report["gold_vs_ensemble_cohen_kappa"] = _gold_cohen_kappa(db)
        report["lid_vs_ensemble_language"] = _lid_agreement(db)
        report["lid_vs_human_gold_language"] = _lid_vs_human_gold(db)
        return report
