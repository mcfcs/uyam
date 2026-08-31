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
(annotation prompt contract) and the `uyam_commit` hash that produced it.
Treat `(dataset_version, prompt_version, uyam_commit)` as the full identity
of the dataset.

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
  `matched_query_or_keyword`

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
  final label was decided (see §3).
- `sarcasm_votes` — e.g. `"3-0"`, `"2-1"` across the base annotators
- `n_annotators`, `mean_confidence` (weakly calibrated — prefer the vote
  pattern), `needs_human`
- `annotators` — the raw per-model labels (model key + digest, all labels,
  confidence, one-sentence rationale)
- `adjudicator` — the adjudicator's labels when the item was escalated, else null

`human_gold` — non-null only for the ~300-item stratified validation subset:
the native-speaker labels, shipped ALONGSIDE (not replacing) the ensemble
labels so leische can reproduce the human-vs-ensemble Cohen's kappa.

Auxiliary signals (`aux`):

- `tx_sentiment` — cardiffnlp/twitter-xlm-roberta-base-sentiment probabilities
  (model-independent literal-sentiment vote)
- `lid` — two-stage fastText token-ratio language ID (`en_ratio`, `tl_ratio`,
  message-level confidence). The ensemble `labels.language` is the primary
  language label; `aux.lid` exists for the LID-validation analysis.

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
  "submission":   {"reddit_fullname", "author_hash", "created_utc", "title", "selftext"},
  "parent_chain": [{"reddit_fullname", "author_hash", "is_submitter", "depth", "created_utc", "text"}, ...],
  "replies":      [{...same shape...}]
}
```

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
uyam annotate index && uyam annotate select && uyam annotate lid
uyam annotate sentiment                     # local GPU, BEFORE Ollama passes
uyam annotate run --annotator gemma3        # remote box (parallel with below)
uyam annotate run --annotator qwen3         # local
uyam annotate run --annotator sealion       # local, after qwen3
uyam annotate aggregate
uyam annotate adjudicate                    # remote box
uyam annotate aggregate
uyam annotate gold-sample                   # then label in the Streamlit tab
uyam annotate aggregate && uyam annotate export --version vN
```

New scraped data only requires re-running from `index`; existing annotations
are keyed by `(reddit_fullname, model, prompt_version)` and are never redone.
Changing the prompt requires bumping `PROMPT_VERSION` (prompts.py +
annotation.yaml), which re-annotates everything under the new version.
