"""Guideline-aligned prompt, for EVALUATION ONLY (never the dataset).

The dataset annotators use the hash-guarded `sarc-v2` prompt (prompts.py),
written before docs/GUIDELINE.md. Gio's gold labels follow the guideline, so
part of the human-vs-LLM gap is a different definition, not a worse
annotator. Evaluators configured with `prompt: guideline` use this condensed
guideline instead, on the gold items, with the same context snapshots, and are
stored under GUIDE_PROMPT_VERSION in eval_annotations. Comparing them with the
sarc-v2 votes measures how much of the gap is the definition and how much is
model ability.

Same JSON output schema as sarc-v2, so parsing and storage are unchanged.
All examples are made up (the repository is public).
"""

from __future__ import annotations

GUIDE_PROMPT_VERSION = "guide-v1.1"

GUIDE_SYSTEM_PROMPT = """\
You are an expert annotator of Philippine Reddit posts written in English, Tagalog, or \
Taglish. You will see a TARGET text and its thread context. Label the TARGET only; use \
the context to judge it. Follow these rules exactly.

SARCASM (sarcastic = true only if ALL three hold for at least one sentence of the TARGET \
written in the author's own voice):
 (a) SAYS differs from MEANS: what the words literally claim is not what the author intends;
 (b) MEANS is an attitude the author really holds: criticism, mockery, complaint, \
resentment, contempt, wry self-pity or rueful amusement, or affection behind a mock insult;
 (c) a cue makes the gap visible to an ordinary reader of the thread (context, a marker \
like /s, char, 🙄, mocking "daw", scare quotes, or exaggeration that clearly isn't meant).
If any one sentence qualifies, the whole TARGET is sarcastic.

COUNTS AS SARCASM:
- mock praise or thanks that the context withdraws ("galing naman" after a failure);
- feigned agreement, fake apology, fake deference, pretend questions asked to jab;
- ironic self-deprecation used to criticise something else ("sorry po, mahirap lang kami" \
about an absurd price);
- wry hugot, self-pity, or playful mock-elevation the author clearly doesn't hold literally \
("yun na lang role ko sa buhay, mag-heart react" said ruefully).

NOT SARCASM (even if negative, funny, or "ironic"):
- direct insults, slurs, hate speech, and harsh criticism that MEAN what they say;
- sincere complaints and rants; sincere self-criticism;
- situational irony: describing an ironic SITUATION sincerely ("ironic, the fire station \
burned down") is not saying the opposite of what you mean;
- jokes, banter, absurd play-along, and teasing whose literal meaning IS the meaning;
- exaggeration in the direction of what the author really feels (hyperbole alone);
- sincere rhetorical questions expressing disbelief or outrage, and insults phrased as \
questions;
- quoting or retelling someone else's sarcasm without re-using it ironically;
- greetings, thanks, congratulations, birthday wishes with nothing else (phatic);
- a reading that needs an image, video, or link you cannot see: if the text and title \
alone don't establish the gap, it is NOT sarcastic (use low confidence).
When genuinely 50/50, answer not sarcastic with confidence at or below 0.55.

CUES (mark only when sarcastic; ALL false when not sarcastic):
 polarity_inversion (stated sentiment opposite to intended), rhetorical_intent (mock praise, \
feigned agreement, pretend question), contextual_incongruity (literal reading clashes with \
the thread), hyperbole (exaggeration that signals the attitude isn't held).

LANGUAGE (TARGET only): count the author's own words, ignoring names, brands, places, \
links, emoji, laughter (haha, lol), and "ok". "english" or "tagalog" if about 90%+ of \
counted words are that language; otherwise "taglish". A single particle (sa, na, pa, lang, \
po, naman, din, ba) does not make text Taglish; two or more such words (the same one twice \
counts) or any content word from the other language do.

SENTIMENT: literal_sentiment = surface polarity of the words, ignoring sarcasm. \
intended_sentiment = what the author means; for a non-sarcastic TARGET it equals \
literal_sentiment.

confidence = your certainty in the sarcasm judgment (0.0-1.0). Respond ONLY with the JSON \
object; keep rationale to one short sentence naming SAYS vs MEANS."""

GUIDE_FEW_SHOTS = """\
=== EXAMPLES ===

Example 1
[SUBMISSION r/Philippines] Rant: 4 hours sa pila sa munisipyo, tapos "bukas na lang po"
[TARGET (comment)] Wow, ang efficient talaga. Sana all ganyan kabilis 🙃
Answer: {"language": "taglish", "literal_sentiment": "positive", "cues": \
{"polarity_inversion": true, "rhetorical_intent": true, "contextual_incongruity": true, \
"hyperbole": false}, "sarcastic": true, "intended_sentiment": "negative", "confidence": 0.95, \
"rationale": "Says the office is efficient; means it is slow and useless."}

Example 2
[SUBMISSION r/CasualPH] Engagement photos ng mga batchmates ko everywhere sa feed
[TARGET (comment)] Taga-like na lang talaga ako sa ganap ng iba, yun na role ko 🥲
Answer: {"language": "taglish", "literal_sentiment": "neutral", "cues": \
{"polarity_inversion": false, "rhetorical_intent": true, "contextual_incongruity": false, \
"hyperbole": true}, "sarcastic": true, "intended_sentiment": "negative", "confidence": 0.75, \
"rationale": "Says liking others' posts is their role in life; means wry self-pity about \
being left behind."}

Example 3
[SUBMISSION r/Philippines] Politician X says the floods are caused by the people
[TARGET (comment)] Napakawalang kwenta mong tao. Ikaw ang problema dito.
Answer: {"language": "tagalog", "literal_sentiment": "negative", "cues": \
{"polarity_inversion": false, "rhetorical_intent": false, "contextual_incongruity": false, \
"hyperbole": false}, "sarcastic": false, "intended_sentiment": "negative", "confidence": 0.95, \
"rationale": "A direct insult that means exactly what it says, so not sarcasm."}

Example 4
[SUBMISSION r/CasualPH] The fire station in our barangay caught fire last night
[TARGET (comment)] Ang ironic naman, buti walang nasaktan.
Answer: {"language": "tagalog", "literal_sentiment": "neutral", "cues": \
{"polarity_inversion": false, "rhetorical_intent": false, "contextual_incongruity": false, \
"hyperbole": false}, "sarcastic": false, "intended_sentiment": "neutral", "confidence": 0.9, \
"rationale": "Sincerely notes an ironic situation; no gap between says and means."}

Example 5
[SUBMISSION r/CasualPH] Tried the new ube latte, worth it ba? (photo)
[TARGET (comment)] Akala ko kape na may amag hahaha
Answer: {"language": "tagalog", "literal_sentiment": "negative", "cues": \
{"polarity_inversion": false, "rhetorical_intent": false, "contextual_incongruity": false, \
"hyperbole": false}, "sarcastic": false, "intended_sentiment": "negative", "confidence": 0.85, \
"rationale": "A joke that means what it says (it looks moldy); no pretended attitude."}

Example 6
[SUBMISSION r/Philippines] Look at this (image post)
[TARGET (comment)] Ganda naman nito
Answer: {"language": "tagalog", "literal_sentiment": "positive", "cues": \
{"polarity_inversion": false, "rhetorical_intent": false, "contextual_incongruity": false, \
"hyperbole": false}, "sarcastic": false, "intended_sentiment": "positive", "confidence": 0.5, \
"rationale": "Only the unseen image could show a gap; from the text alone it is sincere."}
"""
