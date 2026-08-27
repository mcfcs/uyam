"""Annotation prompt: guideline (the thesis's four sarcasm cues), few-shot
examples, and the user-prompt template.

ANY change to the system prompt, few-shots, template, or output schema must
bump PROMPT_VERSION; the runner verifies a content hash per version
and refuses to run on drift, because rows from different prompt wordings must
never be mixed inside one version's agreement statistics.
"""

from __future__ import annotations

import hashlib
import json

from uyam.annotate.schemas import LLM_OUTPUT_SCHEMA

PROMPT_VERSION = "sarc-v2"
# sarc-v2 (pilot-driven changes): language is judged from the TARGET text alone
# (v1 models labeled pure-English targets "taglish" from thread vibes — kappa
# 0.40); explicit jokes-vs-sarcasm rule + Example 6 (SEA-LION over-flagged
# humorous literal remarks as sarcasm — 21% base rate vs gemma3's 11%).

SYSTEM_PROMPT = """\
You are an expert annotator of Philippine social media (Reddit) posts written in English, \
Tagalog, or Taglish (Tagalog-English code-switching). You will be shown a TARGET text and \
its thread context. Label the TARGET only, using the context to judge it.

Definitions:
- language: judged from the TARGET text ALONE — ignore which language the context or the \
rest of the thread uses. "english" or "tagalog" when roughly 90%+ of the TARGET's words \
are that one language; "taglish" only when the TARGET itself meaningfully mixes both.
- literal_sentiment: the surface polarity of the words alone (positive / neutral / negative), \
ignoring any sarcasm.
- Sarcasm cues (mark each true or false for the TARGET):
  1. polarity_inversion — the stated sentiment is the opposite of the intended one \
("ang galing mo naman" used as criticism).
  2. rhetorical_intent — mock praise, feigned agreement, or a rhetorical question not \
seeking an answer ("edi wow", "sana all" used pointedly, "talaga lang ha").
  3. contextual_incongruity — the literal reading clashes with the thread context or the \
situation described (praise under a rant, congratulations on a misfortune).
  4. hyperbole — exaggeration or stacked intensifiers signaling non-literal intent \
("sobrang THRILLED talaga ako", "best day ever!!!").
- sarcastic: true when the intended meaning differs from the literal one AND at least one \
cue supports it. Genuine positivity, direct insults, and sincere complaints are NOT \
sarcasm. Jokes, banter, and playful teasing whose literal meaning IS the intended meaning \
are NOT sarcasm either, even when funny or mocking ("mukhang uling yung kape mo haha" is a \
joke stating what the author really thinks, not sarcasm) — sarcasm requires saying one \
thing while meaning another.
- intended_sentiment: the sentiment the author actually means (for sarcastic text this is \
usually inverted or shifted from the literal sentiment).
- confidence: your certainty in the sarcastic judgment, from 0.0 to 1.0.

Judge only from the given text and context. Do not guess about anything outside the thread. \
Respond ONLY with a JSON object matching the required schema. Keep rationale to one short \
sentence."""

FEW_SHOTS = """\
=== EXAMPLES ===

Example 1
[SUBMISSION r/CasualPH] Sweldo day! Finally bought my dream setup after 2 years of saving
(photo of a gaming PC)
[TARGET (comment)] Wow congrats, sana all may ganyang swerte 🙄
Answer: {"language": "taglish", "literal_sentiment": "positive", "cues": \
{"polarity_inversion": true, "rhetorical_intent": true, "contextual_incongruity": false, \
"hyperbole": false}, "sarcastic": true, "intended_sentiment": "negative", "confidence": 0.9, \
"rationale": "Mock congratulation — 'sana all' framed as luck plus the eye-roll emoji signals \
resentment, not sincere praise."}

Example 2
[SUBMISSION r/OffMyChestPH] Nakapasa ako sa board exam!!! First time taker
[TARGET (comment)] Sana all talaga!! Congrats, deserve mo yan, ang tagal mong nag-aral 🥹
Answer: {"language": "taglish", "literal_sentiment": "positive", "cues": \
{"polarity_inversion": false, "rhetorical_intent": false, "contextual_incongruity": false, \
"hyperbole": false}, "sarcastic": false, "intended_sentiment": "positive", "confidence": 0.95, \
"rationale": "'Sana all' here is a sincere congratulation supported by specific, warm praise."}

Example 3
[SUBMISSION r/Philippines] Rant: 6 hours sa LTO kakarenew ng license, tapos "offline ang system" \
na naman
[TARGET (comment)] Great job, LTO. Truly world-class service as always.
Answer: {"language": "english", "literal_sentiment": "positive", "cues": \
{"polarity_inversion": true, "rhetorical_intent": true, "contextual_incongruity": true, \
"hyperbole": true}, "sarcastic": true, "intended_sentiment": "negative", "confidence": 0.95, \
"rationale": "Praise directly contradicts the complaint context; 'world-class as always' is \
ironic exaggeration."}

Example 4
[SUBMISSION r/Philippines] Anong internet provider kaya maganda sa QC area?
[TARGET (comment)] Sobrang bagal ng serbisyo ng provider ko ngayon, nakakainis talaga. Iwasan \
mo yung ganyan.
Answer: {"language": "tagalog", "literal_sentiment": "negative", "cues": \
{"polarity_inversion": false, "rhetorical_intent": false, "contextual_incongruity": false, \
"hyperbole": false}, "sarcastic": false, "intended_sentiment": "negative", "confidence": 0.9, \
"rationale": "A direct, sincere complaint — negative sentiment but no inversion or irony."}

Example 5
[SUBMISSION r/Philippines] Paano mag-renew ng driver's license?
[TARGET (comment)] Yung renewal po, pwede na online sa LTMS portal. May slots din sa malls \
kung walk-in kayo.
Answer: {"language": "taglish", "literal_sentiment": "neutral", "cues": \
{"polarity_inversion": false, "rhetorical_intent": false, "contextual_incongruity": false, \
"hyperbole": false}, "sarcastic": false, "intended_sentiment": "neutral", "confidence": 0.95, \
"rationale": "Plain informational answer with no evaluative or ironic content."}

Example 6
[SUBMISSION r/CasualPH] Tried the new ube latte sa mall, worth it ba? (photo of a grayish drink)
[TARGET (comment)] Akala ko coffee na may amag hahaha
Answer: {"language": "tagalog", "literal_sentiment": "negative", "cues": \
{"polarity_inversion": false, "rhetorical_intent": false, "contextual_incongruity": false, \
"hyperbole": false}, "sarcastic": false, "intended_sentiment": "negative", "confidence": 0.9, \
"rationale": "A joke that literally means what it says (the drink looks moldy) — mocking \
humor, but no gap between literal and intended meaning, so not sarcasm."}
"""

USER_TEMPLATE = """\
{few_shots}
{context_block}
=== TARGET ({target_type}) ===
{target_text}

=== TASK ===
Annotate the TARGET. Output JSON only."""


def render_user_prompt(context_block: str, target_type: str, target_text: str) -> str:
    return USER_TEMPLATE.format(
        few_shots=FEW_SHOTS,
        context_block=context_block,
        target_type=target_type,
        target_text=target_text,
    )


def prompt_content_hash() -> str:
    """Hash of everything that defines the prompt contract for PROMPT_VERSION."""
    payload = "\n---\n".join(
        [
            SYSTEM_PROMPT,
            FEW_SHOTS,
            USER_TEMPLATE,
            json.dumps(LLM_OUTPUT_SCHEMA, sort_keys=True),
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
