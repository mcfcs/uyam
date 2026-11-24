"""Two-stage fastText language identification (thesis LID design).

Stage 1: whole-message prediction with lid.176 for a confidence signal.
Stage 2: token-level en/tl classification; a message is english or tagalog
only when >= mono_threshold (default 0.9) of its alphabetic tokens are that
language, otherwise taglish. A Filipino function-word list backstops fastText,
which is unreliable on short, informal single tokens.

`language_rule` (2026-11-24) applies docs/GUIDELINE.md L2-L4b to the same
token tags: laughter and internet tokens are not counted, a lone function
word from the other language keeps the main language (L4), repeats count
(L4b), and otherwise the 90% rule applies (L3). The LLM language votes
under the guideline prompt called most English targets Taglish (v4 pilot:
kappa 0.08 vs the gold); this rule scored kappa 0.71 on the same items. The
plain `language` field is kept unchanged for v1-v3 comparability.

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

RULE_VERSION = "L-rules-v1"

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


# L2: laughter and internet tokens count as neither language.
_NOT_COUNTED_RE = re.compile(
    r"^(h+a+[ha]*|h+e+[he]*|lo+l|lmao+|lmfao|rofl|op|char|charot|ok|okay|okey|xd|omg)$",
    re.IGNORECASE,
)


def rule_language(tagged: list[tuple[str, str]]) -> str | None:
    """GUIDELINE L2-L4b on (token, "en" | "tl" | "other") tags; None if nothing counts.

    Tokens tagged "other" (names, noise) and L2 tokens are not counted. The
    main language is the one with more counted words. A single minority word
    that is a function word or particle keeps the main language (L4);
    otherwise the target is Taglish once the minority reaches 10% (L3), with
    repeats counted (L4b).
    """
    counted = [
        (tok.lower(), tag)
        for tok, tag in tagged
        if tag in ("en", "tl") and not _NOT_COUNTED_RE.match(tok)
    ]
    if not counted:
        return None
    n_en = sum(1 for _, tag in counted if tag == "en")
    main = "en" if n_en >= len(counted) - n_en else "tl"
    minority = [tok for tok, tag in counted if tag != main]
    name = "english" if main == "en" else "tagalog"
    function_words = _TL_FUNCTION_WORDS if main == "en" else _EN_FUNCTION_WORDS
    if not minority or (len(minority) == 1 and minority[0] in function_words):
        return name
    return name if len(minority) / len(counted) < 0.10 else "taglish"


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

    def tag_tokens(self, text: str) -> list[tuple[str, str]]:
        return [(token, self._token_language(token)) for token in _TOKEN_RE.findall(text)]

    def identify(self, text: str) -> dict[str, Any]:
        preds = self._predict_label(text, k=1)
        msg_label, msg_prob = preds[0] if preds else ("und", 0.0)

        tagged = self.tag_tokens(text)
        tokens = [token for token, _ in tagged]
        counts = {"en": 0, "tl": 0, "other": 0}
        for _, tag in tagged:
            counts[tag] += 1
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
            "language_rule": rule_language(tagged) or language,
            "rule_version": RULE_VERSION,
            "en_ratio": round(en_ratio, 4),
            "tl_ratio": round(tl_ratio, 4),
            "other_ratio": round(other_ratio, 4),
            "confidence": round(msg_prob, 4),
            "lid_model": self._model_name,
        }


def run_lid(
    db: AnnotationDatabase,
    cfg: LidConfig,
    *,
    force: bool = False,
    target: int | None = None,
    prompt_version: str | None = None,
    refresh_rule: bool = False,
) -> dict[str, int]:
    """Language-identify every eligible candidate missing an LID result.

    With `target`, only the pipeline's N-item target set is covered. With
    `refresh_rule`, only `language_rule` is (re)computed for items that
    already have a result; `language` is never changed.
    """
    if force:
        db.conn.execute("DELETE FROM lid_results")
        db.commit()
    if refresh_rule:
        return refresh_language_rule(db, cfg, target=target, prompt_version=prompt_version)
    if target is not None:
        if not prompt_version:
            raise ValueError("prompt_version is required when target is set")
        pending = db.missing_lid_in_target(prompt_version, target)
    else:
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


def refresh_language_rule(
    db: AnnotationDatabase,
    cfg: LidConfig,
    *,
    target: int | None = None,
    prompt_version: str | None = None,
) -> dict[str, int]:
    """(Re)compute `language_rule` for items with an LID result (the target set if given)."""
    if target is not None:
        if not prompt_version:
            raise ValueError("prompt_version is required when target is set")
        in_scope = set(db.target_items(prompt_version, target))
        names = [
            str(r[0])
            for r in db.conn.execute("SELECT reddit_fullname FROM lid_results ORDER BY 1")
            if str(r[0]) in in_scope
        ]
    else:
        names = [
            str(r[0])
            for r in db.conn.execute("SELECT reddit_fullname FROM lid_results ORDER BY 1")
        ]
    identifier = LanguageIdentifier(cfg)
    processed = 0
    for fullname in names:
        record = db.get_record(fullname)
        if record is None:
            continue
        lid = db.conn.execute(
            "SELECT language FROM lid_results WHERE reddit_fullname = ?", (fullname,)
        ).fetchone()
        tagged = identifier.tag_tokens(str(record["text"]))
        db.set_language_rule(fullname, rule_language(tagged) or str(lid[0]), RULE_VERSION)
        processed += 1
        if processed % 500 == 0:
            db.commit()
    db.commit()
    return {"processed": processed}
