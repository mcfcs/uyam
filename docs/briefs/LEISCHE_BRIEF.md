# Brief for a session in the `leische` repository — from uyam and tsis

Paste everything below the line as the prompt in a Claude session opened in
the leische repo (github.com/mcfcs/leische), on the device where leische is
developed. Written 2026-11-17 by a uyam session. The live data status is in
`uyam/UYAM_LATEST.md`; read that rather than trusting the numbers here if
they disagree.

---

## Context

The thesis now spans **three** repos under github.com/mcfcs, and they talk
only through GitHub (leische is on a different device from the other two):

| Repo | Role |
|---|---|
| **uyam** | data collection + LLM annotation; exports the dataset leische trains on |
| **tsis** (new) | the LaTeX manuscript; its `_verify/recompute.py` recomputes the manuscript's numbers from `leische/results/` |
| **leische** (this repo) | the model, experiments and results |

uyam and tsis have adopted a shared protocol. Each repo publishes a status
file that the others read: uyam's is `UYAM_LATEST.md` (in uyam, mirrored
into tsis). Nobody edits another repo's code or prose. Requests travel as
briefs and status files. This brief asks leische to join that protocol and
to get ready for the next dataset version.

## Ground rules (keep leische's own RULES.md; these add to it)

- **Never edit uyam or tsis**, except one file: leische may write
  `LEISCHE_LATEST.md` at the root of tsis (task 1). No manuscript `.tex`,
  `.bib`, figures or `_verify/`.
- **Dataset-v1 stays frozen.** Nothing below changes any existing v1 result,
  fold file, or `thesis-rewrite/00_NUMBERS.md` entry. Everything for v2 is
  additive and keyed on the new `uyam_commit`.
- Commit and push straight to `main`, plain messages, no attribution
  trailers (leische's existing convention).

## Tasks, in priority order

### 1. Join the cross-repo protocol (do this first)

a. Add to leische `RULES.md` a session-start step:
   ```bash
   git -C ../uyam fetch origin && git -C ../uyam log --oneline -10 origin/main
   git -C ../tsis fetch origin && git -C ../tsis log --oneline -10 origin/main
   git -C ../uyam show origin/main:UYAM_LATEST.md
   ```
   Pull a sibling only if it's on `main` and clean, otherwise read it with
   `git show origin/main:<path>`. Clone them next to leische if they aren't
   there yet.

b. Create **`LEISCHE_LATEST.md`** at the root of leische, written for the
   thesis side, and keep it current with every change that affects results
   or the method. Mirror it verbatim to tsis's root (the only file leische
   writes in tsis):
   ```bash
   cp LEISCHE_LATEST.md ../tsis/ && git -C ../tsis pull --ff-only
   git -C ../tsis add LEISCHE_LATEST.md
   git -C ../tsis commit -m "docs: sync LEISCHE_LATEST.md from leische <sha>"
   git -C ../tsis push
   ```
   Suggested sections, mirroring `uyam/UYAM_LATEST.md`:
   - which dataset version and uyam commit the current results are on, and
     which fold file
   - headline results, each pointing to its `results/` file (what tsis may
     quote)
   - what changed since the last entry (new runs, conditions, method
     changes)
   - which manuscript passages that affects (tsis `CS 197A CS Thesis
     Template (LaTeX)-…/03_methodology.tex` §3.4–§3.5 and `04_results.tex`)
   - the status of leische's requests to uyam (`suggestions/UYAM_CHANGE_BRIEF.md`)
   - a dated change log

c. Add the protocol to leische `HANDOFF.md` (Current state) and `AGENTS.md`.

### 2. Reply to uyam on the U1–U8 brief (decisions needed)

`suggestions/UYAM_CHANGE_BRIEF.md` (2026-09-17) never reached uyam until now.
Status as of 2026-11-17 (uyam `UYAM_LATEST.md` §8):

| Item | uyam status |
|---|---|
| U1 majority label + vote share | not started |
| U2 adjudicator remit (`sarc-v3`) | not started; **needs an option chosen** (1: forbid overriding 3-0, or 2: widen the definition and re-adjudicate 4,886 rows) |
| U3 guideline + gold to ~300 | gold 31/300; guideline not written |
| U4 `context.rendered` | not started |
| U5 more author history | **in progress**, differently from the brief: a subreddit-wide archive backfill, not per-author (see task 3) |
| U6 `needs_human` | not started |
| U7 `labels.language_lid` | not started |
| U8 keyword oversampling | **in progress**, archive keyword searches over Jun 1 → Nov 10 |

