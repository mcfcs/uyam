# Sarcasm annotation guideline (human gold labels)

**Status: draft v1.1, 2026-11-22.** D1 (broad reading) and D4 are decided;
D2, D3 and D5 are still open (§7). Nothing here changes the v1 dataset labels.
Version string for `assist_suggestions.guideline_version`: `guideline-v1.1`.

All examples in this file are **made up** or heavily paraphrased. No Reddit
text is quoted (this repository is public). The real gold items these rules
were checked against are listed in `data/guideline_examples.md`, which is
private and gitignored.

---

## 1. Purpose and status

This guideline governs:

- **the human gold labels** (`human_reviews` rows with `is_gold = 1`), which
  are used only for evaluation (RULES §5.5), and
- **reviewer-assist suggestions** (`assist_suggestions`), which a Claude agent
  pre-computes for the review UI. The agent must apply exactly these rules
  (§6.5).

It does **not** change the v1 dataset. The 15,000 v1 labels were produced by
three annotator LLMs and a blind adjudicator, all using the `sarc-v2` prompt in
`src/uyam/annotate/prompts.py`. That prompt is where this guideline starts:
the same definition of sarcasm, the same four cues, the same jokes rule, and
the same language and sentiment labels. Changing the prompt would mean a new
`PROMPT_VERSION` and re-annotation (RULES §5.4). This document doesn't do that.

Why a written guideline is needed:

- `sarc-v2` is silent on several things a human hits at once: a long target
  that is only partly sarcastic, self-deprecation, quoted sarcasm, phatic
  items (birthday greetings, thanks), and items that depend on an image.
