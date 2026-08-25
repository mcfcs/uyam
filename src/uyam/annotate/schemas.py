"""Pydantic schemas for LLM annotation output and the structured-output JSON schema.

LLM_OUTPUT_SCHEMA is hand-written (not generated from the Pydantic model) so it
stays flat — no $defs/$ref, which some Ollama grammar builders mishandle — and
so property order is exactly the annotation reasoning order:

    language -> literal_sentiment -> cues -> sarcastic -> intended_sentiment
    -> confidence -> rationale

The cue analysis is emitted BEFORE the sarcasm verdict so the verdict is
conditioned on it (a compact, auditable chain of thought).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

Language = Literal["english", "tagalog", "taglish"]
Sentiment = Literal["positive", "neutral", "negative"]

RATIONALE_MAX_CHARS = 400


class CueFlags(BaseModel):
    polarity_inversion: bool
    rhetorical_intent: bool
    contextual_incongruity: bool
    hyperbole: bool


class LLMAnnotationOut(BaseModel):
    """The exact JSON object an annotator model must emit."""

    language: Language
    literal_sentiment: Sentiment
    cues: CueFlags
    sarcastic: bool
    intended_sentiment: Sentiment
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str = ""

    @field_validator("rationale", mode="before")
    @classmethod
    def _truncate_rationale(cls, v: object) -> str:
        # Grammar-constrained decoding cannot enforce maxLength; truncate rather
        # than fail validation when a model rambles.
        if not isinstance(v, str):
            return ""
        return v[:RATIONALE_MAX_CHARS]


LLM_OUTPUT_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "language": {"type": "string", "enum": ["english", "tagalog", "taglish"]},
        "literal_sentiment": {"type": "string", "enum": ["positive", "neutral", "negative"]},
        "cues": {
            "type": "object",
            "properties": {
                "polarity_inversion": {"type": "boolean"},
                "rhetorical_intent": {"type": "boolean"},
                "contextual_incongruity": {"type": "boolean"},
                "hyperbole": {"type": "boolean"},
            },
            "required": [
                "polarity_inversion",
                "rhetorical_intent",
                "contextual_incongruity",
                "hyperbole",
            ],
        },
        "sarcastic": {"type": "boolean"},
        "intended_sentiment": {"type": "string", "enum": ["positive", "neutral", "negative"]},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "rationale": {"type": "string", "maxLength": 300},
    },
    "required": [
        "language",
        "literal_sentiment",
        "cues",
        "sarcastic",
        "intended_sentiment",
        "confidence",
        "rationale",
    ],
}
