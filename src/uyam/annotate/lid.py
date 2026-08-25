"""Two-stage fastText language identification (thesis LID design).

Stage 1: whole-message prediction with lid.176 for a confidence signal.
Stage 2: token-level en/tl classification; a message is english or tagalog
only when >= mono_threshold (default 0.9) of its alphabetic tokens are that
language, otherwise taglish. A Filipino function-word list backstops fastText,
which is unreliable on short, informal single tokens.

Uses fasttext-predict (prediction-only wheel that installs on Windows py3.11;
the official fasttext package needs an MSVC source build).
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from uyam.annotate.config import LidConfig
from uyam.annotate.db import AnnotationDatabase

logger = logging.getLogger(__name__)

LID_MODEL_URL = "https://dl.fbaipublicfiles.com/fasttext/supervised-models/lid.176.bin"

_TOKEN_RE = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿ'ñÑ]+")

# High-frequency Tagalog function words and particles that fastText routinely
# mislabels when seen as isolated tokens.
_TL_FUNCTION_WORDS = frozenset(
    "ang ng mga sa si sina ni nina kay kina ay at o pero kasi dahil para kung "  # noqa: SIM905
    "ako ikaw ka siya kami tayo kayo sila ko mo niya namin natin ninyo nila "
    "akin iyo kanya amin atin inyo kanila ito iyan iyon dito diyan doon "
    "hindi wag huwag oo opo po ho ba naman lang nga pala daw raw din rin "
    "na pa may mayroon meron wala sana talaga sobra grabe yung yun yan eh edi "
    "pano paano bakit saan kailan sino ano alin gusto ayaw pwede puwede "
    "masyado medyo halos lagi minsan ngayon kanina bukas kahapon mamaya".split()
)

# Words fastText's tl label often absorbs but are clearly English in Taglish text.
_EN_FUNCTION_WORDS = frozenset(
    "the a an and or but if of to in on at for with is are was were be been am "  # noqa: SIM905
    "i you he she it we they my your his her its our their this that these those "
    "not no yes do does did have has had will would can could should really".split()
)


def ensure_lid_model(model_path: Path) -> Path:
    """Download lid.176.bin (~130 MB) on first use."""
    if model_path.exists():
        return model_path
    import requests

    model_path.parent.mkdir(parents=True, exist_ok=True)
    logger.info("downloading_lid_model", extra={"url": LID_MODEL_URL})
    tmp_path = model_path.with_suffix(model_path.suffix + ".part")
    with requests.get(LID_MODEL_URL, stream=True, timeout=120) as resp:
        resp.raise_for_status()
        with tmp_path.open("wb") as fh:
            for chunk in resp.iter_content(chunk_size=1 << 20):
                fh.write(chunk)
    tmp_path.rename(model_path)
    return model_path


class LanguageIdentifier:
    def __init__(self, cfg: LidConfig) -> None:
        import fasttext  # fasttext-predict exposes the same module name

        self._model = fasttext.load_model(str(ensure_lid_model(cfg.model_path)))
        self._threshold = cfg.mono_threshold
        self._model_name = cfg.model_path.name

    def _predict_label(self, text: str, k: int = 1) -> list[tuple[str, float]]:
        labels, probs = self._model.predict(text.replace("\n", " "), k=k)
        return [
            (label.replace("__label__", ""), float(p))
            for label, p in zip(labels, probs, strict=False)
        ]

    def _token_language(self, token: str) -> str:
        low = token.lower()
        if low in _TL_FUNCTION_WORDS:
            return "tl"
        if low in _EN_FUNCTION_WORDS:
            return "en"
        preds = self._predict_label(low, k=5)
        for label, _prob in preds:
            if label in ("en", "tl"):
                return label
        return "other"

    def identify(self, text: str) -> dict[str, Any]:
        preds = self._predict_label(text, k=1)
        msg_label, msg_prob = preds[0] if preds else ("und", 0.0)

        tokens = _TOKEN_RE.findall(text)
        counts = {"en": 0, "tl": 0, "other": 0}
        for token in tokens:
            counts[self._token_language(token)] += 1
        n = max(1, len(tokens))
        en_ratio = counts["en"] / n
        tl_ratio = counts["tl"] / n
        other_ratio = counts["other"] / n

        if not tokens:
            language = {"en": "english", "tl": "tagalog"}.get(msg_label, "taglish")
        elif en_ratio >= self._threshold:
            language = "english"
        elif tl_ratio >= self._threshold:
            language = "tagalog"
        else:
            language = "taglish"

        return {
            "language": language,
            "en_ratio": round(en_ratio, 4),
            "tl_ratio": round(tl_ratio, 4),
            "other_ratio": round(other_ratio, 4),
            "confidence": round(msg_prob, 4),
            "lid_model": self._model_name,
        }


def run_lid(db: AnnotationDatabase, cfg: LidConfig, *, force: bool = False) -> dict[str, int]:
    """Language-identify every eligible candidate missing an LID result."""
    if force:
        db.conn.execute("DELETE FROM lid_results")
        db.commit()
    pending = db.missing_lid()
    if not pending:
        return {"processed": 0}
    identifier = LanguageIdentifier(cfg)
    processed = 0
    for fullname in pending:
        record = db.get_record(fullname)
        if record is None:
            continue
        db.upsert_lid(fullname, identifier.identify(str(record["text"])))
        processed += 1
        if processed % 500 == 0:
            db.commit()
    db.commit()
    return {"processed": processed}