- On the first 31 gold items, the human label and the shipped label agree on
  22 of 31 (Cohen's κ −0.15). Against the annotators' majority vote, κ is
  +0.45. Almost all of the 9 disagreements are about playful humor,
  hyperbole, and self-deprecation, which is exactly where `sarc-v2` says
  least.
- Those 31 labels were made **before** this guideline existed, from the
  `sarc-v2` definitions alone.

### 1.1 Where this guideline differs from `sarc-v2`

These differences are a **documented limitation of the v1 labels**, not
something to hide. Human-vs-ensemble agreement on the gold subset measures
two things at once: how well the ensemble labeled, and how far its
definition sits from this one.

| # | Topic | `sarc-v2` prompt | This guideline | Direction |
|---|---|---|---|---|
| 1 | Partial sarcasm in a long target | silent | sarcastic if **any** sentence of the target is (§2, T2) | looser than whatever each model did on its own |
| 2 | Sarcastic span | not collected | copy the sarcastic sentence(s) into `sarcastic_span` | new; humans and assist only |
| 3 | What counts as a gap | "intended meaning differs from the literal one AND at least one cue" | same, plus: what the author *means* must be a real attitude, not just "I'm kidding" (§3, step 4) | slightly stricter than the prompt's wording |
| 4 | Gap without a polarity flip (mock praise, a fake apology, feigned agreement) | covered: polarity inversion is one cue of four, and rhetorical intent lists mock praise and feigned agreement | same as the prompt | **looser than how the adjudicator applied the prompt**: its rationales demand a strict inversion of the literal meaning |
| 5 | Hyperbole alone | a cue; still needs intended ≠ literal | never enough on its own (H2) | same, made explicit |
| 6 | Self-deprecation, *hugot*, playful elevation | silent; its jokes rule excludes jokes that mean what they say | ironic self-deprecation, wry *hugot* / self-pity poses and playful mock-elevation **are** sarcasm when the author clearly doesn't hold the literal evaluation (D1, broad reading); sincere self-criticism is not (H1, H3, H4) | **looser than `sarc-v2`**: more human positives than the ensemble, by design. Tag these items `D1` in `notes` |
| 7 | Quoted or reported sarcasm | silent | not sarcastic unless the author re-uses it ironically (T3) | a new specification |
| 8 | Phatic items | silent (the candidate filter now excludes unannotated phatic items) | not sarcastic unless an irony marker is present (H11) | same in practice |
| 9 | Image, link, or deleted parent | "judge only from the given text and context" | same, plus the unsure flag (H13) | same |
| 10 | Uncertainty | a 0–1 confidence | the `unsure` flag, plus the best reading; a 50/50 call is labeled not sarcastic (§6) | humans only |
| 11 | Language and sentiment | 90% rule on the target alone | same, plus counting rules (§5) | same, more specific |
| 12 | Cues on non-sarcastic items | "mark each true or false" | all four false when `sarcastic` is no (§3, step 7) | a specification; the models sometimes set cues on non-sarcastic items |

**Thesis note.** `03_methodology.tex` §Annotation says the researcher labeled
the gold subset "using the same definitions given to the models and without
seeing the model labels". Once this guideline is adopted, that sentence should
say the definitions were *made operational in a written guideline*, cite it
(for example as an appendix), state that the first 31 items were labeled
before it existed, and describe the assist flow (§6.4). Rows 1, 6, 7 and 12
of the table are where the human label can differ from the ensemble purely
because of the guideline. Row 6 (D1) is the largest such difference, so gold
κ should be reported twice: on all items, and with the `D1`-tagged items
counted as not sarcastic (the `sarc-v2` construct).

---

## 2. Unit of annotation

**T1. Label the TARGET, read with its thread context.** Read the context first
(the submission title and body, the parent chain, and the replies shown), then
the target. Label the target only. The context is evidence about what the
target means, never the thing being labeled. Use only what the review screen
shows, which is what the models saw. Don't open Reddit, don't search, and
don't use anything you know about the author or events outside the thread.

**T2. Partial sarcasm: a target is sarcastic if any part of it is sarcastic.**
A part means a sentence or clause written in the author's own voice that
passes the procedure in §3. Copy that sentence (or those sentences) verbatim
into `sarcastic_span`: the shortest stretch, cut at sentence boundaries, that
contains the sarcasm. Put each sentence on its own line, and leave the span
empty when the item is not sarcastic. For a one-sentence target, the span is
the whole target.

Why "any part":

1. Sarcasm is a property of an utterance. A post that contains one sarcastic
   line *does* sarcasm, and that is what a detector has to notice.
2. The alternatives ("most of the post", "the main point") need a second
   judgment that changes with post length and is hard to make consistently.
3. The span keeps the stricter reading available. A later analysis can count
   a target as sarcastic only when the span covers most of it, without
   relabeling anything.

The cost is that long posts get more chances to contain a sarcastic aside, so
positives may skew long. The v1 LLM labels followed no stated rule here,
which is why row 1 of §1.1 is a limitation. Report positives by length band
when the gold subset is analysed.

**T3. Quoted or reported sarcasm doesn't count by itself.** If the target
quotes or retells a sarcastic line (someone else's, or the author's own
earlier remark), the target is not sarcastic just because of that line:

- ✘ *"Sabi ng manager namin, 'ang aga mo naman ngayon' pero five minutes lang akong late. Nakakahiya talaga."* The author reports the boss's sarcasm and is sincerely embarrassed.
- ✘ Quoting a TV or movie line to make a sincere point.
- ✔ *"'World-class service' daw. Tatlong oras na ako sa pila."* The author re-uses the phrase ironically, in their own voice, so it counts. The span is the quoted phrase plus the sentence it sits in.

(Retelling your *own* earlier sarcastic line is open decision D2 in §7.)

**T4. Sarcasm aimed at oneself counts the same as sarcasm aimed at others**,
as long as it passes §3: *"Galing ko talaga, tatlong beses ko nang nakalimutan yung password ko ngayong araw."*
Self-deprecation that isn't ironic is a different case; see H3.

**T5. Sarcasm visible only from context counts.** Contextual incongruity is
one of the four cues. *"Grabe, ang bilis talaga"* is sincere under a post
praising same-day delivery and sarcastic under a post about a three-hour
queue. The context has to be in the thread shown. Don't guess at a
situation that isn't there.

**T6. A reply to sarcasm is judged on its own.** A sincere answer to a
sarcastic parent is not sarcastic. A reply that continues the irony is
sarcastic only if the reply itself says something its author doesn't mean.

---

## 3. Decision procedure

Work through these steps in order for every item.

1. **Read the context, then the target.** Note who is speaking (OP or not)
   and what situation the thread describes.
2. **Is it phatic?** Greetings, thanks, birthday wishes, congratulations, or
   condolences with nothing else in them → **not sarcastic** (H11). Stop. If
   an irony marker is present ("edi congrats", "thanks for nothing", 🙄),
   carry on.
3. **Does the meaning hinge on something you can't see** (an image, a link,
   a video, a deleted parent, an event not described in the thread)? If the
   gap in step 4 can only be found by guessing at that missing thing →
   **not sarcastic, and set `unsure`** (H13). Stop.
