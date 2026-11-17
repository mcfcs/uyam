# Brief for a session in the `tsis` repository — from uyam

Paste everything below the line as the prompt in a Claude session opened in
the tsis repo (github.com/mcfcs/tsis). Written 2026-11-17 by a uyam session.
tsis's own `RULES.md` and `UYAM_LATEST.md` were added the same day
(commit `a55d261`), so read those first.

---

## Context

tsis is the thesis manuscript. The data comes from **uyam** (`../uyam`) and
the results from **leische** (`../leische`, developed on another device). Each
publishes a status file that tsis reads: `UYAM_LATEST.md` (synced into this
repo by uyam) and, soon, `LEISCHE_LATEST.md` (leische has been asked to add
it). `RULES.md` §1 describes the session-start check: fetch both siblings,
list new commits, pull only when safe.

The manuscript describes uyam's **dataset-v1** (20,573 records, 1 Aug –
4 Sep 2026) and leische's v1 results, and that is still correct. uyam is
building **dataset-v2** (window 1 Jun – 10 Nov 2026, ~450k+ corpus records,
a new `phatic` filter, keyword oversampling actually run). It isn't
exported yet, and leische hasn't re-run on it.

## Ground rules

- Follow tsis `RULES.md` and `GUIDE.md`.
- **Don't change any number in the manuscript in this session.** v2 isn't
  final, and Chapter 4 must stay on v1 until leische re-runs. Prose
  restructuring with v1 numbers kept is fine only if Gio asks.
- Never edit uyam or leische. Read them via `git show origin/main:<path>`
  when their local clone isn't clean on `main`.
- Commit and push each task separately; no AI attribution.

## Tasks, in priority order

### 1. Make `_verify/recompute.py` runnable on both devices

`RESULTS` is hard-coded to the leische device
(`C:\Users\Admin\OneDrive\Documents\GitHub\leische\results`). Make it
configurable without changing any computation:

- Priority order: a `--results <path>` argument, then a `LEISCHE_RESULTS`
  environment variable, then `../leische/results` relative to the repo root,
  then the current hard-coded path as a last fallback.
- Fail with a clear message if the directory or an expected file is missing,
  and print which path was used at the top of `REPORT.md`.
- Re-run it against leische's `main` results. On the uyam PC the `../leische`
  clone is on an old branch with someone's uncommitted change, so don't pull
  it. Use a separate worktree instead, which leaves it untouched:
  `git -C ../leische fetch origin && git -C ../leische worktree add ../leische-main origin/main`,
  then `--results ../leische-main/results`. Acceptance:
  still **171 passed, 0 mismatched**, with the same figures as the current
  `REPORT.md`. If anything differs, stop and report. Don't "fix" numbers.

### 2. Add a data-side check for the v1 figures

`_verify/` checks the model numbers but not the data numbers in §3.1–§3.2
(20,573 records; 1,954 / 18,619; 8,920 authors; per-subreddit 8,583 / 8,405
/ 3,585; Table `tab:exclusions` 1,507 / 896 / 340 / 278 / 116 / 12, totals
3,149 and 17,424; 15,000 annotated; 10.7% sarcastic). Add
`_verify/data_card.py`. It should read uyam's v1 dataset card
(`../uyam/data/annotated/dataset_card.json`, which exists only on the uyam
PC because `data/` is not in git) and compare each figure, writing
`_verify/DATA_REPORT.md`. Add the rows to `_verify/MAPPING.md`. If the card
isn't available on the device, skip with a message rather than fail.
Report any mismatch to Gio; don't edit the text.

### 3. Prepare (don't apply) the v2 update

Using `UYAM_LATEST.md` §7 (the affected-passages table), draft the v2
changes in a **separate file**, e.g. `_drafts/v2_data_update.md`. Give the
exact passages, proposed wording, and placeholders like `{{v2.corpus_records}}`
instead of numbers:

- §3.1 Data Collection: two sources (live scrape, Arctic Shift archive), the
  window, the same HMAC hashing across sources, the ≥3-day reply horizon,
  archive scores being snapshots (not used as features).
- §3.2 Preprocessing: the `phatic` reason (definition: short
  thanks/congrats/greeting-only text; irony markers veto; phatic items an
  annotator called sarcastic went to human review) and the updated exclusion
  table.
- The commented oversampling note (now executed) and the commented
  "Departures from the Proposal" list (the window is no longer five weeks).
- Scope and limitations ¶3 (temporal context bounded by a wider window).
- Ethics / data-source wording for using a third-party Reddit archive.

Follow GUIDE.md in the draft (acronyms, citations), so applying it later
is mechanical once uyam announces v2 and leische re-runs.

### 4. Record the protocol in a handoff

Create `HANDOFF.md` in tsis (current state + dated session log, like the
uyam and leische ones). Note which uyam and leische commits were read, what
was verified, and what's waiting on v2. Reference it from `CLAUDE.md`.

## Done when

- `recompute.py` runs on this PC and the leische device; REPORT still
  171/171.
- `data_card.py` + `DATA_REPORT.md` exist (or a clear note on why the card
  wasn't readable here).
- The v2 draft exists with placeholders only; the manuscript is unchanged.
- `HANDOFF.md` exists; everything is pushed.
