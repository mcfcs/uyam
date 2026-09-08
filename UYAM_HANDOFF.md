# uyam → leische handoff

What the model repository needs from the data repository, ordered by how much
it blocks. Everything here was found by running the pipeline against the
current export (`data/annotated-review.csv` + `data/uyam_export.csv`,
`prompt_version = sarc-v2`, 6,650 annotated rows over a 20,573-row corpus).

`tools/build_uyam_export.py` already derives the dataset contract from those
two CSVs, so **none of these items block starting analysis** — they are the
gap between "the pipeline runs" and "the numbers are defensible".

Status of the §10 readiness gate on the current export:

```
[PASS] dataset-v2 under sarc-v2        dataset_version=v2, prompt_version=sarc-v2
[PASS] ≥400 sarcastic positives        403 positives
[FAIL] gold subset labeled + κ         n_gold_items=0, sarcastic κ=None      ← H1
[PASS] every language×sarcastic cell ≥10   min cell 58
[PASS] fold file frozen                results/folds-v2.json
```

---

## Blocking

### H1 — Human gold subset + Cohen's κ

**Nothing validates the labels right now.** The corpus is annotated entirely by
a three-model LLM ensemble; zero items carry a human label, so
`gold_vs_ensemble_cohen_kappa.n_gold_items = 0` and the readiness gate fails.

This matters more than usual because the measured ensemble agreement is weak:

| label | Fleiss' κ | Krippendorff's α |
|---|---|---|
| `sarcastic` | **0.386** | 0.387 |
| `literal_sentiment` | 0.641 | 0.638 |
| `intended_sentiment` | 0.568 | 0.565 |

κ = 0.386 on the *primary target label* is "fair" agreement. Three models
that disagree this much on sarcasm cannot themselves establish that the labels
are right — only a human sample can.

**Ask:** label ~300 items in the uyam review tab (stratified over
`language × sarcastic`, and deliberately over-sampling the 2-1 splits, which
is where the disagreement lives). Write the human labels into the export under
`human_gold` and the resulting Cohen's κ into `dataset_card.json`.

**Hard rule:** gold items are evaluation-only forever. The pipeline never
trains on them (MODEL_PLAN §11.4).

---

## High

### H2 — Conversational context is not exported

The export carries `post_title`, `parent_url`, `post_url` — but not the text
of the parent chain or the replies. Yet the annotator rationales clearly
reacted to thread context ("*given the context of a party serving*",
"*contradicts the context of being a budget-friendly option*"), so uyam
assembles a context block somewhere in the annotation prompt and then discards
it.