4. **Gap test, one sentence at a time.** Split the target into sentences in
   the author's own voice (leave out quoted or reported lines, T3). For each
   one, write two short lines:
   - **Says:** what the words literally claim or express.
   - **Means:** what the author actually intends.

   A sentence passes only if all three of these hold:
   - (a) *Says* ≠ *Means*. A sincere statement, a complaint, an insult, an
     apt comparison, or a joke that means what it says fails here (H4, H10).
   - (b) *Means* is an attitude the author really holds: criticism,
     mockery, a complaint, resentment, contempt, wry self-pity or
     amusement, or affection hidden behind a mock insult. Under D1 (broad
     reading), a wry pose counts: an exaggerated self-put-down (*hugot*),
     self-pity or a playfully elevated description, when the author clearly
     doesn't hold the literal evaluation. Tag such items `D1` in `notes`.
     If *Means* is only "I'm kidding" or "I'm playing along" (an absurd
     scenario with no evaluation in it), it fails (H4).
   - (c) At least one cue makes the gap visible to an ordinary reader of the
     thread (table below). If you can only reach *Means* by speculating about
     the author, it fails.
5. **Any sentence passes → `sarcastic = yes`.** Copy the passing sentence(s)
   into `sarcastic_span` (T2). If none passes → `sarcastic = no`.
6. **Hesitating?** If you're torn between two readings after a second read,
   label your best reading, set `unsure`, and write the other reading in
   `notes`. If it's genuinely 50/50, label **no** (§6.1).
7. **Cues.** If the item is sarcastic, mark every cue that supports the
   sarcastic span. If it's not sarcastic, leave all four cues false, even if
   there is sincere exaggeration.
8. **Language and sentiment** (§5).

### When is a single cue enough?

| Cue | Enough on its own? |
|---|---|
| **polarity_inversion**: the stated sentiment is the opposite of the intended one | **Yes**, once the inversion is actually established by context or a marker. It is the gap itself, so it needs evidence. Don't assume it. |
| **rhetorical_intent**: mock praise, feigned agreement or apology, or a question that pretends to ask in order to jab | **Yes**, when the pretense is clear. A rhetorical question that sincerely expresses disbelief or outrage is not this cue (H5). |
| **contextual_incongruity**: the literal reading clashes with the situation in the thread | **Yes**, when the clash is the point (praise under a rant, congratulations on a misfortune). **No**, when the comment is just off-topic, misreads the post, or is a sincere reframe or pep talk. |
| **hyperbole**: exaggeration, stacked intensifiers, caps | **Never alone.** Exaggeration in the direction of what the author really feels is emphasis. Hyperbole counts only alongside another cue showing that the exaggerated attitude isn't held (H2). |

---

## 4. Hard cases

Each case gives a ruling, the reason, and examples. ✔ means sarcastic, ✘
means not. Square brackets give the thread context.

