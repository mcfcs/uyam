# Leische — Model Development Guide

**Context-Aware Sarcasm Detection in Code-Switching Social Media Posts**
Companion to [dataset-contract-leische.md](dataset-contract-leische.md) (the data contract).
Copy this file into the new `leische` repository as its starting `docs/MODEL_PLAN.md`.

> **Status: notebook-building phase — do NOT train for results yet.**
> The current export (`dataset-v1`) is a 100-item pilot with only 8 sarcastic
> items and no human gold subset. Everything below is written so the notebooks
> can be built and smoke-tested NOW on the pilot data, and simply re-pointed at
> `dataset-v2` when the full annotated corpus lands. See §10 for the readiness
> checklist that gates real training.

---

## 1. What leische must produce (from the thesis)

| RQ | Deliverable |
|---|---|
| RQ1 | Context-aware sarcasm model vs context-agnostic XLM-R baseline, F1-compared |
| RQ2 | Ablation over the three context sources (8 conditions, §7.3) |
| RQ3 | Two-stage sentiment evaluation: pre- vs post-sarcasm-aware interpretation (§8) |

The thesis architecture is the **baseline commitment** — build it exactly as
specified first (§4). The upgrades in §9 are additions layered on top, each
kept behind a config flag so every claim in the manuscript remains reproducible
with upgrades off.

---

## 2. Inputs: every feature the dataset provides

One row per target from `dataset-v2.jsonl` (schema details in the contract doc).
How each field is used by the model:

| Field | Role in leische |
|---|---|
| `text` (+ `title`/`selftext` for submissions) | **Target input** to the encoder |
| `labels.sarcastic` | **Primary training label** (binary) |
| `labels.language` (english/tagalog/taglish) | Stratification key; disaggregated reporting; optional auxiliary head (§9.3) |
| `labels.literal_sentiment` | Auxiliary signal: polarity-shift feature/objective (§9.3) |
| `labels.intended_sentiment` | **RQ3 ground truth** (stage-2 sentiment target) |
| `labels.cues.{polarity_inversion, rhetorical_intent, contextual_incongruity, hyperbole}` | Auxiliary multi-task heads (§9.3) — free supervision the thesis's own guideline defines |
| `reliability.sarcasm_votes` ("3-0"/"2-1") | Soft labels / sample weights (§9.1) |
| `reliability.resolved_by` (unanimous/majority/adjudicator/human) | Sample weighting + robustness slices ("does performance hold on unanimous-only?") |
| `reliability.mean_confidence` | Weak signal only — vote pattern is the trusted one |
| `reliability.annotators[].{labels, rationale}` | EDA + error analysis; rationales are text you can mine for cue keywords |
| `human_gold.*` (once labeled) | **Held-out sanity set** — never trained on (§10) |
| `aux.tx_sentiment.{p_pos,p_neu,p_neg}` | RQ3 stage-1 classifier candidate; optional 3-dim feature |
| `aux.lid.{en_ratio, tl_ratio}` | Optional scalar features; code-switching-degree analysis |
| `context.submission.{title, selftext}` | **Conversational context** item 0 |
| `context.parent_chain[]` (text, depth, is_submitter, created_utc, author_hash) | **Conversational context** items 1..P (ordered oldest→newest) |
| `context.replies[]` (≤3) | **Conversational context** items P+1..P+R (reception evidence) |
| `author_hash` + `created_utc` (+ `corpus-v2.jsonl`) | **Temporal context**: author history reconstruction |
| `submission_fullname` | **Fold grouping key** (thread-level splits — mandatory) |
| `depth`, `is_submitter`, `score`, `record_type` | Structural scalar features (optional; score is post-hoc information — see caution §11.5) |
| `sampling_strategy` | Natural vs keyword_oversampled — test-fold hygiene (§7.2) |
| `provenance.prompt_version`, `dataset_version` | Log in every results table |

**Rule: the model consumes the embedded `context` object, not a re-crawl.**
Labels were conditioned on exactly that snapshot.

---

## 3. Repository & notebook layout

