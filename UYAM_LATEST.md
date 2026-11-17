# UYAM_LATEST — data-side status for the thesis

What has changed in **uyam** (data collection and annotation) that the thesis
(**tsis**) needs to know about. The source of truth is this file in the uyam
repo; an identical copy sits at the root of tsis. Newest information first.

- **Last updated:** 2026-11-17 20:10, uyam `af861db` (plus this file's commit)
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
| Corpus records | 20,573 (1,954 submissions, 18,619 comments) | 423,433 so far (24,707 / 398,726), *provisional, still growing* |
| Distinct authors | 8,920 | 46,755 so far, *provisional* |
| Eligible after filters | 17,424 | not computed yet |
| Annotated items | 15,000 | 15,000 (same labels; new items optional) |
| Sarcastic | 1,601 (10.7%) | unchanged unless re-annotated |
| Preprocessing filters | 6 reasons (manuscript Table 3.1) | 7 reasons: adds `phatic` (§4) |
| Keyword oversampling | not executed | being run (§5) |
| Prompt / labels | `sarc-v2` | `sarc-v2` (no prompt change so far) |

## 2. Corpus right now (2026-11-17 18:05, *provisional*)

Update 20:05: **r/Philippines and r/CasualPH are complete for Jun 1 →
Nov 10** (the resume added 3,068 + 1,627 posts and 51,831 + 11,980 comments
on top of the table below). r/OffMyChestPH is collecting, and the keyword
searches run after it.

| Subreddit | Submissions | Comments | Span |
|---|---|---|---|
| r/Philippines | 20,870 | 372,191 | 2026-06-01 → 2026-11-10 |
| r/CasualPH | 2,706 | 18,823 | 2026-06-01 → 2026-11-10 (Aug 18 → Nov being filled) |
| r/OffMyChestPH | 1,131 | 7,712 | 2026-08-10 → 2026-11-10 (Jun → Aug not yet collected) |

By collection source: Arctic Shift archive 22,215 / 375,150; live Shreddit
scrape 1,429 / 12,216; early-August live scrape (runs predate manifests)
1,063 / 11,360.

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
executed". For v2 it **is being executed**. The archive backfill runs the 8
Taglish sarcasm-marker searches ("edi wow", "sana all", "wow naman", "ang
galing naman", "talaga lang", "ikaw na", "buti naman", "yan tayo eh") over
Jun 1 → Nov 10 once the natural pass finishes. Those rows are tagged
`keyword_oversampled` and must stay out of natural-distribution metrics.

Open issue: a 2026-11-10 scraper trial stored **57 posts tagged
`keyword_oversampled` that actually came from `/new`**. Gio decides whether
to re-tag them.

## 6. What hasn't changed

- Annotation prompt (`sarc-v2`), the three annotator LLMs, the adjudicator,
  and the 15,000 labels.
- Gold subset: **31 of 300** labeled by Gio (no Cohen's κ yet).
- Conversational context per labeled item is still the snapshot the
  annotators saw. New data adds **temporal** context (other posts by the same
  author), not new conversational context for existing items.

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
| U8 | run or drop keyword oversampling | **in progress** (§5) |

## 9. Change log

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