### H1. Mock praise ("ang galing mo naman")

**Ruling:** sarcastic when the context or a marker shows the praise isn't
meant (it is criticism or mockery wearing praise). Not sarcastic when the
praise fits the situation. Under D1 (broad reading), a **playfully elevated
description** is sarcastic too: grand praise for something ordinary or
faulty that the author clearly doesn't mean literally (tag `D1`).

- ✔ [OP scraped the car on the gate during their first week of driving] *"Ang galing mo naman, pang-F1 ka na talaga."* Says: you're a great driver. Means: you're a bad driver. Cues: polarity inversion, rhetorical intent, contextual incongruity.
- ✘ [OP shows the first cake they ever baked, and it looks good] *"Ang galing mo naman! Parang binili sa bakery."* This is sincere.
- ✔ (`D1`) [OP says their dog refuses dog food] *"Pang-mayaman ang panlasa ng aso niyo, ayaw ng dog food haha."* Says: your dog has refined taste. Means: your dog is being picky, and that's funny. The elevation is a pose, not a belief. Cues: rhetorical intent and hyperbole.

### H2. Hyperbole alone vs hyperbole with inverted intent

**Ruling:** hyperbole alone is **not** sarcasm. Exaggeration that points the
way the author really feels is emphasis, and that includes exaggerated
complaints and exaggerated delight. It is sarcasm when the exaggerated
attitude is one the author doesn't hold, and another cue shows that.

- ✘ *"Sobrang init ngayon, parang nasa loob ako ng oven buong araw."* A sincere complaint. All cues false.
- ✘ *"BEST. MILK TEA. EVER. Babalik ako bukas!!!"* Sincere enthusiasm.
- ✔ *"Wow, best commute EVER. Dalawang oras sa EDSA tapos tumirik pa yung bus 🙂"* Hyperbole plus polarity inversion: the day was bad.

### H3. Self-deprecation

**Ruling:** it depends on whether the self-put-down is meant.

- **Sincere self-criticism → not sarcastic.** ✘ *"Ang tanga ko talaga, naiwan ko na naman yung ID ko."*
- **Wry *hugot*, self-pity or self-deprecating humor → sarcastic under D1**
  (broad reading, tag `D1`), when the self-assessment is a pose the author
  wouldn't stand by literally. The real attitude is self-pity, rueful
  amusement or a complaint about their situation.
  ✔ (`D1`) *"Taga-heart react na lang talaga ako sa engagement photos ng iba, yun na role ko 🥲"* Says: my role in life is reacting to others' engagements. Means: I feel left behind (wry self-pity). Cues: hyperbole, rhetorical intent.
  ✔ (`D1`) [thread about being judged in a hobby] *"Sasali sana ako, pero baka ako pa yung magmukhang creep haha."* A self-put-down offered as a wry pose.
  ✘ An exaggerated complaint about the world, not about the self (H2), is
  still emphasis: *"Sobrang init, parang oven."*
- **Ironic self-deprecation → sarcastic.** The author pretends to accept a
  low status or blame they actually reject, as a way to criticise someone or
  something else.
  ✔ [thread asking whether ₱4,500 per head is fair for a "luxury picnic"] *"Pasensya na po, mahirap lang kami, sa Luneta lang kami nagpi-picnic lol."* Says: sorry, we're poor. Means: that price is absurd. Cues: rhetorical intent (a fake apology) and hyperbole.
  ✔ *"Ay oo, kasalanan ko na naman. Ako lang naman kasi ang empleyado dito 🙃"*
- **Ironic self-praise → sarcastic** (T4). ✔ *"Galing ko talaga, tatlong beses ko nang nakalimutan yung password ko."*

Quick test: does the author believe the words? A put-down the author
believes, about themselves, is not sarcasm. A put-down they don't believe is
sarcasm: used to hit something else (no tag), or as a wry pose about
their own situation (tag `D1`).