Notebook-first, but importable: logic lives in a small package, notebooks stay
thin and re-runnable. This is what makes the ablation matrix feasible later.

```
leische/
  data/                      # copied uyam exports: dataset-vN.jsonl, corpus-vN.jsonl, dataset_card.json
  src/leische/
    data.py                  # load/validate rows against the contract; splits
    contexts.py              # conversational / temporal / retrieval assembly
    encoders.py              # XLM-R wrapper, pooling, projection
    model.py                 # ContextAwareSarcasmModel (all stages, ablation flags)
    train.py                 # fold loop, loss, early stopping, seeds
    evaluate.py              # metrics, ablation tables, RQ3 evaluation
    config.py                # one dataclass: every switch in this document
  notebooks/
    00_environment.ipynb     # installs, GPU check, seed/determinism check
    01_data_eda.ipynb        # §5 — run on EVERY new dataset version
    02_context_assembly.ipynb# §6 — build + inspect the three context tensors
    03_baseline_xlmr.ipynb   # target-only fine-tune (the RQ1 baseline)
    04_context_model.ipynb   # full architecture, single-condition training
    05_ablation_matrix.ipynb # 8 conditions × 5 folds (§7.3)
    06_rq3_sentiment.ipynb   # two-stage sentiment evaluation (§8)
  configs/                   # yaml per experiment; git-committed
  results/                   # per-run CSV + config snapshot + dataset_version
```

Environment: Python 3.11+, `torch` (cu124 build on the 8 GB machine),
`transformers`, `datasets`, `scikit-learn`, `sentence-transformers` (retrieval),
`pandas`, `matplotlib`. Pin exact versions in `requirements.txt` on day one;
record `dataset_version` + `prompt_version` + git commit in every results file.

**Hardware reality (8 GB VRAM):** `xlm-roberta-base` (~279 M params) trains in
fp16 with batch 8–16 + gradient accumulation. `xlm-roberta-large` does NOT fit
comfortably — treat large as a stretch goal via LoRA (§9.4) or the 24 GB box
if it can take a CUDA workload alongside Ollama duty.

---

## 4. Baseline architecture (thesis §methodology — build this exactly)

### Stage 1 — Context-specific encoding (shared encoder)
One shared `xlm-roberta-base` encodes every text unit separately: the target,
each conversational item, each temporal item, each retrieval exemplar.
Mean-pool token states (more stable than CLS for short noisy text — verify
both in `03_baseline`), then a shared linear projection to `d_model` (start
256). Shared weights are a deliberate small-data choice from the thesis
(~5,000 rows) — do not give each channel its own encoder.

Truncation budgets (fit an item in one pass): target 192 tokens, context items
96 each, submission selftext 128.

### Stage 2 — Conversational + temporal interaction (target-conditioned attention)
Standard scaled dot-product attention, target embedding **t** as the query:

```
c_conv = Attn(Q = W_q t,  K/V = conversational item embeddings)
c_temp = Attn(Q = W_q t,  K/V = temporal item embeddings · exp(−λ·Δt_i))
```

- Conversational items: `[submission, parent_1..parent_P, reply_1..reply_R]`,
  each with an added **role embedding** (submission / ancestor / reply) and
  **is_submitter flag** — cheap and preserves thread structure the flat list loses.
- Temporal decay: `Δt_i` = time between the author's i-th prior post and the
  target, in **days**; λ initialized to 0.1 and **learnable** (thesis states
  fixed decay; learnable-λ is a strict generalization — report both, one flag).
- Empty channels (no parents; author with no history): output a zeros vector
  and let the Stage-4 gate learn to suppress — plus a learned "missing" embedding
  as an alternative to try.

### Stage 3 — Retrieval-guided expectation modeling
Two exemplar banks built **from the training fold only**: sarcastic and
non-sarcastic labeled targets. For each input target:

1. Retrieve top-k (k=5) nearest sarcastic + top-k nearest non-sarcastic
   exemplars by cosine over frozen sentence embeddings
   (`paraphrase-multilingual-MiniLM-L12-v2` to start; §9.5 for upgrades).
