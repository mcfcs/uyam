# UYAM_LATEST — data-side status for the thesis

What has changed in **uyam** (data collection and annotation) that the thesis
(**tsis**) needs to know about. The source of truth is this file in the uyam
repo; an identical copy sits at the root of tsis. Newest information first.

- **Last updated:** 2026-11-19, uyam `07211e2` (plus this file's commit)
- **Dataset the manuscript and all leische results use:** `dataset-v1`
  (`prompt_version = sarc-v2`, exported 2026-09-15). **Still current.**
- **Next dataset:** `dataset-v2`, **not exported yet**. The corpus is being
  extended (see below). Numbers marked *provisional* will change before v2.

> **For tsis editors:** don't put any *provisional* number into the
> manuscript. Chapter 4 is computed on v1, and mixing v1 results with v2
> corpus figures would make the chapters disagree. Update the manuscript once
> this file announces `dataset-v2` as exported, together with leische's
> re-run on it.

---

## 1. Dataset versions

| | `dataset-v1` (in the manuscript) | `dataset-v2` (in progress) |
|---|---|---|
| Collection window | 2026-08-01 → 2026-09-04 (~5 weeks) | 2026-06-01 → 2026-11-10 (~5 months), *provisional* |
| Corpus records | 20,573 (1,954 submissions, 18,619 comments) | 588,507 (40,125 / 548,382), *provisional*; collection complete |
| Distinct authors | 8,920 | 63,754, *provisional* |
| Eligible after filters | 17,424 | not computed yet |
| Annotated items | 15,000 | 15,000 (same labels; new items optional) |
| Sarcastic | 1,601 (10.7%) | unchanged unless re-annotated |
| Preprocessing filters | 6 reasons (manuscript Table 3.1) | 7 reasons: adds `phatic` (§4) |
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

## 4. Preprocessing change: `phatic` exclusion (code ready, not yet applied)

A new filter excludes short social-ritual items with nothing else in them:
happy-birthday, thanks, congrats, greetings, "commenting to follow". It
applies only when a phatic phrase matches, the item is ≤ 10 tokens, and ≤ 2
content tokens remain. Irony markers veto it ("edi congrats", "thanks for
nothing", "ikaw na"). Phatic items that any annotator already voted
sarcastic are **not dropped** but sent to human review.

Dry run on v1 data (2026-11-10): **107** items would be excluded (none labeled
sarcastic) and **21** would go to review. It hasn't been applied to the real
database yet, so manuscript Table 3.1 is still correct for v1.

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

The 57 posts from the 2026-11-10 Shreddit trial that are wrongly tagged
`keyword_oversampled` (they came from `/new`) are still to be re-tagged
`natural`. leische asks for this (its U8). It rewrites raw JSONL, so it waits
for Gio's yes.

## 6. What hasn't changed

- Annotation prompt (`sarc-v2`), the three annotator LLMs, the adjudicator,
  and the 15,000 labels.
- Gold subset: **31 of 300** labeled by Gio (no Cohen's κ yet).
- Conversational context per labeled item is still the snapshot the
  annotators saw. New data adds **temporal** context (other posts by the same
  author), not new conversational context for existing items.

### 6.1 Label definition gap (for the guideline, leische U3)

The `sarc-v2` prompt defines `sarcastic` for "the TARGET" (intended meaning
differs from literal, plus a cue). It never says whether a long post counts
as sarcastic when only part of it is. Each annotator LLM chose for itself,
which adds noise on long items. The gold guideline should fix the unit, e.g.
"sarcastic if any part of the target is sarcastic", optionally marking which
sentence. That gap is a limitation of the v1 labels.

## 7. Manuscript passages affected (tsis, `CS 197A CS Thesis Template (LaTeX)-…/`)

Do not edit these until `dataset-v2` is announced here. Listed so the update
is quick when it comes:

| Where | Currently says (v1) | Will need |
|---|---|---|
| `03_methodology.tex` §Data Collection, ¶2 | 20,573 records, 8,920 authors, 1 Aug – 4 Sep 2026, per-subreddit counts | v2 counts and window, two sources |
| `03_methodology.tex` §Data Collection, ¶1 | "a Python pipeline" | mention the archive source and the 3-day reply horizon |
| `03_methodology.tex` commented oversampling note | "implemented but not executed" | executed; tagged rows excluded from natural metrics |
| `03_methodology.tex` §Preprocessing + Table `tab:exclusions` | 6 reasons, 3,149 excluded, 17,424 eligible | add `phatic`; v2 counts |
| `03_methodology.tex` commented "Departures" list | "about five weeks instead of three months" | ~5 months: no longer a departure |
| `01_introduction_05scopeandlimitations.tex` ¶3 | temporal context bounded by the window | still true, window wider; add archive caveats |
| `04_results.tex` temporal-channel discussion | coverage measured on v1 (~35–37% within 48 h) | re-measured on v2 by leische |

## 8. leische's requests to uyam (`leische/suggestions/UYAM_CHANGE_BRIEF.md`, 2026-09-17)

| Item | Request | Status in uyam |
|---|---|---|
| U1 | ship `sarcastic_majority` + `sarcasm_vote_share` | not started |
| U2 | adjudicator remit (`sarc-v3`, adjudicator only) | not started; needs Gio's choice of option |
| U3 | written guideline + gold subset to ~300 | gold 31/300; guideline not written |
| U4 | export `context.rendered` verbatim | not started |
| U5 | more author history for temporal context | **in progress** via the subreddit-wide archive backfill (not per-author); coverage to be reported in the v2 card |
| U6 | make `needs_human` fire | not started |
| U7 | deterministic `labels.language_lid` | not started |
| U8 | run or drop keyword oversampling | search step redundant (natural pass complete); target-selection option open; 57 trial posts to re-tag (§5) |

## 9. Change log

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