### H4. Jokes, banter, and playful teasing

**Ruling: keep the core of the `sarc-v2` jokes rule.** Jokes, banter, and
teasing whose literal meaning *is* the intended meaning are not sarcasm,
however funny or mocking they are. **Kidding statements with no evaluation
in them** (playing along, absurd scenarios, "charot" retractions of a tease)
are not sarcasm either, because they fail step 4(b). A joke **is** sarcasm
when it works by saying the opposite of a real evaluation, and, under D1,
when it is a playful elevation or wry self-put-down the author doesn't hold
(H1, H3; tag `D1`).

- ✘ *"Mukhang uling yung kape mo haha."* (the `sarc-v2` example) The author really thinks the coffee looks like charcoal.
- ✘ [OP shows new native chickens. Parent: *"Ang ganda nila, pang-tinola na. Joke!"*] *"Shh, baka marinig ka nila 😂"* This is playing along with a joke. *Means* is "I'm joking too."
- ✘ [OP bought choc-chip cookies with no chips] *"Collector's item yan, limited edition 😆"* A silver-lining joke that consoles the OP, with no real criticism behind it.
- ✔ Same thread: *"Galing ng quality control nila, collector's item talaga 🙄"* The praise is aimed at the brand and is withdrawn: mock praise.
- ✔ *"Ang bango ng kwarto mo, parang medyas na isang linggong hindi nilabhan 😂"* Playful, but it says the opposite of the real evaluation.

Why keep the core: it is in the prompt the v1 labels came from, and it was
added because the pilot showed a model over-flagging humor (base rate 21% vs
11%). D1 loosens it only for wry poses. That is why those items carry the
`D1` tag: κ can then be reported with and without them (§1.1).

### H5. Rhetorical questions

**Ruling:** sarcastic when the question pretends to ask, or pretends not to
know, in order to jab. Not sarcastic when it is a real question, a sincere
expression of disbelief or outrage, or an insult phrased as a question
(a direct insult is not sarcasm).

- ✔ [officemate arrives two hours late] *"Ang aga niya ah. Anong oras ba pasok niyo, tanghalian?"*
- ✘ *"Seryoso? Ginawa niya talaga yun?"* Real incredulity.
- ✘ [post warning about a deadbeat ex] *"Wala pa ring trabaho? Wala pa ring sustento?"* Echo questions that sincerely repeat the post's point with indignation. Don't infer a hidden "he's actually rich" reading.
- ✘ *"May utak ka ba?"* A direct insult.
- ✘ *"Kinausap mo na ba siya?"* A real question.

### H6. "Sana all": sincere vs ironic

**Ruling:** sincere by default. "Sana all" expresses wishing, wistfulness,
or admiration. It is ironic when it is aimed at someone to belittle them:
it implies luck, privilege, or something undeserved, or comes with 🙄,
"edi", "swerte", or "sponsor", or answers a humblebrag.

- ✘ [thread daydreaming about travelling without worrying about money] *"Sana all talaga 🥲 gusto ko na rin mag-Japan."* Sincere longing that agrees with the thread. 🥲 is wistful, not mocking.
- ✘ *"Sana all!! Congrats, deserve mo yan."* (the `sarc-v2` example)
- ✔ [OP flexes a surprise iPhone from their partner] *"Sana all may sponsor 🙄"*

### H7. "Edi wow", "ikaw na", "edi sana"

**Ruling:** "edi wow" and "ikaw na" are conventionally dismissive
mock-admiration, so they are **sarcastic by default** (rhetorical intent).
A sincere use is rare and needs clear support in the context. "Edi sana"
plus a counterfactual is a plain complaint, not sarcasm. "Edi" on its own is
not a marker.

- ✔ [OP brags about being the only one in class to get a uno] *"Edi wow. Ikaw na talaga."*
- ✘ *"Edi sana sinabi mo agad, hindi na sana ako pumunta."* A direct complaint.

