# Dataset Contract: uyam → leische

This document is the handoff contract between **uyam** (data collection +
annotation, this repo) and **leische** (the model repository for the thesis
*Context-Aware Sarcasm Detection in Code-Switching Social Media Posts*).
uyam's responsibility ends at the artifacts described here; leische must not
need to re-crawl Reddit or re-run annotation.

The model-side companion — architecture, notebook plan, feature-to-model
mapping, and upgrade recommendations — is
[leische-model-notebook-guide.md](leische-model-notebook-guide.md).

## 1. Artifacts and versioning

Produced by `uyam annotate export --version v1` under `data/annotated/`:

| File | Contents |
|---|---|
| `dataset-v1.jsonl` | One row per resolved annotation target (submission or comment) — the training dataset |
| `dataset-v1.parquet` | Same rows, flattened (nested objects as `*_json` string columns) |
| `corpus-v1.jsonl` | Pseudonymized dump of the FULL collected corpus — source for temporal context |
| `dataset_card.json` | Counts, label distributions, agreement statistics, model provenance, filter settings |

Versioning: the dataset version (`v1`) is bumped for any re-export with
different labels; `dataset_card.json` records the `prompt_version`
(annotation prompt contract), the `uyam_commit` hash and `created_at`.
Treat `(dataset_version, prompt_version, uyam_commit)` as the full identity
of the dataset.

**Only these files are the contract.** The CSV buttons in the Streamlit app
(`annotated-review.csv`, `uyam_export.csv`) are eyeballing aids: they carry
no context snapshot, no cues, no per-annotator confidence, no LID ratios and
no identity stamp. Do not build the model dataset from them.

## 2. Row schema (`dataset-v1.jsonl`)

Identity and metadata:

- `reddit_fullname` — stable unique id (`t3_…` submission, `t1_…` comment)
- `record_type` — `submission` | `comment`
- `subreddit`, `permalink`, `created_utc` (ISO-8601 UTC), `score`
- `submission_fullname` — thread id (equals `reddit_fullname` for submissions).
  **Use this for thread-level fold grouping** (see §7).
- `parent_fullname`, `depth` — thread structure
- `author_hash` — HMAC-SHA256 pseudonym; consistent across this dataset and
  `corpus-v1.jsonl`, meaningless outside them. Raw usernames never ship.
- `is_submitter` — comment author is the thread OP
- `sampling_strategy` — `natural` | `keyword_oversampled` (see §7),
  `matched_query_or_keyword`. Comments inherit their submission's value, so
  the column is never blank.
- `is_text_only` — false for submissions that are link/image posts with no
  body (thesis §3.2 excludes them at candidate selection; the flag lets you
  verify none slipped through and slice by it)

Text:

- `text` — the annotation target (comments: body; submissions: title + selftext)
- `title`, `selftext` — populated for submissions, null for comments

Labels (`labels`, the ensemble finals — the training targets):

- `sarcastic` — bool. Binary sarcasm label per the thesis's four operationalized cues.
- `language` — `english` | `tagalog` | `taglish`
- `literal_sentiment` — surface polarity (`positive`|`neutral`|`negative`)
- `intended_sentiment` — the sentiment actually meant. **This is the ground
  truth for the RQ3 sentiment evaluation**; for sarcastic rows it is typically
  inverted/shifted from `literal_sentiment`.
- `cues` — four booleans: `polarity_inversion`, `rhetorical_intent`,
  `contextual_incongruity`, `hyperbole`. Auxiliary; interpret only when
  `sarcastic` is true.

Reliability (`reliability`):

- `resolved_by` — `unanimous` | `majority` | `adjudicator` | `human`. How the
  final label was decided (see §3) — the JOINT resolution of all four labels.
- `per_label` — the same decision for EACH label separately:
  `{sarcastic, language, literal_sentiment, intended_sentiment}` →
  `unanimous` | `majority` | `adjudicator` | `human` | `unresolved`. Sarcasm
  is `unresolved` only under `--include-unresolved`; use this rather than
  `resolved_by` when a single label (e.g. language) is what you filter on.
- `sarcasm_votes` — e.g. `"3-0"`, `"2-1"` across the base annotators
- `n_annotators`, `mean_confidence` (weakly calibrated — prefer the vote
  pattern), `needs_human`
- `annotators` — the raw per-model labels (model key + digest, all labels,
  confidence, one-sentence rationale)
- `adjudicator` — the adjudicator's labels when the item was escalated, else null

`human_gold` — non-null only for the ~300-item stratified validation subset:
the native-speaker labels, shipped ALONGSIDE (not replacing) the ensemble
labels so leische can reproduce the human-vs-ensemble Cohen's kappa.
**Gold rows are evaluation-only: never train on them.** The subset is
stratified on `sarcastic × language × record_type × subreddit × split`, where
split rows (base annotators disagreed on sarcasm, later decided by the
adjudicator) are weighted ×2 (`review.gold_split_oversample`).

Auxiliary signals (`aux`):