`tools/build_uyam_export.py` currently **rebuilds** conversational context by
joining `post_id` / `parent_comment_id` back through `uyam_export.csv`, and
stamps every row `context.source = "corpus_rebuild"`. That violates
MODEL_PLAN §11.7 ("never rebuild conversational context from the corpus dump;
the labels saw the embedded snapshot") and has to be disclosed as a limitation
until it is fixed.

**Ask:** emit, per annotated target, the exact context block the annotator
prompt was given:

```json
"context": {
  "source": "annotator_snapshot",
  "submission":   {"reddit_fullname": "t3_…", "title": "…", "selftext": "…"},
  "parent_chain": [{"reddit_fullname": "t1_…", "text": "…", "depth": 1,
                    "is_submitter": false, "created_utc": "…"}],
  "replies":      [{"reddit_fullname": "t1_…", "text": "…", "depth": 2,
                    "is_submitter": false, "created_utc": "…"}]
}
```

`parent_chain` oldest→newest, `replies` capped at 3 (thesis §3.4(1)).
Once `context.source == "annotator_snapshot"` the pipeline's §11.7 warning goes
away on its own — no other change needed.

### H3 — sealion failed on 24% of rows

`reason: sealion` is null on **1,606 of 6,650** rows. Those rows fall back to
two annotators, which is exactly why the export contains 1,280 `2-0` votes and
324 unresolvable `1-1` ties.

Downstream this shows up as 867 dataset rows marked
`reliability.resolved_by = "unanimous_partial"` (agreement among only the two
models that answered) — a weaker label than a genuine 3-0.

**Ask:** find out whether these are timeouts, context-length overflows, or
parse failures, then re-run sealion on the 1,606 rows.

### H4 — 1,795 rows have no sarcasm label at all

`resolved_by = "unresolved"` covers 2,565 rows; sarcasm specifically is
unresolved on **1,795** (all the `2-1`, `1-1` and `1-0` votes). There are
**zero** adjudicator rows in the export — `reason: adjudicator` is null
everywhere — so the adjudication stage described in the pipeline never ran.

Split votes are precisely where borderline sarcasm lives, so this is the
highest-yield source of additional positives: the dataset currently sits at
403 positives against a ≥400 floor.

**Ask:** run the adjudicator over the 1,469 `2-1` sarcasm splits (and the
`1-1` ties once H3 restores sealion), and set
`reliability.resolved_by = "adjudicator"` on the rows it decides.

Note for whoever does this: `resolved_by` in the CSV describes the *joint*
resolution of all four labels, not sarcasm alone. 770 rows are labelled
`unresolved` while their sarcasm vote did resolve. Consider exporting a
per-label resolution instead of one column.

---

## Medium

### H5 — Cue labels are not collected

MODEL_PLAN §9.3 wants the four cues the thesis's own annotation guideline
operationalises sarcasm through — `polarity_inversion`, `rhetorical_intent`,
`contextual_incongruity`, `hyperbole`. They are free supervision (the models
already reason about them in prose) and they enable per-cue error analysis.

They are absent, so `labels.cues` is `null` on every row and the multi-task cue
heads stay off.

**Ask:** have the annotation prompt emit the four booleans alongside the
existing labels.

### H6 — No language-identification output

Thesis §3.2.1 specifies a fastText token-level LID pass with ≥90% thresholds,
and promises to "report classification accuracy of the automatic step against
the manual labels". **That number cannot be computed** — the export carries no
automatic language label and no token ratios, only the ensemble's final
`language`.

There is also no per-annotator language vote, so `language` is the one label
with no inter-annotator agreement statistic (see the table in H1).

**Ask:** emit `aux.lid = {"auto_label": …, "en_ratio": …, "tl_ratio": …}` and a
per-annotator `language` vote.

### H7 — Author identifiers are not hashed

Thesis §3.1: "author identifiers will be anonymized via one-way hashing before
storage." The export's `author` column contains plaintext Reddit handles
(`AshenWitcher20`, `CoffeeonPineapple`, …); `author_status = "pseudonymized"`
is a claim the data does not support.

`tools/build_uyam_export.py` hashes them on the way in
(`sha256(salt + handle)[:16]`), but that is after the fact — the plaintext
still sits in the stored CSV.

**Ask:** hash at collection time in uyam, and drop the plaintext column from
the export.

### H8 — Collection window is 24 days, not three months

Thesis §1.4 scopes the study to "a time frame of at least three months".
The corpus effectively spans **2026-08-01 → 2026-09-04** (~5 weeks; one stray
2025-07 row), and the *annotated* rows span **2026-08-01 → 2026-08-24 — 24
days**.

This is the direct cause of the thin temporal channel. Measured on the current
dataset, under the thesis's own temporal rule (≤5 author posts within 48 h
before the target):

| coverage | share of rows |
|---|---|
| ≥1 temporal item | 34.5% |
| ≥3 temporal items | 10.8% |
| the full 5 | 5.3% |

Dropping the 48 h window only lifts ≥1 coverage to 45.7%. Two thirds of the
dataset will present an empty temporal channel no matter how the model is
built, so RQ2's temporal condition currently measures *data availability*, not
whether temporal context helps.

**Ask:** extend the collection window backwards. Even without new annotation,
back-filling `uyam_export.csv` with more history **per already-collected
author** would thicken the temporal channel for the existing labelled rows —
that is the cheapest single improvement available to RQ2.

### H9 — Keyword oversampling never ran