### H8. Markers: emoji, "char/charot", "/s", "daw/kuno", scare quotes

A marker can supply the cue in step 4(c), but the gap test still has to pass.
No marker makes an item sarcastic automatically.

| Marker | Reading |
|---|---|
| `/s` | an explicit sarcasm tag; strong evidence. Sarcastic unless the tagged line plainly has no gap (some people tag jokes with `/s`). |
| char, charot, chariz, joke lang, de joke | "just kidding": the line before it isn't meant literally. That alone is **not** sarcasm. It's sarcastic only if the retracted line is mock praise or ironic criticism. ✘ *"Crush ko siya, char."* ✘ *"Pang-tinola na sila, charot!"* ✔ *"Ang ganda ng boses mo, pang-Grammy. Charot."* (when the context shows the singing was bad) |
| 🙄 😒 🙃 🤡, clapping between words (*"galing 👏 mo 👏"*) | irony and mockery markers. They usually supply rhetorical intent or polarity inversion. |
| 😂 🤣 haha, HAHAHA | humor, not irony. They don't move the label either way. |
| 🥲 😭 🥹 | emotional, usually sincere (wistful, touched, sad). |
| daw, raw, kuno, *"supposedly"* | reportative distance. Sarcastic when used to mock a claim (✔ *"Pro-poor daw sila 🙄"*). Not sarcastic when simply reporting (✘ *"Sarado daw sila bukas."*). |
| "scare quotes" around one word | ironic distancing; often rhetorical intent (✔ *the "free" upgrade na may bayad pala*). |
| CAPS, letter stretching (*"sobraaaa"*) | hyperbole cue only (H2). |

### H9. Praise with a twist, and positive framing of a misfortune

**Ruling:** if the twist shows the praise wasn't meant, the item is
sarcastic. If the twist adds a sincere reservation, it isn't. Congratulating
or celebrating someone's misfortune is sarcastic when it mocks them, and not
when it is a sincere reframe or pep talk. With no marker either way, treat it
as 50/50: label no and set `unsure`.

- ✔ *"Ang bilis ng delivery nila, tatlong linggo lang 🙂"*
- ✘ *"Ang ganda ng service, medyo mahal lang talaga."* Sincere praise plus a sincere reservation.
- ✔ [OP was scammed of ₱20k] *"Congrats, may lesson ka na worth 20k 👏"*
- ✘ + `unsure` [OP was let go after three months] *"Welcome to your tambay era."* It could be a pep talk or a jab, and nothing marks it.
- ✔ Same thread: *"Ayan kasi, congrats sa tambay era mo 👏"*

### H10. Complaints that are just negative

**Ruling:** direct criticism, insults, rants, swearing, and sincere
complaints are **not** sarcasm, however harsh, and including exaggerated
ones (H2). Only a sentence that pretends a different attitude is sarcastic,
and by T2 that one sentence is enough.

- ✘ *"Ang bagal ng LRT ngayon, nakakainis."*
- ✘ *"Ang kapal ng mukha ng landlord mo."*
- ✔ *"Nakakainis yung landlord ko. Ang bait niya talaga, tinaasan na naman yung upa kahit sira pa yung CR."* Span: the second sentence.

### H11. Phatic items (greetings, thanks, birthday wishes)

**Ruling:** a target that is only a social ritual (*"Happy birthday OP! 🎂"*,
*"Salamat po!"*, *"Congrats!"*, *"Ingat kayo"*) is **not sarcastic**, with
literal and intended sentiment both positive. Any irony marker breaks this
rule, and then the item goes through §3 normally: ✔ *"Edi congrats, late ka na naman."*
✔ *"Thanks for nothing."* Ritual text followed by real content is not phatic:
label the content.

Since the U9 change, phatic items that were already annotated stay in the
dataset and are flagged with `candidates.phatic = 1`, so they can still
appear in the gold queue. Label them like any other item (it takes seconds).
See D4 for whether they should count toward the 300.