2. Encode retrieved texts through the shared Stage-1 encoder.
3. Target attends to each bank separately; concatenate and project:
   `c_ret = W_r [Attn(t, S_sarc) ; Attn(t, S_nonsarc)]`

The contrast between "what sarcastic things look like" and "what sincere
things look like" is the expectation-violation signal the thesis describes.

**Leakage rules (hard requirements):** banks exclude (a) anything outside the
training fold, (b) anything sharing the target's `submission_fullname`.
Rebuild banks per fold. Cache embeddings once per dataset version.

### Stage 4 — Gated context fusion (GMU-style)
```
g = softmax( W_g [c_conv ; c_temp ; c_ret] )        # 3 scalars, sum to 1
c_fused = g1·c_conv + g2·c_temp + g3·c_ret
```
Per-instance gates — the mechanism that suppresses uninformative sources.
**Log the gate values per instance**: the gate distribution by language and
by record_type is itself thesis-discussion material (e.g. "temporal context
gets down-weighted for authors with <3 prior posts").

### Stage 5 — Classification
`logits = MLP([t ; c_fused])` (one hidden layer, GELU, dropout 0.2).
Loss: cross-entropy with **inverse-frequency class weights** computed on each
training fold (never globally). The context-agnostic baseline for RQ1 is the
same encoder + `MLP([t])` — keep them in one model class behind ablation flags
so the comparison never drifts.

Training defaults to start from: AdamW, encoder lr 2e-5 / heads lr 1e-4,
linear warmup 10%, ≤10 epochs, early stopping on validation F1 (patience 3),
fp16, grad-clip 1.0, seed set {13, 42, 7} — report mean over seeds × folds.

---

## 5. Notebook 01 — EDA (rerun on every dataset version)

Minimum panels: label counts overall and by `language × sarcastic` (the
stratification cells — flag any cell < 10); `sampling_strategy` breakdown;
`resolved_by` / `sarcasm_votes` distribution; text-length histograms (token
counts under the Stage-1 budgets); context coverage (% with parents, mean chain
length, % with replies); **temporal coverage**: distribution of prior-post
counts per author from the corpus dump — this decides whether temporal context
is viable at all; agreement stats echoed from `dataset_card.json`; 20-row
manual read of sarcastic items with all three model rationales.

## 6. Notebook 02 — Context assembly

Build and cache the three channels as tensors/arrays keyed by
`reddit_fullname`, with a printed audit for 5 random items showing exactly
which texts entered each channel. Temporal: take up to K=10 prior posts by
`author_hash` with `created_utc` strictly before the target's (from
`corpus-v2.jsonl`); store Δt in days. Retrieval: embed all targets once,
verify the leakage filter drops same-thread neighbors.

---

## 7. Evaluation protocol (thesis-fixed)

### 7.1 Splits
`StratifiedGroupKFold(n_splits=5)` — stratify on `sarcastic × language`,
**group on `submission_fullname`** (thread-mates share context; splitting a
thread across folds is leakage). Within each fold: 80/10/10
train/val/test per the thesis (carve val out of the train side, grouped the
same way). Fix the fold assignment once per dataset version and save it to
`results/folds-v{N}.json` — every experiment reuses the same folds.

Consider also auditing author overlap across folds (`author_hash`): report it;
if a few authors dominate, add author to the grouping key as a robustness run.

### 7.2 Test-fold hygiene
`sampling_strategy == "keyword_oversampled"` rows may appear in training folds
but must be **excluded from any metric claiming the natural distribution** —
report main tables on natural-only test rows, oversampled contribution
separately.

### 7.3 Ablation matrix (RQ2 — 8 conditions)
| # | conv | temp | ret |
|---|---|---|---|
| 1 (baseline) | – | – | – |
| 2 | ✓ | – | – |
| 3 | – | ✓ | – |
| 4 | – | – | ✓ |
| 5 | ✓ | ✓ | – |
| 6 | ✓ | – | ✓ |
| 7 | – | ✓ | ✓ |
| 8 (full) | ✓ | ✓ | ✓ |