Thesis §3.1 promises "keyword- and heuristic-based oversampling of potentially
sarcastic threads … while preserving a natural distribution for the
non-sarcastic majority class". The export contains **zero**
`keyword_oversampled` rows — `sampling_strategy` is `natural` on submissions
and blank on all 6,234 comments.

The pipeline's §7.2 test-fold hygiene path (natural-only vs all-rows metrics)
therefore runs but has nothing to separate.

**Ask:** either run the oversampling pass, or drop the claim from §3.1. Also
populate `sampling_strategy` on comments regardless — a blank column is
indistinguishable from "not recorded".

### H10 — Image-only submissions are not filtered

Thesis §3.2 excludes "multimodal-only content (e.g., image-only posts)".
**14.2%** of corpus submissions have an empty `selftext` and a non-self URL —
they are image posts whose only text is the title. Some are in the annotated
set.

**Ask:** either filter them at collection, or flag them
(`is_text_only: false`) so the pipeline can slice them out and report the
effect.

---

## Low

### H11 — Export identity is not stamped

`dataset_card.json` needs `dataset_version`, `prompt_version`, `uyam_commit`
and an export timestamp; the CSVs carry only `prompt_version`. The pipeline
freezes its fold file against this identity and refuses to mix versions
(§11.8), so `uyam_commit = null` weakens that guard.

**Ask:** write a `dataset_card.json` next to the export.

### H12 — Per-annotator confidence is not exported

`confidence` is a single row-level mean, and it is present only on the 4,085
rows where all four labels resolved. The per-model confidences that produced
it are gone, which rules out confidence-weighted soft labels (§9.1).

**Ask:** carry `confidence` inside each annotator record.

---

## What the adapter derives today

For reference, `tools/build_uyam_export.py` reconstructs these from the CSVs —
all verified, none guessed:

| contract field | source | note |
|---|---|---|
| `submission_fullname` | `t3_` + `post_id` | fold grouping key |
| `author_hash` | `sha256(salt + author)[:16]` | see H7 |
| `created_utc`, `depth`, `is_submitter`, `score` | corpus join on `id` | 100% join rate |
| `reliability.annotators[]` | parsed from the `reason: <model>` prefix | `[sarcastic; literal→intended]`, **100% parse rate on all three models** |
| `reliability.sarcasm_votes` | `votes` | reconstruction from parsed votes matches the column exactly |
| `agreement.labels` | computed from the parsed per-model votes | Fleiss κ + Krippendorff α |
| `context.*` | corpus rebuild | **see H2** — not the annotator snapshot |
| `labels.cues`, `aux.*`, `human_gold` | `null` | H5, H6, H1 — never fabricated |

Rows dropped by the adapter: 1,795 with an unresolved sarcasm vote (H4) and
439 with an unresolved language vote (language is the stratification key and
the disaggregation axis for every research question). 6,650 → **4,416 rows,
403 positives, 679 threads**.

---

## uyam response — 2026-09-08

Most items above were measured on the two Streamlit CSV downloads, which are
not the dataset contract. `uyam annotate export --version vN` writes
`dataset-vN.jsonl` + `corpus-vN.jsonl` + `dataset_card.json`, and that export
already carried the context snapshot (H2), the four cues (H5), the LID ratios
(H6), per-annotator confidence (H12) and the identity stamp (H11). Point
`tools/build_uyam_export.py` at the JSONL. Per-item status is in
[docs/dataset-contract-leische.md §10](docs/dataset-contract-leische.md).

What changed in uyam for the rest: `reliability.per_label` (H4),
`context.source` (H2), `is_text_only` (H10), `aux.lid.auto_label` (H6),
plaintext authors removed at the source plus `uyam scrub-authors` (H7),
comments inherit `sampling_strategy` and `uyam collect --oversample-only`
(H9), gold sampling weights split votes ×2 (H1), and the card now reports
`collection_window`, `temporal_context_coverage` and a `readiness` block (H8).

H3 was a misread: sealion has zero failure records; those rows were not yet
annotated. `uyam annotate pipeline` runs every annotator to the same
12,000-item target (least-covered model first), then adjudicates (H4) and
draws the gold sample (H1).