### H12. Very long confessional posts

**Ruling:** read the whole target. Most vents are sincere: plain bitterness
and sincere self-pity are not sarcasm (H10). A vent's overall sad tone is not
a D1 pose either: under D1 a single sentence must be a wry self-put-down the
author doesn't hold (H3). Look for sentences that pretend an attitude, which
in vents is usually mock praise of a third party
(✔ *"Ang galing talaga ng HR namin, tatlong buwan na wala pa ring sweldo."*).
One such sentence makes the target sarcastic (T2), with that sentence as the
span. Don't label a long post sarcastic because of its overall tone. If the
post is very long, read every sentence near a marker from H8 carefully and
skim the rest for them.

### H13. Meaning that depends on an unavailable image or link

**Ruling:** the models saw text only, so the human judges from text only. If
the gap depends on what an image, link, or video shows (*"Ganda ng view 😍"*
under a photo you can't see), or on a parent that is deleted or empty, label
**not sarcastic and set `unsure`**, with the note *"depends on image/link"*.
The exception is when the text alone establishes the gap (a 🙄, a
contradiction inside the target, or a marker from H8). The same applies when
a target refers to something that isn't in the thread at all.

---

## 5. Language and sentiment

These follow `sarc-v2` and the thesis's 90% rule.

**L1.** Judge language from the **target text alone**, never from the
thread.

**L2.** Count the author's own words. Leave out proper nouns, brand,
product, food, and place names, subreddit and app names, URLs, emoji,
laughter (haha, HAHAHA), and internet tokens (lol, lmao, OP, char/charot).
Count "ok/okay" as neither language.

**L3.** If roughly 90% or more of the counted words are one language, use
`english` or `tagalog`. Otherwise use `taglish`. On short targets, a single
content word in the other language is already more than 10%, so
*"Rare yan, parang misprint"* is `taglish`.

**L4.** A lone function word or particle from the other language (sa, na,
pa, lang, po, naman, din, ba) does not make an otherwise one-language target
Taglish, because the prompt says "meaningfully mixes". For example,
*"He's still jobless sa PH?"* is `english`. Two or more such words, or any
content word, do count.

**S1. `literal_sentiment`** is the surface polarity of the words alone,
ignoring sarcasm. *"Ang galing mo naman"* is positive.

**S2. `intended_sentiment`** is what the author means. For a sarcastic item
it usually flips (positive → negative). It can keep the same polarity when
the gap is one of stance and not polarity (a fake apology can be
negative → negative).

**S3.** For a non-sarcastic item, intended sentiment normally equals literal
sentiment. If you set them differently, say why in `notes`.

---

## 6. When unsure, and the blind-first assist flow

**6.1 Use the `unsure` flag. Don't guess silently.** Set `unsure` whenever
step 3 or step 6 applies, or whenever the context you'd need is missing.
Still give a label: your best reading, or **no** when it's 50/50 (the base
rate is about 11%, so a coin-flip "yes" mostly adds false positives). Write
the competing reading in `notes`. The export marks unsure items
(`human_gold.unsure`), so gold κ can be reported both with and without them.

**6.2 Notes.** Write one line naming the rule that decided the item (for
example `H3 ironic self-dep` or `H9 50/50`). That one line is what makes the
labels auditable later.

**6.3 Blind first.** On gold items, don't tick "Reveal model votes" before
saving. If you do, the app records it (`votes_seen`), and that item is left
out of the gold κ.

**6.4 The assist flow.** (Since 2026-11-22 the review page has a toggle,
*Show Claude's suggestion before I label*, for a second judgement while
labeling. It is off by default. An item saved with it on is recorded as
`assist_before_first_pass`. Its first pass is then Claude-informed rather
than blind, and the card reports how many there are, next to the gold κ.)