Update `UYAM_CHANGE_BRIEF.md` with **current** priorities, given everything
learned since September (your HANDOFF already notes that U2 moves 1,146
labels). Say which items v2 must include, which can wait, and which option
you recommend for U2, with the evidence. uyam will read it from
`origin/main`. Put a one-line summary in LEISCHE_LATEST.md.

### 3. Get ready for `dataset-v2` (don't run on it until uyam announces it)

What's coming (provisional; final numbers in uyam's v2 `dataset_card.json`):

- **Corpus window** grows from 1 Aug → 4 Sep 2026 to **1 Jun → 10 Nov
  2026**. uyam's raw corpus is ~620 MB and ~450k+ records (v1's
  `corpus-v1.jsonl` was 20 MB / 20,573 records). r/Philippines and r/CasualPH
  are complete; r/OffMyChestPH is still collecting.
- **Labeled rows**: still the same 15,000 `sarc-v2` labels unless uyam ships
  U1–U2 changes. The new data mainly feeds the **temporal channel**
  (same-author history). v1 measured ~36.7% of rows with ≥1 prior post within
  48 h, against your U5 target of ≥60%.
- **New preprocessing filter `phatic`** (thanks / congrats / HBD-only text)
  removes ~107 items from eligibility. Some of the 15,000 labeled rows
  may drop out, and the export reports `counts.skipped_ineligible`.
- **Two collection sources**: the live page scrape (Aug–Sep, Nov 8–10) and
  the **Arctic Shift archive** (Jun → Nov). Archive `score` /
  `num_comments` are ingest-time snapshots. leische doesn't appear to use
  `score`; keep it that way. Archive comments cover **at least 3 days after
  each post**, so late replies can be missing in the corpus (labeled rows'
  conversational context is the annotator snapshot, unchanged).
- **`keyword_oversampled` rows** will exist for the first time. Your §7.2
  hygiene path already excludes them from natural-distribution metrics.
  Confirm it does with real rows. Note: 57 v1-era trial posts carry that tag
  but came from `/new`; uyam will report whether they're re-tagged.

Checks to do now, against v1, so v2 is a re-run and not a debugging session:

- Can the corpus loader and the temporal-history builder handle a ~30×
  larger `corpus-v2.jsonl` (memory, time)? Profile on a synthetic 30× file if
  needed.
- Does the §3 validator accept `counts.skipped_ineligible`, the `phatic`
  exclusion reason and `sampling_strategy = keyword_oversampled`?
- Do folds re-freeze automatically on the new `uyam_commit`, with v1 fold
  files untouched (RULES R1)?
- Plan which runs to repeat on v2: at least the temporal-channel conditions
  and the coverage measurement, since that's what the new data changes.

### 4. Keep tsis able to verify your numbers

tsis's `_verify/recompute.py` reads `leische/results/` directly (the
`ablation-*` dirs, `predictions.csv`, `fold_metrics.csv`, `significance.csv`,
`folds-v1-*.json`). Its last report: 171 passed, 0 mismatched.

- Don't rename or restructure those files for v1. Put v2 results alongside
  (e.g. a version suffix) and say so in LEISCHE_LATEST.md.
- Its results path is hard-coded to this device
  (`C:\Users\Admin\OneDrive\Documents\GitHub\leische\results`), which is why
  it can only run here. tsis will make it configurable. Tell tsis (in
  LEISCHE_LATEST.md) if the path ever moves.
- `thesis-rewrite/00_NUMBERS.md` is what tsis sessions quote. Keep it
  current, and mark v1 vs v2 clearly once v2 runs exist.

## Done when

- leische RULES/HANDOFF/AGENTS describe the protocol; `LEISCHE_LATEST.md`
  exists in leische and in tsis (identical), and both are pushed.
- `UYAM_CHANGE_BRIEF.md` has dated, current priorities and a U2 decision
  (or the evidence Gio needs to make it).
- A short v2-readiness note (loader scale, validator, folds, run plan) is in
  LEISCHE_LATEST.md and the HANDOFF session entry.
- No v1 result, fold file, or `00_NUMBERS.md` v1 figure changed.
