# UYAM_LATEST — data-side status for the thesis

What has changed in **uyam** (data collection and annotation) that the thesis
(**tsis**) needs to know about. The source of truth is this file in the uyam
repo; an identical copy sits at the root of tsis. Newest information first.

- **Last updated:** 2026-11-22 (late night), uyam `bb73abc`+ (plus this file's commit)
- **Dataset the manuscript and all leische results use:** `dataset-v1`
  (`prompt_version = sarc-v2`, exported 2026-09-15). **Still current.**
- **`dataset-v2` is exported** (2026-11-22 00:34 PHT, `uyam_commit`
  `39b1500`, card `dataset_card-v2.json`). leische has received it and
  verified the bundle checksums (LEISCHE_LATEST, 2026-11-22). Its v2 runs are
  paused at 1 of 11, so there are no v2 results yet. Checked against v1: the
  same 15,000 ids with 0 label changes (sarcastic, language, both
  sentiments, cues). Card numbers: 1,601 shipped / 2,747 majority positives,
  1,712 rows differ, κ shipped vs majority 0.545. Coverage: 48 h 46.5%,
  7 d 64.9%, 30 d 82.1%, unbounded 89.4%. 120 rows are `phatic`-flagged.
  The corpus dump holds 281,275 records (labeled authors). Gold: 31 labeled
  (κ −0.148). A re-export will carry the rest of the gold labels with the
  training labels unchanged. **tsis: the v2 dataset figures below are final
  for v2, but keep them out of the manuscript until leische has v2
  results, so the chapters don't mix datasets.**
- **`dataset-v3` is in preparation** (equal-size re-annotation, the adviser's
  item 5; see §10). It is a separate database
  (`data/db/annotation-v3.sqlite3`), so v1/v2 are untouched. Its 21,000
  targets are the 15,000 v2 items plus 6,000 new items: 1,600 each from
  June, July and October, and 1,200 from Nov 1–10. The lineup is three
  ~8–9B models: qwen3:8b, Gemma-SEA-LION-v3-9B and Llama-SEA-LION-v3-8B, all
  q4 on the laptop GPU. Labels are by majority vote, with no adjudicator.
  The 100-item pilot passed. The full run (~28 h of laptop GPU) waits on
  Gio's go. Nothing to quote yet.

> **For tsis editors:** don't put any *provisional* number into the
> manuscript. Chapter 4 is computed on v1, and mixing v1 results with v2
> corpus figures would make the chapters disagree. Update the manuscript once
> this file announces `dataset-v2` as exported, together with leische's
> re-run on it.

---

## 1. Dataset versions

| | `dataset-v1` (in the manuscript) | `dataset-v2` (exported 2026-11-22) |
|---|---|---|
| Collection window | 2026-08-01 → 2026-09-04 (~5 weeks) | 2026-06-01 → 2026-11-10 (~5 months). The card's `collection_window` reads 2025-07-30 → 2026-11-13: one stray 2025 post (as in v1) and a few Nov 11–13 records from the live walks. Labeled rows still span 2026-08-01 → 09-03 |
| Corpus records | 20,573 (1,954 submissions, 18,619 comments) | 588,507 collected (40,125 / 548,382); `corpus-v2.jsonl` ships the 281,275 by labeled authors |
| Distinct authors | 8,920 | 63,754 |
| Eligible after filters | 17,424 | 512,471 (of 588,507; 1,417 unlabeled phatic items excluded) |
| Annotated items | 15,000 | **15,000, the same items with identical labels** (verified against v1); 120 flagged `phatic` |
| Sarcastic | 1,601 (10.7%) | unchanged unless re-annotated |
| Preprocessing filters | 6 reasons (manuscript Table 3.1) | 7 reasons: adds `phatic` for unlabeled items; labeled ones are flagged, not dropped (§4) |
| Keyword oversampling | not executed | search step redundant; optional at annotation (§5) |
| Prompt / labels | `sarc-v2` | `sarc-v2` (no prompt change so far) |

## 2. Corpus right now (2026-11-19, *provisional*)

The **natural pass is complete** for all three subreddits, Jun 1 → Nov 10.
The keyword-oversampling searches are running (§5) and will add a little
more.

| Subreddit | Submissions | Comments |
|---|---|---|
| r/Philippines | 23,938 | 424,022 |
| r/OffMyChestPH | 12,210 | 95,417 |
| r/CasualPH | 3,977 | 28,943 |
| **Total** | **40,125** | **548,382** |

Raw size: ~770 MB of JSONL, about 30× v1's 20 MB corpus dump.

### 2.1 Temporal-context coverage (measured on the 15,000 v1 labeled items)

Share of labeled items with ≥1 earlier post by the same author inside the
history window, on the v1 corpus vs the current corpus:

| History window | v1 corpus | current corpus | records the model would read (≤5 per item) |
|---|---|---|---|
| 48 h (current thesis rule) | 36.8% | **46.5%** | 9,637 |
| 7 days | 45.2% | **64.9%** | 17,098 |
| 30 days | 53.5% | **82.1%** | 27,819 |
| unbounded (before the item) | 53.5% | **89.3%** | 34,990 |

At 48 h: ≥3 prior posts for 18.9%, the full 5 for 10.1%.

**Why this matters for the method:** all 15,000 labels are from Aug–Sep, so
under the 48-hour rule almost none of the Jun–Jul and Oct–Nov data can reach
the model. Only 9,637 of 588,507 records (1.6%) are ever read. Widening the
window (the model already decays older posts with λ) is what turns the
backfill into coverage: 7 days clears leische's U5 target of ≥60%. This is a
**leische / methodology decision**, not a uyam one. Most of the corpus is
context, not annotation targets: nobody annotates 588k items.

### 2.2 Length profile of the labeled items (v1, for the analysis chapter)

Sentences counted by a punctuation/line-break splitter (approximate for
informal Taglish). F1 is leische's seed-13 out-of-fold predictions at the
tuned threshold.

| Sentences | Items | Share | Sarcastic | 2-1 split votes | F1 baseline | F1 full model | > 192 tokens |
|---|---|---|---|---|---|---|---|
| 1 | 4,941 | 32.9% | 14.7% | 41.2% | 0.318 | 0.337 | 0.0% |
| 2 | 3,967 | 26.4% | 12.3% | 32.8% | 0.364 | 0.375 | 0.0% |
| 3 | 2,102 | 14.0% | 9.4% | 24.9% | 0.404 | 0.392 | 0.0% |
| 4–5 | 2,015 | 13.4% | 6.2% | 20.5% | 0.344 | 0.326 | 0.5% |
| 6–10 | 1,351 | 9.0% | 4.1% | 15.2% | 0.192 | 0.245 | 12.7% |
| 11+ | 624 | 4.2% | 1.6% | 10.7% | 0.000 | 0.000 | 77.1% |

≤3 sentences is 73.4% of items and 88.1% of the sarcastic ones (1,411 of
1,601). ≤5 sentences is 86.8% of items and 95.9% of positives. Long items are
mostly r/OffMyChestPH and submissions.

## 3. How the data is collected now

The v1 corpus was scraped live from Reddit pages. Two things changed:

1. **Reddit closed its unauthenticated `.json` listings** (HTTP 403, observed
   2026-11-10). The live scraper ("Shreddit") now reads the rendered page.
   Logged-out `/new` only reaches back ~1,000 posts (about 8–10 days on
   r/Philippines), so live scraping can't recover older months.
2. **Historical posts come from the Arctic Shift archive**
   (arctic-shift.photon-reddit.com), a third-party Reddit archive queried by
   subreddit and date. This is what extends the window to 2026-06-01.

Facts the methodology / ethics text will need (once v2 is final):

- Two sources: live page scrape (Aug–Sep core; Nov 8–10) and Arctic Shift
  (Jun 1 → Nov 10). Each record's source is recorded in its collection-run
  manifest.
- Usernames are still replaced by the same keyed HMAC-SHA256 hash, applied
  on ingest for both sources. The same author gets the same hash across
  sources, which is what lets temporal context link them.
- Archive `score` and `num_comments` are snapshots from when the archive
  ingested the item, not current values. Don't compare scores across sources.
- Archive comments are collected for **at least 3 days after each post**.
  Later replies to archive-sourced posts may be missing.
- Posts are all posts per day (cap 300/day; r/Philippines has ~250–270).
- Collection used rotating residential proxies for the live scraper. The
  archive needs none.

### 3.1 Ethics facts the thesis must state accurately (checked 2026-11-19)

- **No data came through Reddit's official API.** The v1 core was collected
  by an automated scraper reading Reddit's logged-out JSON listings and pages
  through rotating proxies. v2 history comes from the Arctic Shift archive.
  The scope paragraph's reason for choosing Reddit ("its public Application
  Programming Interface (API)") does not describe how the data was actually
  collected.
- **Reddit User Agreement** (effective 1 July 2026): users may not "Access,
  search, or collect data from the Services by any means (automated or
  otherwise) except as permitted in these Terms or in a separate agreement
  with Reddit (we conditionally grant permission to crawl the Services in
  accordance with the parameters set forth in our robots.txt file, but
  scraping the Services without Reddit's prior written consent is
  prohibited)". No research exception appears in the Agreement itself.
- The live scraper switched proxies when Reddit rate-limited or blocked an
  IP ("blocked by network security"). That is working around an access
  control, and it should be disclosed or avoided. Since the archive now
  covers the whole window, no further live scraping is needed.
- The archive keeps copies of content that may since have been deleted on
  Reddit. A deletion policy is still to be decided (the tsis v2 draft has a
  `{{v2.deletion_policy}}` placeholder).
- These are for Gio and the adviser / ethics reviewer to weigh. uyam
  doesn't decide them.

## 4. Preprocessing change: `phatic` (applied 2026-11-19)

A filter for short social-ritual items with nothing else in them: happy
birthday, thanks, congrats, greetings, "commenting to follow". It applies
only when a phatic phrase matches, the item is ≤ 10 tokens, and ≤ 2 content
tokens remain. Irony markers veto it ("edi congrats", "thanks for nothing",
"ikaw na").

Applied to the full corpus on 2026-11-19 (after a database backup):
- **1,417 unlabeled** items excluded (reason `phatic`).
- **120 already-labeled** items are **kept with their labels and flagged**
  (`flags.phatic` in the export). This follows leische's U9 and Gio's choice
  of frozen labels. None of the 120 is labeled sarcastic.
- Every one of the 15,000 labeled items stays eligible, and their labels,
  context snapshots and Gio's 31 gold labels were compared against the backup:
  0 changes.

Manuscript Table 3.1 stays correct for v1. For v2 it gains a `phatic` row
(1,417) and the v2 counts.

## 5. Keyword oversampling

The manuscript comment in §3.1 says oversampling "was implemented but not
executed". Status for v2 (2026-11-19):

- **Searching Reddit by keyword adds nothing any more.** The natural pass
  collected *every* post from Jun 1 to Nov 10, so the archive keyword searches
  only re-find posts already in the corpus (first 5 searches: 107 hits, all
  duplicates). They were stopped. Archive search also covers only post
  titles and bodies, and the markers live mostly in comments.
- **The markers are rare.** 1,196 of 588,507 items contain any of the 8
  ("edi wow", "sana all", "wow naman", "ang galing naman", "talaga lang",
  "ikaw na", "buti naman", "yan tayo eh"). 48 are already labeled, leaving
  1,148 unlabeled.

| Marker | Posts | Comments | Labeled | Labeled sarcastic |
|---|---|---|---|---|
| sana all | 43 | 264 | 15 | 8 (53%) |
| ikaw na | 93 | 565 | 31 | 3 (10%) |
| buti naman | 10 | 142 | 2 | 0 |
| others (5 markers) | 5 | 80 | 0 | — |

  Only "sana all" shows a sarcasm rate above the 10.7% base, on a small
  sample. "ikaw na" is mostly literal ("ikaw na bahala").
- **What oversampling can still mean:** choose extra *annotation targets* by
  marker from the collected corpus, tagged `keyword_oversampled`, and keep
  them out of natural-distribution metrics. That's an optional extra
  annotation of ~1,148 items, and it's Gio's decision. leische asked that the
  15,000 v1 labels stay unchanged, which this wouldn't affect.
- If nothing is added, the honest wording is that oversampling was **not
  needed for collection** (every post in the window was collected) and was
  not applied at annotation.

The 57 posts (and 543 comments) from the 2026-11-10 Shreddit trial that
were wrongly tagged `keyword_oversampled` were **re-tagged `natural`** on
2026-11-19 (`uyam retag-calendar-walk`, with Gio's OK). The rewritten files
were backed up first, the change is noted in the three run manifests, and
the corpus now has **no** `keyword_oversampled` records.

## 6. What hasn't changed

- Annotation prompt (`sarc-v2`), the three annotator LLMs, the adjudicator,
  and the 15,000 labels.
- Gold subset: **31 of 300** labeled by Gio. The 269 unlabeled gold items
  were **redrawn** (Gio's OK): eligible, non-phatic items only, stratified
  sarcasm × language × split vote × length band. On 2026-11-19 the redraw was
  proportional with split votes ×2. The old strata had forced ≥1 item from
  ~100 tiny cells, which over-drew long submissions (20 → 4) and pulled in
  greeting posts (3 → 0). On **2026-11-22** the strata the ensemble shipped as
  sarcastic were also weighted ×2 (Gio), so **62 of the 300 are
  ensemble-sarcastic** (about 33 under the earlier draw). That gives a usable
  κ on the sarcastic class. **The gold sample is not at natural prevalence**:
  weight back when estimating it. Gold κ on sarcasm over the first 31 is
  −0.148, as leische measured.
- Conversational context per labeled item is still the snapshot the
  annotators saw. New data adds **temporal** context (other posts by the same
  author), not new conversational context for existing items.

### 6.1 Label definition gap and the new guideline (leische U3)

The `sarc-v2` prompt defines `sarcastic` for "the TARGET" (intended meaning
differs from literal, plus a cue). It never says whether a long post counts
as sarcastic when only part of it is. Each annotator LLM chose for itself,
which adds noise on long items. That gap is a limitation of the v1 labels.

**`docs/GUIDELINE.md` (draft v1.1, 2026-11-22)** now governs the human gold
labels. A target is sarcastic if any author-voice sentence is, and that
sentence is marked as the span. It has a Says/Means test, 13 hard-case
rulings, and §1.1 lists every difference from `sarc-v2`. **D1 is decided:
the broad reading** (Gio, 2026-11-22). Wry *hugot*, self-pity and playful
mock-elevation count as sarcasm when the author clearly doesn't hold the
literal evaluation. This is looser than `sarc-v2`, so the human labels
will have more positives than the ensemble by design. Such items are tagged
`D1` in the review notes, so the gold κ can be reported twice: as labeled,
and with the D1 items treated as not sarcastic (the `sarc-v2` construct).
D2, D3 and D5 are still open. This bears on leische's U2 (the
adjudicator's definition), which stays deferred until ~150 gold items are
labeled.

Two notes for the thesis text (found while writing the guideline):
- `03_methodology.tex` says the researcher labeled "using the same
  definitions given to the models". Once the guideline is adopted, it should
  say the definitions were made operational in a written guideline, and that
  the first 31 items predate it.
- "When all three annotators agreed on the sarcasm label, that label was
  accepted" isn't exact. 341 rows with a unanimous sarcasm vote were still
  adjudicated because another label split, and the adjudicator flipped the
  sarcasm label on 52 of them.

### 6.2 New review and evaluation tooling

- **Blind-first assist (Claude Code):** suggestions for the gold items are
  computed by Claude Code agents from the guideline only (no ensemble votes,
  no human labels). The Review tab reveals them only **after** Gio saves his
  own label. The first-pass label is kept (`first_pass_json`) and is what
  every gold score uses: the card's gold κ, the LID-vs-gold accuracy and
  `eval-report`. Gold items whose model votes were shown before the first
  save are recorded (`votes_seen`) and left out of those scores. The export
  ships `human_gold.first_pass` (same shape as the other human labels),
  `sarcastic_span`, `unsure`, `assist_seen`, `votes_seen` and (from
  2026-11-22) `assist_before_first_pass`: Gio can opt to see Claude's
  suggestion before labeling, as a second judgement. Those first passes are
  Claude-informed, still counted in κ, and their number is reported. 300
  full-label suggestions under `guideline-v1.1` are loaded for the redrawn
  queue (2026-11-22: sarcasm, span, language, both sentiments and cues; 25
  sarcastic). With the opt-in toggle, the review form starts from them. On Gio's 31 labeled items they agree with him on 29 (26 under
  the strict v1 draft). The ethics text should mention
  that item text is processed by a third-party model (§3.1).
- **Evaluation-only annotators:** the adviser's suggested `nimble:9b` can be
  run on the gold items (`uyam annotate evaluate --model nimble`, then
  `eval-report`). Its votes live in a separate table and can never change the
  dataset. Not run yet: the model must be pulled on the remote box first.

## 7. Manuscript passages affected (tsis, `CS 197A CS Thesis Template (LaTeX)-…/`)

Do not edit these until `dataset-v2` is announced here. Listed so the update
is quick when it comes:

| Where | Currently says (v1) | Will need |
|---|---|---|
| `03_methodology.tex` §Data Collection, ¶2 | 20,573 records, 8,920 authors, 1 Aug – 4 Sep 2026, per-subreddit counts | v2 counts and window, two sources |
| `03_methodology.tex` §Data Collection, ¶1 | "a Python pipeline" | mention the archive source and the 3-day reply horizon |
| `03_methodology.tex` commented oversampling note | "implemented but not executed" | not needed at collection (every post collected); applied at annotation only if Gio adds marker items (§5) |
| `03_methodology.tex` §Preprocessing + Table `tab:exclusions` | 6 reasons, 3,149 excluded, 17,424 eligible | add `phatic`; v2 counts |
| `03_methodology.tex` commented "Departures" list | "about five weeks instead of three months" | ~5 months: no longer a departure |
| `01_introduction_05scopeandlimitations.tex` ¶3 | temporal context bounded by the window | still true, window wider; add archive caveats |
| `04_results.tex` temporal-channel discussion | coverage measured on v1 (~35–37% within 48 h) | re-measured on v2 by leische |
| `03_methodology.tex` §Annotation (human subset) | "same definitions given to the models"; stratified sample | written guideline (`docs/GUIDELINE.md`), redrawn sample, blind-first assist step (§6.1–6.2) |

## 8. leische's requests to uyam (`leische/suggestions/UYAM_CHANGE_BRIEF.md`, 2026-09-17)

| Item | Request | Status in uyam |
|---|---|---|
| U1 | ship `sarcastic_majority` + `sarcasm_vote_share` | **done** (export + card `label_sets`; trial: 2,747 majority positives, 1,712 rows differ, κ 0.545, matching leische) |
| U2 | adjudicator remit (`sarc-v3`, adjudicator only) | not started; needs Gio's choice of option |
| U3 | written guideline + gold subset to ~300 | guideline draft v1 written (decisions D1–D5 open); gold queue redrawn, 31/300 labeled |
| U4 | export `context.rendered` verbatim | not started |
| U5 | more author history for temporal context | **done** (backfill complete; card `temporal_context_coverage.by_window`: 48 h 46.5%, 7 d 64.9%, 30 d 82.1%) |
| U6 | make `needs_human` fire | not started |
| U7 | deterministic `labels.language_lid` | not started |
| U9 | keep the 15,000 labeled rows; flag `phatic` instead of dropping | **done** (120 flagged, 0 labels changed) |
| U8 | run or drop keyword oversampling | 57 trial posts re-tagged `natural` (**done**); search step redundant; marker target-selection is Gio's call (§5) |

## 9. Requests to the tsis and leische sessions

Both sessions read this file at session start (their tsis-first rules), so
these requests reach them without uyam editing their repos.

- **To tsis and leische, from Gio (2026-11-22): end every chat with ALL of
  Gio's open action items.** Every open step on his end, not just this
  chat's: your repo's to-dos, the adviser's items in §10 that you own, and
  anything another repo is waiting on. Group them by urgency, and say which
  ones an agent can do once he says go. Please add this to your RULES.md.
  It is uyam RULES §1.3.
- **To tsis and leische: ask Gio before any LLM run on `100.102.10.69`**
  (the 24 GB box is his gaming PC). It is uyam RULES §6.
- **To tsis:** put the adviser's action items (§10) into tsis's own HANDOFF
  / next steps. They drive the manuscript revisions: the terminology change
  (Filipino, code-switching), the second annotator, ethics and API status,
  and the guideline as an appendix.
- **To leische:** the model-side adviser items in §10 (rows 3, 4, 6 and 7)
  are yours. Their data is already in `dataset-v2`. Record them in
  LEISCHE_LATEST.md with a status.
- **To leische (2026-11-22): report results by `media_context`.** 43% of the
  labeled rows reply to image or video posts that nobody saw. Please report
  every condition overall **and on the text-only slice**
  (`media_context.text_only`: v2 has 7,798 rows and 565 positives; per-row
  flags for v2 are in `media_context-v2.jsonl`, and v3 rows carry them
  inline). If RQ1/RQ2 hold on text-only, the blind spot didn't drive them.
  Optional ablation: a "context incomplete" tag in the conversational input.
  Judge it on the text-only slice and the gold labels, because sarcasm is
  ~2× more common under image posts and the tag could be a shortcut. See
  docs/dataset-contract-leische.md.

### 9.1 Media the model never sees (found 2026-11-22)

Image-only posts were excluded as targets (manuscript filter iv), but their
comments were not. In the 15,000 v1/v2 labels:

| The item replies to | Rows | Share | Sarcastic |
|---|---|---|---|
| a text post | 8,143 | 54% | 7.3% |
| an image / gallery post | 5,652 | 38% | 14.0% |
| a video post | 910 | 6% | 19.3% |
| a link (news) post | 295 | 2% | 12.5% |

63% of the sarcastic labels sit under image, video or link posts. The
annotators and the model saw only those posts' titles. Model F1 against the
LLM labels is not lower there (leische v1, `pred_tuned`: image posts ~0.36–0.38,
text posts ~0.29–0.31), but the labels share the same blindness, so F1 can't
reveal the problem. The human gold κ per slice (in the card) can. **For the
manuscript:** say this in §1.5 / filter (iv), report the text-only slice
(7,798 rows), and name image captions (a vision model as a text context
channel) as future work. The dataset was not cut to text-only: that would
remove most positives and change the population.

## 10. Adviser action items (Gio's October meeting; next meeting 2026-11-25, before the final defense)

| # | Item | Owner | Where it stands (2026-11-22) |
|---|---|---|---|
| 1 | Finish the gold set (31 → 300) under a written guideline/codebook | Gio (uyam) | **On track.** `docs/GUIDELINE.md` v1.1 is the codebook: definition, Says/Means procedure, cue rules, 13 hard cases, span and unsure fields, decision log. The 300-item queue is drawn (62 ensemble-sarcastic) and 31 are labeled. To mark it final: D2, D3 and D5 |
| 2 | A second human annotator | Gio | **Deferred (Gio, 2026-11-22: doesn't fit the time).** It will be stated as a limitation: a single human annotator, so no inter-annotator κ. The partial substitute is D5, a blind re-label of the first 31 items after at least two weeks, for an intra-annotator κ. The adviser's "majority vote vs no-override of 3-0" question then rests on the single-annotator gold set and on the equal-size re-annotation (row 5) |
| 3 | Re-run the 3 main conditions on majority-vote labels | leische | **Data ready:** `labels.sarcastic_majority` is in v2 (U1; 2,747 positives vs 1,601 shipped) |
| 4 | Zero-shot LLM and simple text-concatenation baselines | leische | Not started. A zero-shot LLM scored against LLM-made labels is partly circular (more so if it shares a family with the annotators), so it should be scored mainly against the human gold and should use a model outside the annotator lineup. Running it needs Gio's OK (RULES §6) |
| 5 | Re-annotate with equal-size LLMs (remove model size as a hidden variable) | uyam (**decided by Gio 2026-11-22: yes, plus new months**) | **Conflicts with the frozen v2 labels, so it must be a separate version (`dataset-v3`); v1/v2 stay as they are.** Current lineup: qwen3:8b, SEA-LION v3 9B, gemma3:27b (plus a qwen3:32b adjudicator). Cheapest equal-size route: keep the two ~8–9B models' votes and replace only gemma3:27b with a ~9B annotator on all 15,000 items, using the same `sarc-v2` prompt. All three then fit the local 8 GB GPU, so the gaming PC isn't needed. Three binary votes always have a majority, so the sarcasm label needs no adjudicator (this fits row 3). Cost: one model × 15,000 items, to be timed with a 100-item pilot first. Needs Gio's go |
| 6 | RQ3 across all folds with proper controls | leische | leische has fold 0 only (`results/rq3-fold0.csv`). The human gold carries intended sentiment for a human check |
| 7 | Re-run on items with real context only (don't force retrieval) | leische | The data supports it. Conversational context exists for comments, not submissions. Author history within 48 h exists for 46.5% of v2 items (64.9% at 7 d). uyam can add per-row context flags to the export if leische wants them |
| 8 | Reddit API access | Gio | Application pending. **uyam collection is frozen until it is approved** (RULES §5.10). If approved, re-fetching the labeled ids through the API is a provenance and deletion check worth reporting |
| 9 | Ethics clearance | Gio | Pending. It must cover: the headed-browser scrape with rotating proxies (the v1 core, all 15,000 labeled items), the Arctic Shift archive (v2 history), pseudonymization, Claude processing the gold text, the second annotator, and retention and deletion (§3.1) |
| 10 | Release the dataset only after the labels are validated and the manuscript is revised | all | **In place:** RULES §5.11. Exports go to leische only, by direct transfer, never GitHub |
| 11 | Manuscript: "Taglish" → "Filipino"; define code-switching (including intra-sentential) | tsis | Note on the data: the label `taglish` means the target "meaningfully mixes both" (neither language ≥ ~90% of its words). It is assigned per target, so it covers **both intra- and inter-sentential** switching without telling them apart. Recommended: keep the three profiles and rename them in the text (English / Filipino / Filipino–English code-switched). The Chapter 4 by-language result depends on keeping them separate. If the manuscript needs the intra-sentential share, uyam can measure it with the token-level LID |

## 11. Change log

- **2026-11-22 (late night)**: Every exported row (v3 on) now carries
  `media_context`, and `media_context-v2.jsonl` covers v2. The finding is in
  §9.1 and the request to leische in §9. Also: guideline L4b, and full-label
  Claude suggestions with the review-form rules. No labels changed.
- **2026-11-22 (night)**: dataset-v3 set up. The DB is forked (the 15,000
  v2 items + 6,000 new Jun/Jul/Oct/Nov items; qwen3 and SEA-LION votes
  kept, gemma3 and the 32B adjudicator dropped). Majority vote with a
  confidence tie-break, no adjudicator. The new annotator is
  Llama-SEA-LION-v3-8B (Llama 3.1 family, Filipino-tuned). Pilot on 100
  v2 items: 0 failures, 2.3 s per item, sarcasm agreement with qwen3 /
  gemma3 / SEA-LION 90 / 86 / 90%. On those 100 the v3 majority gives 10
  positives where v2 shipped 4: majority vote ships more positives than
  unanimity-or-adjudicate. The second human annotator is deferred (Gio:
  doesn't fit the time). The new items have more same-author history
  (≈58–64% at 48 h vs 46.5% for the v2 items). The gold set covers only the
  Aug–Sep items, which is a stated limitation.
- **2026-11-22 (evening)**: The adviser's action items were recorded with
  their status (§10), and requests to the tsis and leische sessions added
  (§9). New uyam rules: ask before any LLM run (the remote box is Gio's
  gaming PC); end every chat with all of Gio's action items; collection
  frozen until API access and ethics clearance; no release before
  validation.

- **2026-11-22 (later)**: `dataset-v2` exported by Gio (uyam `39b1500`) and
  received by leische (checksums verified; v2 runs paused at 1 of 11).
  Verified identical to v1 on all 15,000 labels. New uyam rule: every
  session pulls tsis first and reads LEISCHE_LATEST.md.
- **2026-11-22**: Guideline D1 decided (broad reading, tagged `D1`), and the
  guideline is now v1.1. The gold queue was redrawn with sarcastic strata ×2
  (62 of 300), and the assist suggestions were regenerated. The v2 export is
  now recommended without waiting: leische's LEISCHE_LATEST waits on uyam,
  so both repos were waiting on each other. A new leische brief
  (`docs/briefs/LEISCHE_BRIEF.md`) covers the bundle transfer (not GitHub)
  and how to unzip it without overwriting the v1 card. No labels or data
  changed.
- **2026-11-19 (night)**: A code review of the day's changes led to fixes.
  Every gold score now uses the blind first pass, `votes_seen` was added, and
  raw-JSONL rewrites now split on newlines only (the retag that already ran
  lost nothing). `docs/dataset-contract-leische.md` now documents all the v2
  export fields. No labels or data changed.
- **2026-11-19 (late)**: v2 prepared, trial-exported and checked against v1
  (same 15,000 items, 0 label changes). U1, U5, U8 and U9 done. Gold queue
  redrawn. Guideline draft v1, blind-first assist, and evaluation-only
  annotators (nimble) added. The real v2 export waits for leische's
  readiness.
- **2026-11-19 (evening)**: Added the ethics facts (§3.1; Reddit User
  Agreement clause verified) and the label-definition gap (§6.1).
- **2026-11-19 (later)**: Keyword searches stopped as redundant. The marker
  counts and the target-selection option are in §5.
- **2026-11-19**: Natural pass complete for all three subreddits (588,507
  records). Keyword searches restarted after a fix. Temporal coverage by
  history window (§2.1) and the length profile (§2.2) added.
- **2026-11-17 (evening)**: r/Philippines and r/CasualPH backfills finished.
  Briefs for the other repos are in uyam `docs/briefs/`:
  `LEISCHE_BRIEF.md` (join this status-file protocol, reply on U1–U8, get
  ready for v2) and `TSIS_BRIEF.md` (portable `_verify`, data-number check,
  v2 draft with placeholders).
- **2026-11-17**: The archive backfill resumed after stopping on 11-13
  (throttling, an archive outage, keyword jobs missing their date window;
  all fixed in `5b72a10`). This file was created.
- **2026-11-11**: Archive backfill started (Jun 1 → Nov 10, 300 posts/day).
  The live scraper was fixed for Reddit's JSON 403 and rate-limit/IP-block
  pages. Nov 8–10 was collected live.
- **2026-11-10**: Arctic Shift archive source added. `phatic` filter added
  (not applied). Proxy provider switched (Wolves → DataImpulse).
- **2026-09-15**: `dataset-v1` exported (what the manuscript uses).