1. Label the item blind and save. This is your **first-pass** label.
2. Only after saving may the app show the Claude suggestion
   (`assist_suggestions`: label, span, rationale, confidence).
3. You may revise. If you do, the app keeps your first-pass labels in
   `first_pass_json` and sets `assist_seen = 1`.
4. **Gold κ (human vs ensemble, for sarcasm, language and both
   sentiments) uses the first-pass label**, and so do `eval-report` and the
   LID accuracy. Post-assist labels are reported separately and are never
   the headline number. A correction you make before revealing anything is
   saved as your label, but the first pass stays as first saved.

*Implementation status (2026-11-19): all of this is in the Annotation Review
tab (commit 2469da3). It has sentence checkboxes for the span, an "Unsure"
checkbox, and a "Show Claude's take" button that appears only after the first
save. The gold κ in the dataset card reads `first_pass_json`, and the export
ships `human_gold.first_pass`, `sarcastic_span`, `unsure`, `assist_seen` and
`votes_seen`.*

**6.5 Rules for the assist agent.** The agent:

- works from the target, its context, and this guideline **only**. It never
  sees the ensemble's votes, the adjudicator's output, or any human label;
- follows §3 step by step and writes a rationale of one or two sentences
  that contains the *Says / Means* pair and the rule IDs it used;
- fills `sarcastic`, `sarcastic_span`, `rationale`, `confidence`,
  `source` (the model id), and `guideline_version = "guideline-v1.1"`;
- follows §6.1 too: in a 50/50 case it outputs no, with low confidence.

Running the assist sends item text to a third-party model provider. That is
a processing step the thesis's ethics text should state (UYAM_LATEST §3.1).

---

## 7. Open decisions for Gio

- **D1. Hyperbolic self-pity, *hugot*, and playful mock-elevated
  description. Resolved 2026-11-22 (Gio): broad reading.** They count as
  sarcasm when the author clearly doesn't hold the literal evaluation (H1,
  H3, H4, step 4b). This matches most of the first 31 human labels, and it
  moves the gold labels away from `sarc-v2`. The reviewer tags such items
  `D1` in `notes` so κ can be reported with and without them.
- **D2. The author retelling their own earlier sarcastic remark** (*"sabi
  ko sa kanya, 'wow, ang aga mo ah'"*). Draft: not sarcastic (T3), but mark
  it in `notes`.
- **D3. 50/50 defaults to "no".** The alternative is to give the best guess
  even at 50/50 and rely on `unsure` alone.
- **D4. Phatic items in the gold sample. Resolved 2026-11-19:** the
  unlabeled gold items are drawn from eligible, non-phatic items only
  (stratified by sarcasm × language × split vote × length; since 2026-11-22
  the split and shipped-sarcastic strata are each weighted ×2). The two phatic
  items among the first 31 keep their labels; the export flags them
  (`flags.phatic`), so κ can be reported with and without them.
- **D5. Re-label the first 31 under this guideline.** A blind second pass
  at least two weeks after the first would also give the intra-annotator κ
  that the proposal planned. It has to happen **before** reading
  `data/guideline_examples.md`, which shows the earlier labels.

---

## 8. Changelog

| Version | Date | Change |
|---|---|---|
| v1.1 draft | 2026-11-22 | D1 resolved (broad reading): wry *hugot*, self-pity and playful elevation count, tagged `D1` (step 4b, H1, H3, H4, §1.1 row 6). The gold queue was redrawn with shipped-sarcastic strata weighted ×2. Assist suggestions were regenerated under `guideline-v1.1`. |
| v1 draft | 2026-11-19 | First written version, for Gio's review. Starts from `sarc-v2`. Adds the any-part rule and the sarcastic span (T2), quoted sarcasm (T3), the *Says/Means* gap test with the "real attitude" condition, cue sufficiency, hard cases H1–H13, the language counting rules, the `unsure` flag, and the blind-first assist flow. Checked against the 31 existing gold items (private: `data/guideline_examples.md`). |
