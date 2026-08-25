"""Pass 1: transformer literal-sentiment classifier over every candidate.

Uses cardiffnlp/twitter-xlm-roberta-base-sentiment (multilingual, pos/neu/neg)
as a model-independent sentiment vote alongside the LLM ensemble.

Run this BEFORE the local Ollama passes — transformers and Ollama cannot share
the 8 GB GPU (use --device cpu to run them concurrently anyway).
"""

from __future__ import annotations

import logging
from typing import Any

from uyam.annotate.config import TxSentimentConfig
from uyam.annotate.db import AnnotationDatabase

logger = logging.getLogger(__name__)

_MAX_LENGTH = 512


def _resolve_device(device: str) -> str:
    if device != "auto":
        return device
    import torch

    return "cuda" if torch.cuda.is_available() else "cpu"


def run_tx_sentiment(
    db: AnnotationDatabase, cfg: TxSentimentConfig, *, force: bool = False
) -> dict[str, Any]:
    """Classify literal sentiment for every eligible candidate missing a result."""
    if force:
        db.conn.execute("DELETE FROM tx_sentiment")
        db.commit()
    pending = db.missing_tx_sentiment()
    if not pending:
        return {"processed": 0, "device": None}

    import torch
    from transformers import AutoModelForSequenceClassification, AutoTokenizer

    device = _resolve_device(cfg.device)
    tokenizer = AutoTokenizer.from_pretrained(cfg.model)
    model = AutoModelForSequenceClassification.from_pretrained(cfg.model)
    model.to(device)
    model.eval()
    revision = getattr(model.config, "_commit_hash", None)

    # Map model label ids to our pos/neu/neg keys regardless of label casing/order.
    id2key = {}
    for idx, label in model.config.id2label.items():
        low = str(label).lower()
        if low.startswith("pos"):
            id2key[int(idx)] = "positive"
        elif low.startswith("neg"):
            id2key[int(idx)] = "negative"
        else:
            id2key[int(idx)] = "neutral"

    processed = 0
    for start in range(0, len(pending), cfg.batch_size):
        batch_ids = pending[start : start + cfg.batch_size]
        texts: list[str] = []
        kept_ids: list[str] = []
        for fullname in batch_ids:
            record = db.get_record(fullname)
            if record is None:
                continue
            texts.append(str(record["text"]))
            kept_ids.append(fullname)
        if not texts:
            continue

        inputs = tokenizer(
            texts, return_tensors="pt", padding=True, truncation=True, max_length=_MAX_LENGTH
        ).to(device)
        with torch.no_grad():
            logits = model(**inputs).logits
        probs = torch.softmax(logits, dim=-1).cpu()

        for fullname, prob_row in zip(kept_ids, probs, strict=True):
            scores = {"positive": 0.0, "neutral": 0.0, "negative": 0.0}
            for idx, key in id2key.items():
                scores[key] += float(prob_row[idx])
            label = max(scores, key=lambda k: scores[k])
            db.upsert_tx_sentiment(
                fullname,
                {
                    "label": label,
                    "p_pos": round(scores["positive"], 4),
                    "p_neu": round(scores["neutral"], 4),
                    "p_neg": round(scores["negative"], 4),
                    "model_name": cfg.model,
                    "model_revision": revision,
                    "device": device,
                },
            )
        processed += len(kept_ids)
        db.commit()
        if processed % 500 < cfg.batch_size:
            logger.info("tx_sentiment_progress", extra={"processed": processed})

    return {"processed": processed, "device": device}
