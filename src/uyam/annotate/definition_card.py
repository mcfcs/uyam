"""The sarcasm definition card shown on the labeling page (docs/GUIDELINE.md v1.1).

A one-screen summary for the annotator: what counts, what doesn't (hate
speech and irony included), the tricky distinctions, cues, language and
sentiment. The full rules, with the reasons behind them, stay in GUIDELINE.md;
the rule IDs in brackets point there. Examples are made up (the repository is
public).
"""

# ruff: noqa: E501  (markdown table rows are content, not code)
from __future__ import annotations

DEFINITION_CARD = """\
**Sarcastic = yes** when at least one sentence in the author's own voice has **all three**:

1. **Says ≠ Means:** the words claim something the author doesn't intend.
2. **Means is a real attitude:** criticism, mockery, complaint, resentment, or wry
   self-pity/amusement. "I'm just kidding" doesn't count.
3. **A cue shows the gap** to an ordinary reader: the thread context, `/s`, *char*, 🙄,
   mocking *daw*, scare quotes, or exaggeration that clearly isn't meant.

One such sentence is enough: tick it as the span (T2).

---

**✔ Counts as sarcasm**

| Kind | Example (made up) |
|---|---|
| Mock praise (H1) | [4-hour queue at the munisipyo] *"Wow, ang efficient talaga, sana all ganyan kabilis 🙃"* |
| Fake apology / feigned agreement (H3, H9) | [₱4,500 "luxury picnic"] *"Pasensya na po, mahirap lang kami, sa Luneta lang kami nagpi-picnic"* |
| Pretend question to jab (H5) | [absurd flood plan] *"Ilang dredger kaya kailangan diyan, boss?"* |
| Wry pose, **D1** (write D1 in notes) | *"Taga-heart react na lang ako sa engagement ng iba, yun na role ko 🥲"* |

**✘ Not sarcasm.** It may be harsh, funny or ironic, but Says = Means.

| Kind | Example (made up) | Why |
|---|---|---|
| Insults, **hate speech**, slurs (H10) | *"Napakawalang kwenta mong tao."* | contempt said outright |
| Sincere complaint / rant (H10) | *"Ang bagal ng LRT ngayon, nakakainis."* | means it |
| **Situational irony** (H14) | [fire station caught fire] *"Ang ironic naman, buti walang nasaktan."* | describes an ironic situation sincerely |
| Joke that means what it says (H4) | *"Akala ko kape na may amag hahaha"* | mocking but literal |
| Hyperbole alone (H2) | *"Parang nasa oven ako sa sobrang init."* | exaggerates a real feeling |
| Sincere rhetorical question (H5) | *"Paano nangyari 'to?!"* | genuine outrage |
| Quoting someone else's sarcasm (T3) | *"Sabi niya, 'wow, ang aga mo ah'"* | not the author's own |
| Greetings / thanks / HBD only (H11) | *"Happy birthday OP! 🎂"* | phatic |
| Needs the unseen image (H13) | [image post] *"Ganda naman nito"* | no gap in the text → best reading + **Unsure** |

**Tricky pairs**
- **Sarcasm vs insult:** an insult says the criticism; sarcasm hides it behind the opposite.
- **Sarcasm vs irony:** sarcasm is *the author* pretending an attitude; an ironic *situation*
  told straight is not sarcasm.
- **Sarcasm vs joke:** a joke can mock and still be literal; sarcasm needs Says ≠ Means.
- **50/50?** Label **no** and tick Unsure (§6.1).

---

**Cues** (only when sarcastic): *polarity inversion* (opposite sentiment) · *rhetorical intent*
(mock praise, feigned agreement, pretend question) · *contextual incongruity* (clashes with the
thread) · *hyperbole* (exaggeration that isn't meant).

**Language** (target text only): count the author's own words, ignoring names, links, emoji,
*haha*, *lol* and *ok*. If about 90% are one language, it's *english* or *tagalog*; otherwise
*taglish*. One particle (*sa, na, lang, po, naman, ba*) doesn't make it Taglish; two (the same
one twice counts) or any content word does (L1–L4b).

**Sentiment:** *literal* = the surface tone of the words. *Intended* = what the author means.
When not sarcastic, they're the same (S1–S3).
"""