- `tx_sentiment` — cardiffnlp/twitter-xlm-roberta-base-sentiment probabilities
  (model-independent literal-sentiment vote)
- `lid` — two-stage fastText token-ratio language ID: `auto_label` (alias
  `language`), `en_ratio`, `tl_ratio`, `other_ratio`, message-level
  `confidence`. The ensemble `labels.language` is the primary language label;
  `aux.lid` exists for the LID-validation analysis (thesis §3.2.1). The card
  reports `lid_vs_ensemble_language` and, once gold labels exist,
  `lid_vs_human_gold_language` accuracy. Each annotator's own language vote
  is in `reliability.annotators[].language`.

`context` — see §5. `provenance` — `prompt_version`, `dataset_version`.

## 3. How the labels were produced

1. Three LLM annotators labeled every eligible item independently at
   temperature 0 with schema-constrained JSON output, each seeing the thread
   context (§5): `qwen3:8b` and SEA-LION v3 9B (Filipino-tuned) on an 8 GB
   local GPU, `gemma3:27b` on a 24 GB box. Field order in the output schema
   forces cue analysis before the sarcasm verdict.
2. Aggregation: sarcasm requires **unanimity**; any split (incl. 2-1)
   escalates. 3-class labels take a ≥2 majority; three-way ties escalate.
3. Escalated items were re-annotated **blind** (no access to the three votes)
   by the adjudicator `qwen3:32b`; its labels replace all labels on those rows
   (`resolved_by = "adjudicator"`).
4. Adjudications below the confidence floor (0.6) went to a human queue; human
   labels are final (`resolved_by = "human"`).
5. A stratified ~300-item gold subset (sarcasm × language × record_type ×
   subreddit) was labeled blind by the author (native Tagalog/English speaker).

Agreement statistics in `dataset_card.json`: per-label Fleiss' kappa over the
three base annotators, Krippendorff's alpha (nominal, missing-tolerant),
Cohen's kappa human-vs-ensemble on the gold subset, and fastText-vs-ensemble
language agreement. Cite these as the dataset's reliability evidence.

## 4. Provenance / reproducibility caveats

- Every per-model annotation carries the Ollama model digest, endpoint,
  server version, decoding options, and prompt version. That tuple — not
  re-running — is the reproducibility contract: temperature-0 decoding is
  near- but not bit-identical across Ollama versions, GPU kernels, and quants.
- The annotation prompt (guideline encoding the four thesis cues + five
  Taglish few-shot examples) lives at `src/uyam/annotate/prompts.py`, hash-
  guarded per `prompt_version`.

## 5. Conversational context (precomputed — use as-is)

`context` embeds **exactly what the annotators saw**, snapshotted at
annotation time:

```json
{
  "source":       "annotator_snapshot",
  "submission":   {"reddit_fullname", "author_hash", "created_utc", "title", "selftext"},
  "parent_chain": [{"reddit_fullname", "author_hash", "is_submitter", "depth", "created_utc", "text"}, ...],
  "replies":      [{...same shape...}]
}
```

`source` is always `annotator_snapshot` for annotated rows (`missing` would
mean the snapshot was lost — treat as a bug, never rebuild from the corpus).

- `parent_chain` is oldest→newest, capped at 6 ancestors (nearest 2 kept
  fullest); `replies` are up to 3 direct replies to the target; long selftexts
  are head+tail truncated. Truncation marks: `…` and `[…]`.
- For submission targets, `submission` is null (the target IS the submission)
  and `replies` hold its first top-level comments.
- Feed this object to the conversational-context encoder. Do not rebuild
  context from `corpus-v1.jsonl` for training the conversational channel —
  the labels were conditioned on THIS snapshot.

## 6. Temporal and retrieval context (leische builds these)

**Temporal** (author-history) context is reconstructable, not precomputed:
group `corpus-v1.jsonl` rows by `author_hash`, sort by `created_utc`, and take
the author's posts/comments preceding the target's timestamp (the thesis's
exponential time-decay weighting is a model-side concern). `author_hash` is
HMAC-keyed and consistent across both files, so author histories are exact
within this dataset.

**Retrieval** context (sarcastic / non-sarcastic exemplars) is built at train
time from the labeled rows. To avoid leakage, restrict each fold's retrieval
pool to that fold's TRAINING rows only — never retrieve from validation/test.

### Dataset card additions

- `readiness` — the numbers the model-side gate checks: exported rows,
  sarcastic positives, the smallest `language × sarcastic` cell, gold items
  labeled and their sarcasm Cohen's κ, rows with a snapshot context.
- `collection_window` — first/last `created_utc` of the corpus and of the
  exported rows. Thesis §1.4 scopes "at least three months"; the card makes
  the actual span explicit so the limitation is stated with the data.
- `temporal_context_coverage` — under the thesis §3.4 rule (≤5 same-author
  posts within 48 h before the target, from the collected corpus): share of
  exported rows with ≥1, ≥3 and the full 5 history posts. Two thirds of rows
  currently have none; a wider *subreddit* crawl is the only way to raise it
  — per-author history cannot be back-filled because usernames are hashed
  before storage (§3.1), by design.