Disabled channels are removed from the gate softmax (renormalize over active
ones), not zero-filled. Metrics: F1 (primary), precision, recall, accuracy —
mean ± std across the 5 folds. Add a **significance test** the thesis doesn't
specify but examiners will ask for: paired bootstrap or approximate
randomization between condition 8 and condition 1 predictions, plus McNemar
for the headline pair.

### 7.4 Diagnostics worth logging from day one
Per-fold confusion matrices; F1 disaggregated by `language`, `record_type`,
`resolved_by` (is the model only right on easy unanimous items?); gate-value
distributions; calibration (reliability diagram + ECE — matters for RQ3).

---

## 8. RQ3 — two-stage sentiment evaluation (Notebook 06)

Ground truth: `labels.intended_sentiment`. Comparison:

- **Stage 1 (pre-sarcasm):** a sentiment classifier applied directly to the
  target. Two candidates: (a) `aux.tx_sentiment` probabilities already shipped
  (zero work, model-independent), (b) a small sentiment head fine-tuned on
  `literal_sentiment`. Report (a) as primary — it is external and unbiased.
- **Stage 2 (post-sarcasm):** for targets the sarcasm model flags sarcastic,
  reinterpret sentiment using a head over `[t ; c_fused]` trained on
  `intended_sentiment`; non-flagged targets keep the stage-1 prediction.

Metrics: macro-F1 and accuracy, disaggregated by language and by
gold-sarcasm label. The interesting cell is *sarcastic rows where
`literal ≠ intended`* — that is where sarcasm-awareness must show its value;
report that slice explicitly (the dataset carries both labels precisely for this).

---

## 9. Beyond the thesis — recommended upgrades (each one flag, each an ablation row)

Ordered by expected value-for-effort on a small noisy dataset:

### 9.1 Label-quality-aware training  *(highest priority — near-free)*
The dataset tells you how certain each label is; use it.
- **Sample weights** by provenance: unanimous 1.0, majority 0.9,
  adjudicator 0.7, human 1.0 (tune on val).
- **Soft labels**: target = vote share (3-0 → 0.95/0.05, adjudicated 2-1 →
  0.75/0.25) with label smoothing semantics.
- Robustness slice: train on all, evaluate on unanimous-only vs all — a large
  gap means label noise dominates.

### 9.2 Class-imbalance handling beyond weighted CE
With ~8–15% positives: try **focal loss** (γ=2) vs weighted CE, and a
`WeightedRandomSampler` targeting ~25–30% positives per batch. Keep weighted
CE as the thesis-reported number; report the better variant as an improvement row.

### 9.3 Multi-task auxiliary heads  *(uses features already in the dataset)*
Shared representation, small heads, summed losses (aux weights ~0.2–0.3):
- **4 cue heads** (`polarity_inversion` etc.) — the thesis operationalizes
  sarcasm through these cues; making the model predict them injects the
  annotation guideline into the representation and gives per-cue error analysis.
- **Polarity-shift head**: predict `literal_sentiment` vs `intended_sentiment`
  mismatch (the inversion signal directly).
- **Language head** (english/tagalog/taglish) — cheap regularizer; also
  enables per-language calibration.

### 9.4 Small-data fine-tuning hygiene
Freeze embeddings + bottom 4–6 XLM-R layers first (unfreeze-all as ablation);
layer-wise lr decay (0.9); **LoRA/adapters** (r=8 on attention) as the
low-VRAM path — also how `xlm-roberta-large` becomes feasible on 8 GB.
R-Drop or simple dropout-ensembling if variance across seeds is ugly.

### 9.5 Retrieval upgrades
Swap MiniLM for `intfloat/multilingual-e5-base` (stronger multilingual
retrieval); ablate k ∈ {3,5,10}; **SupCon loss** on target embeddings
(sarcastic vs not) so retrieval and classification geometry align. If retrieval
looks noisy in EDA, try exemplar centroids ("prototype" vectors) instead of
top-k — more stable with few positives.

