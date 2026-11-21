# Brief for a session in the `leische` repository: dataset-v2

Paste everything below the line as the prompt in a Claude session opened in
the leische repo (github.com/mcfcs/leische), on the device where leische is
developed. Written 2026-11-22 by a uyam session. It replaces the 2026-11-17
brief: leische has since joined the protocol (LEISCHE_LATEST.md) and replied
on U1–U8. Live data status is in `uyam/UYAM_LATEST.md`. Trust that file over
this one if they disagree.

---

## Context

Three repos under github.com/mcfcs talk only through GitHub: **uyam** (data
and annotation, exports the dataset), **tsis** (the manuscript, the source of
truth for numbers and method), and **leische** (this repo, the model). Follow
leische's own RULES.md, HANDOFF.md and session-start protocol first. This
brief adds one job: **get leische onto dataset-v2 without touching any v1
result.**

What v2 is (from uyam's UYAM_LATEST.md and docs/dataset-contract-leische.md):

- **The same 15,000 labeled rows with identical labels** (verified against
  v1: 0 label changes). That was leische's request, so v1 vs v2 isolates the
  effect of extra author history.
- The corpus grows from 1 Aug → 4 Sep 2026 to **1 Jun → 10 Nov 2026** (588,507
  records). The default `corpus-v2.jsonl` holds only the exported rows plus
  every record by their authors (`corpus_dump_scope: labeled_authors`,
  ~281k records), which is what the temporal channel needs.
- Temporal coverage under the 48 h rule rises from ~36.7% to 46.5% of rows
  (7 d 64.9%, 30 d 82.1%). This is in the card's
  `temporal_context_coverage.by_window`.
- New fields: `labels.sarcastic_majority`, `labels.sarcasm_vote_share` (U1),
  `flags.phatic` (U9: 120 rows, labels kept), and `human_gold.first_pass` /
  `sarcastic_span` / `unsure` / `assist_seen` / `votes_seen`. To reproduce
  the gold κ, use `first_pass` when present and leave out `votes_seen` rows.
- **No `keyword_oversampled` rows**: the 57 mis-tagged trial posts were
  re-tagged `natural` (U8), and no oversampling was run. Keep the masking
  code anyway (task 1c); it must be a no-op on v2.
- The gold subset is still being labeled by Gio (31 of 300 when v2 was
  exported). A later re-export will carry more gold labels with the same
  training labels.

## Getting the data (it is NOT on GitHub)

uyam is a **public** repo and its `data/` is gitignored, so the dataset
never goes through git. Gio transfers the bundle `uyam-dataset-v2.zip` from
the uyam PC, either:
- by opening `http://100.71.83.125:8501` in a browser on this device (the
  uyam Streamlit app over Tailscale) → Annotation tab → **Download bundle**,
  or
- by Taildrop: on the uyam PC, `tailscale file cp
  data\annotated\uyam-dataset-v2.zip <this-device>:`; here, `tailscale file
  get <dir>`.

If the zip isn't on this device yet, **stop and ask Gio for it**. Don't try
to fetch it some other way.

**Unpacking: do not unzip into `data/`.** The bundle contains a file named
`dataset_card.json`, which would overwrite the v1 card. Instead:

```bash
mkdir -p data/incoming-v2 && unzip uyam-dataset-v2.zip -d data/incoming-v2
cd data/incoming-v2 && sha256sum -c SHA256SUMS.txt && cd -
```

Then check the identity in `data/incoming-v2/dataset_card.json`:
`dataset_version == "v2"`, `prompt_version == "sarc-v2"`, and the
`uyam_commit` named in UYAM_LATEST.md. Only after task 1b gives the card a
per-version path, move the files into place, for example `data/dataset-v2.jsonl`,
`data/corpus-v2.jsonl` and `data/dataset_card-v2.json`. Leave the v1 files
untouched.

## Tasks, in order

### 1. The v2-readiness code changes (HANDOFF "What is unfinished" item 6)

Do them exactly as leische's HANDOFF orders them, each with `--until 6` and the
§9/§10 smoke tests before its commit:

a. Dataset-versioned output: `results/v2/`, `cache/checkpoints/v2/`, the
   top-level CSVs and figures, plus an identity check in both exploration
   notebooks' skip-if-done. v1 paths and names don't change; tsis
   `_verify/recompute.py` reads them by name.
b. `dataset_version` selectable (env / `--set`) with a per-version card
   path, demo included.
c. Mask `keyword_oversampled` rows out of test/val metrics, the threshold and
   ECE when `natural_only_metrics` is on, and record the choice in `run.json`.
d. Streaming corpus loader: line by line, four fields, labeled authors only,
   dedup on `reddit_fullname`, time-sorted per-author arrays with binary
   search.
e. Fix the stale coverage comments.

Before the first v2 run: validate the v2 rows against v1 by `reddit_fullname`.
They should be the same 15,000 ids with identical `labels.sarcastic`,
`language` and both sentiments. Any difference is a uyam bug, so stop and
report it in LEISCHE_LATEST.md rather than training.

### 2. The v2 runs (LEISCHE_LATEST.md §5 plan)

A new fold file `folds-v2-<hash>.json` is written automatically. Register its
SHA-256 in `tools/check_repo.py` in the same commit, and never edit either
fold file. Run `1_baseline`, `5_conv_temp` and `8_full` at 3 seeds, plus
`3_temp`, `7_temp_ret`, the `temporal-unbounded` variant, and the coverage
measurement. Results go under `results/v2/` only. Because the labels are
identical, any v1 → v2 change in the temporal conditions is the effect of
the added author history. Report it with the same significance tests as v1,
and report honestly when it is null.

### 3. Report

- Update LEISCHE_LATEST.md: v2 results in a section of their own, marked as
  v2, with v1 numbers unchanged. Mirror it to tsis as usual.
- Update HANDOFF.md and push. uyam reads leische from `origin/main`.

## Ground rules

- Never edit uyam or tsis. The one exception is the LEISCHE_LATEST.md mirror
  in tsis.
- v1 stays frozen: its results, fold file and the numbers tsis verifies.
- Gold labels are evaluation-only. Never train on rows with `human_gold`.
- Every F1 is still agreement with the LLM-ensemble label (keep the stamp).
- U2 (the adjudicator's definition) stays deferred until the gold subset
  reaches ~150 items, as leische's 2026-11-17 reply recommends. Note: the
  human gold guideline now uses the broad reading (wry hugot, self-pity and
  playful praise can count, tagged `D1`), which bears on that decision.