- `label_distributions.is_text_only`, `sarcastic_per_label_resolution`.

## 7. Recommended evaluation protocol

- Stratified 5-fold cross-validation, stratifying jointly on
  `labels.sarcastic` × `labels.language` (per the thesis).
- **Group folds by `submission_fullname`** so no thread is split across folds
  — thread-mates share conversational context and would leak.
- Keep `sampling_strategy = "keyword_oversampled"` rows out of any fold or
  metric that claims to reflect the natural label distribution; report their
  contribution separately (the dataset card breaks distributions down by
  strategy).
- Class weighting: inverse-frequency on the sarcasm label (expected minority
  ~5–15% under natural sampling; check `label_distributions` in the card).
- Optional sample weighting: down-weight `resolved_by = "adjudicator"` rows
  or `sarcasm_votes = "2-1"`-escalated rows in sensitivity analyses.

## 8. Known limitations

1. Labels come from an LLM ensemble, not a human committee; the gold-subset
   Cohen's kappa is the human-alignment evidence. Report it alongside results.
2. Self-reported LLM confidence is weakly calibrated; use `sarcasm_votes` /
   `resolved_by` as the reliability signal.
3. fastText LID is weak on very short informal text; the ensemble language
   label is primary, `aux.lid` is a cross-check.
4. Three Filipino subreddits only; findings may not transfer to other
   platforms/communities.
5. Deleted/removed content and link-only posts were excluded at candidate
   selection (`dataset_card.json` → `counts.candidates` lists every exclusion
   reason and count).
6. Sarcasm class imbalance is expected; do not rebalance the test folds.

## 9. Re-running / extending

All commands are resumable and idempotent (`uyam annotate status` shows
progress):

```
uyam scrub-authors                          # once: strip legacy plaintext usernames
uyam collect --source shreddit --oversample-only   # keyword_oversampled threads
uyam annotate pipeline                      # everything below, to pipeline.target_items
uyam annotate gold-sample                   # (pipeline draws it) then label in the Streamlit tab
uyam annotate aggregate && uyam annotate export --version vN

# pass by pass, if you prefer:
uyam annotate index && uyam annotate select && uyam annotate lid
uyam annotate sentiment --target 0          # local GPU, BEFORE Ollama passes
uyam annotate run --annotator gemma3 --target 0        # remote box (parallel with below)
uyam annotate run --annotator sealion --annotator qwen3 --target 0   # local, least-done first
uyam annotate aggregate
uyam annotate adjudicate                    # remote box
uyam annotate aggregate
```

## 10. Handoff status (uyam, 2026-09-08)

Response to `UYAM_HANDOFF.md` (written by leische against the Streamlit CSV
downloads, not against `dataset-*.jsonl`):

| item | status |
|---|---|
| H1 gold subset + κ | Pipeline draws a 300-item gold sample weighted ×2 on split votes; Gio labels it in the Annotation Review tab; `aggregate` + `export` then fill `human_gold` and the card's `gold_vs_ensemble_cohen_kappa`. Not automatable. |
| H2 context snapshot | Was already in the JSONL export; now stamped `context.source = "annotator_snapshot"` with `depth` / `is_submitter` / `created_utc` per entry. |
| H3 sealion "failures" | Not failures: zero sealion failure records. Those rows were simply not yet annotated by sealion (least-covered model). `annotate pipeline` runs it first to the shared target. |
| H4 adjudication | Runs inside `annotate pipeline`; rows it decides carry `resolved_by = "adjudicator"`. `reliability.per_label` added for per-label resolution. |
| H5 cues | Always in the JSONL export (`labels.cues`, per-annotator `cues`). |
| H6 LID | Always in the JSONL export (`aux.lid`); `auto_label` alias added, plus `lid_vs_human_gold_language` in the card. |
| H7 plaintext authors | Fixed at the source (no `author` field on records); `uyam scrub-authors` rewrites legacy files; the browser shows hashes. |
| H8 collection window | Cannot be fixed by code: `/new` reaches only days back and per-author back-fill is impossible with hashed identifiers. Card now reports `collection_window` + `temporal_context_coverage`; the thesis must state the actual span. |
| H9 oversampling | Comments inherit `sampling_strategy`; `uyam collect --oversample-only` / sidebar checkbox runs the keyword searches on their own (before, they only ran after an un-stopped natural pass). |
| H10 image-only posts | Already excluded at candidate selection (`link_post`, 0 in the annotated set); `is_text_only` flag added to every row. |
| H11 export identity | Always in `dataset_card.json` (`dataset_version`, `prompt_version`, `uyam_commit`, `created_at`). |
| H12 per-annotator confidence | Always in the JSONL export (`reliability.annotators[].confidence`). |

New scraped data only requires re-running from `index`; existing annotations
are keyed by `(reddit_fullname, model, prompt_version)` and are never redone.
Changing the prompt requires bumping `PROMPT_VERSION` (prompts.py +
annotation.yaml), which re-annotates everything under the new version.