### 9.6 Encoder alternatives (encoder ablation table)
XLM-R base is the thesis commitment. Worth one comparison run each:
`microsoft/mdeberta-v3-base` (frequently outperforms XLM-R at same size),
`jcblaise/roberta-tagalog-base` (Tagalog-native; weaker English side —
interesting per-language behavior). Keep the main narrative on XLM-R;
present alternatives as "the pipeline generalizes across encoders".

### 9.7 LLM zero-shot baseline row
You already run qwen3/gemma3 via Ollama in uyam. Add a no-training baseline:
zero-shot sarcasm classification of test folds (target-only, and
target+context variants). It contextualizes the trained model's value and
directly supports the thesis claim that purpose-built models beat general LLMs
— or honestly reports otherwise. Careful: the dataset's labels come from
*different prompts and an ensemble*, so a single LLM zero-shot row is not
label-circular, but note the shared-family caveat (qwen3-8b annotated;
use gemma3 or a model family NOT in the annotator pool for this row).

### 9.8 Calibration + thresholding
Temperature-scale on validation; pick the F1-optimal threshold on val (don't
default to 0.5 with 10% positives); report ECE. RQ3 stage-2 quality depends
on which items get flagged — a badly calibrated flag ruins the sentiment story.

---

## 10. Data-readiness gate (when training becomes legitimate)

Do not report any number from training until ALL of:

- [ ] Full corpus annotated under **sarc-v2** and exported as `dataset-v2`
- [ ] **≥ 400–500 sarcastic positives** (with ~10% base rate that means
      ~4–5k resolved items; keyword-oversampled collection is enabled in uyam
      to help). Below that, 5-fold CV on the positive class is ±noise.
- [ ] **Gold subset labeled** (~300 items in the uyam review tab) and
      human-vs-ensemble Cohen's κ reported in `dataset_card.json` — this number
      goes in the thesis before any model result does.
- [ ] Every `language × sarcastic` stratification cell ≥ 10 items
- [ ] Fold file frozen and committed

**What to do meanwhile (all legitimate now):** build notebooks 00–02 fully on
the pilot export; implement the model and run the two smoke tests —
(1) overfit 16 rows to ~zero loss (architecture sanity), (2) one full
5-fold dry run at tiny settings to prove the harness end-to-end. Mark every
pilot-derived number as SMOKE in outputs.

## 11. Pitfalls checklist

1. **Retrieval leakage** — banks from train fold only, same-thread excluded (§4.3).
2. **Thread leakage** — group folds by `submission_fullname`, no exceptions.
3. **Oversampled rows in natural metrics** — filter by `sampling_strategy` (§7.2).
4. **Gold subset touched by training** — it is evaluation-only, forever.
5. **`score` as a feature** — upvotes accrue *after* posting (post-hoc signal
   unavailable at prediction time) and correlate with reception; if used at
   all, report with/without. Same logic applies to replies — they are
   legitimate here only because the thesis defines the task as post-hoc thread
   analysis, but state that framing explicitly in the manuscript.
6. **Positive-class variance** — always mean ± std across folds × seeds;
   single-run F1 on ~80 test positives swings wildly.
7. **Context drift** — never rebuild conversational context from the corpus
   dump; the labels saw the embedded snapshot.
8. **Version mixing** — never mix `dataset-v1` (sarc-v1) rows with v2 rows.

---

## 12. Suggested build order

1. `00` env + `01` EDA on pilot export *(half a day)*
2. `data.py` + contract validation + frozen folds logic *(day)*
3. `02` context assembly with audits *(1–2 days; temporal needs the corpus dump)*
4. `03` baseline XLM-R + smoke tests *(day)*
5. `04` full architecture, gates logged, ablation flags *(2–3 days)*
6. `05` ablation harness + significance tests *(day)*
7. `06` RQ3 evaluation *(day)*
8. Then wait for the §10 gate → real runs → upgrade rows from §9.

Total: roughly 1.5–2 focused weeks to a fully smoke-tested pipeline that is
one config change away from real training when the data is ready.
