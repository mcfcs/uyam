# Session handoff

The chat-to-chat log for agents working in uyam. Read *Current state* and the
newest session entry before starting. Update all three sections before ending
a chat (see [RULES.md §1](RULES.md)).

> Not to be confused with [UYAM_HANDOFF.md](UYAM_HANDOFF.md), which holds the
> model repo's (leische's) requests to uyam and uyam's responses.

---

## Current state (as of 2026-11-22, end of session)

**Corpus (raw JSONL, `data/raw/`)**: **588,507 records** (40,125
submissions, 548,382 comments) from r/Philippines, r/CasualPH and
r/OffMyChestPH, **2026-06-01 → 2026-11-10**, complete (Arctic Shift archive
plus the Aug–Sep Shreddit core and Nov 8–10 Shreddit walks). Every record is
`natural`: the 57 mis-tagged trial posts (and 543 comments) were re-tagged on
2026-11-19 (backups in `data/raw-backup-20261119T030244Z/`, log
`data/retag-log-20261119T030244Z.json`).

**Annotation (`data/db/annotation.sqlite3`, prompt `sarc-v2`)**: re-indexed
and re-selected 2026-11-19: **512,471 eligible**, 1,417 unlabeled items
excluded as `phatic`, 120 labeled items flagged `phatic` (labels kept). The
15,000 aggregated items and their labels are **frozen** (Gio, for leische
U9): 1,601 sarcastic, unchanged and verified against the backup
`data/db/annotation-2026-11-19.bak.sqlite3`. Gold queue redrawn again on
2026-11-22 with split and shipped-sarcastic strata each ×2: 300 items, 62
ensemble-sarcastic, 31 labeled by Gio, 269 to do (backup
`annotation-2026-11-22-pre-gold-redraw.bak.sqlite3`). `assist_suggestions`
holds 300 Claude Code suggestions (`claude-code:claude-opus-5-5`,
`guideline-v1.1`, 24 sarcastic, 4 of them D1-tagged). On Gio's 31 items they
agree on 29. `eval_annotations` is empty because nimble hasn't been run.
The `human_reviews.votes_seen` and `first_reviewed_at` columns were added
(backup first: `data/db/annotation-2026-11-19-pre-votes-seen.bak.sqlite3`).

**Export (`data/annotated/`)**: **`dataset-v2` exported** by Gio on
2026-11-22 00:34 PHT (`uyam_commit` `39b1500`; `dataset_card-v2.json`,
`uyam-dataset-v2.zip`). It is verified identical to v1 on all 15,000 labels.
The card numbers match the trial (majority positives 2,747, rows differing
1,712, κ 0.545, 48 h coverage 46.5%), and the corpus dump has 281,275
records. leische received it and verified the checksums, and its v2 runs are
paused at 1 of 11. `dataset-v1` files and `dataset_card-v1.json` are kept;
`dataset_card.json` is now the v2 card.

**Known gaps the thesis must address**: data didn't come through Reddit's
official API (ethics reminder); gold κ on 31 items is −0.148; guideline
decisions D2, D3 and D5 are open (D1 broad, D4 done).

## In progress

**dataset-v3 full run STARTED 2026-11-22 02:50 PHT** (Gio's go). It is
detached: orchestrator pid 199228, lane job `annotation-v3-run-all-local`.
- Command: `uyam annotate pipeline --config-path config/annotation-v3.yaml
  --skip-prep --skip-gold`. Prep was skipped because the v3 copy is already
  indexed and selected, and a full LID pass over ~495k items would add RAM
  pressure next to Gio's apps.
- **Labeling is fast now** (2026-11-22). The main app's tabs are lazy, so
  only the open tab runs (`1294a88`), and a Review rerun dropped from ~10 s
  to 0.1 s. Before, every click re-ran all tabs, including Data Browser's
  whole corpus in ~4.5 GB on a machine swapping at ~85k pages/s. There is
  also a labeling-only site, `run-label-app.bat` → http://100.71.83.125:8502
  (`931192a`), and an opt-in "Claude's suggestion before I label" toggle
  (`8e1d38d`, recorded as `assist_before_first_pass`).
- **Review form (`bb73abc`):** Claude's take now carries every label (300
  full-label suggestions loaded: 25 sarcastic; vs Gio's 32, sarcasm 30/32 and
  language 30/32). The upfront toggle prefills the form from it. "Not
  sarcastic" disables the span and cues and makes intended = literal (S3)
  unless "differs" is ticked. New guideline gaps the agents raised:
  - S1 on long mixed targets: whole target or the sarcastic span?
  - Intended sentiment of D1 items: wry amusement → neutral, self-pity →
    negative?
  - L4 with a repeated particle ("po … po"): does it count twice?
  - A Cebuano + English target: there is no label for it (taglish was used).
  - Parody in someone else's voice; mock advice; feigned surrender.
- **Runs & Datasets** tab: every run side by side (the original v1/v2 run stays
  untouched in annotation.sqlite3 as the fallback), a label browser per run, and
  every exported version (v1, v2, ...). All read-only.
- Watch it in the app: Annotation tab → **Current annotation runs** (read-only,
  refreshes every 30 s; `7342bca`). Logs: `data/.v3-pipeline.log` and
  `data/.annotate-status/annotation-v3-run-all-local.log`. ETA after the first
  hour: ~33 h (SEA-LION's mean pace is 9.9 s/item, not the 5.7 s median).
- Order: llamasealion (20,900 left, ~27 items/min, ~13 h), then qwen3
  (6,000), then sealion (6,000). Roughly 27–30 h in total. Sentiment for the
  6,000 new items is done.
- It shares the laptop GPU with Gio's ComfyUI and GPT-SoVITS (manabi) and
  runs next to arbit (CPU/RAM). **Don't close those apps** (Gio).
- To stop: kill the orchestrator and the lane job. To resume: re-run the
  same command (finished items are skipped).
- Still to do before export: LID for the 6,000 new items (needs a
  `--target` option on `annotate lid`), the gold sync, `--retry-failed` for
  qwen3's 2 old failures, then aggregate and export. Gio is labeling the 269 gold items in the
main DB. leische's v2 runs are paused at 1 of 11 (watch tsis
LEISCHE_LATEST.md).

**dataset-v3 (`data/db/annotation-v3.sqlite3`, `config/annotation-v3.yaml`):**
- The fork of annotation.sqlite3 was made 2026-11-22 (`uyam annotate fork`,
  seed 13). It holds 21,000 pinned targets in `target_priority`: the 15,000 v2
  items (priority 2) plus 1,600 Jun / 1,600 Jul / 1,600 Oct / 1,200 Nov 1–10
  (priority 1).
- Kept votes: qwen3 14,998 and SEA-LION 15,000 (sarc-v2). New annotator
  `llamasealion` (Llama-SEA-LION-v3-8B q4_k_m) has 100 pilot votes.
- Majority vote with a confidence tie-break, no adjudicator, no remote
  endpoint.
- Before export: sync Gio's gold labels from the main DB, since the fork
  copied the 31 that existed then. That needs a small `sync-reviews` command,
  not built yet. Then `uyam annotate aggregate --config-path
  config/annotation-v3.yaml` and `uyam annotate export --version v3
  --config-path config/annotation-v3.yaml`.

## Next steps (for Gio unless marked)

These are also the to-dos in the Streamlit **Reminders** tab
(`config/reminders.yaml`), where Gio ticks them off. `data/reminders.json`
records which ones are done.

1. **Label the 269 gold items** in Annotation Review (guideline v1.1): blind
   first, write `D1` in notes for wry-pose items, then optionally reveal
   Claude's suggestion. D2, D3 and D5 are open but don't block labeling.
2. **Re-export after the gold labels** so leische gets the new
   `human_gold` labels. The training labels are unchanged, so leische's v2
   runs remain valid.
3. **nimble** on the remote box: `ollama pull nimble:9b`, then
   `uyam annotate evaluate --model nimble` and `uyam annotate eval-report`.
4. **Agent, after the export:** check the card against the trial numbers
   above, update UYAM_LATEST.md §1/§9, and mirror it to tsis. Once the gold
   set is labeled, re-export (same version label only if training labels are
   unchanged; human_gold changes alone don't change the training labels).
5. Thesis text (reminders in the *Thesis manuscript* section): Arctic Shift
   method and ethics, window and phatic filter, guideline and blind-first
   assist.
6. Optional: marker oversampling (1,148 items) and annotating new Jun–Nov
   items.

**Regenerating the assist suggestions** (after a guideline change): dump
the gold items (fullname, target, parent, submission title/body; no votes,
no labels) into batches of 60 under the scratchpad, give each to one agent
with only `docs/GUIDELINE.md`, and ask it for JSONL with exactly
`reddit_fullname, sarcastic, sarcastic_span, rationale, confidence`. Agents
must not open the DB or `data/guideline_examples.md`. Then run `uyam annotate
load-assist <files> --source claude-code:<model> --guideline-version <v>`.
It validates everything before writing and replaces rows by id.

**Shreddit walk works live (verified 2026-11-11).** `/r/<sub>/new.json`
still returns 403, so calendar mode scrolls the page itself. That needed
`cb0dc37` (one `evaluate_all` snapshot per page, feed-page waits, stop at
`--since`, keyword jobs really search, one Chrome profile per job) plus three
fixes found by live runs. `a63687c`: a step that finds nothing jumps to the
page bottom so the next feed page loads (without it every walk stopped at 27
cards), the rate-limit toast is waited out, and comment expansion uses 2 s
timeouts. `0e53b76`: Reddit's "blocked by network security" page is detected
and that proxy is dropped. `5c76e83`: the toast wait is capped at 360 s.
Live: all three walks reached Nov 8 (52 / 102 / 102 cards). The final
r/Philippines run stored 58 posts and 1,649 comments, waited out a 209 s
rate limit, and switched past 2 IP bans. `fe55611` also stops tests from
sharing the live scrape's control file and `collect.log`. The archive source
still covers everything up to ~2 days ago.

**Open questions / ideas for agents:**
- Records don't carry their source; it's only in the run manifest. If leische
  needs per-row provenance (e.g. to ignore archive scores), add a
  `source_type` column to the corpus dump, joined from manifests.
- `compute_agreement` (κ/α) still counts every annotated item, including ones
  a later filter made ineligible. Since U9, all 15,000 stay eligible, so this
  only matters if a future filter drops labeled items.

---

## Session log

### 2026-11-23 (later) — v3 complete; definition test result

**v3:** finished at 09:16 after 30.4 h.
- 21,000 aggregated: 4,785 unanimous, 16,214 majority, 1 unresolved (the qwen3
  failure item).
- 4,011 sarcastic: 17.9% on the 15k v2 items, 22.0% on the 6k extension.
- Not exported yet. Still to do: LID for the 6k, retry qwen3's 2 failures
  (an LLM run, so ask Gio), sync the gold labels into the v3 DB, aggregate,
  export.
- The pipeline's final hint line still says `--version v1` (cosmetic).

**Definition test** (`data/eval-report-guideline.md`, all three guide
evaluators 300/300, done 10:09):
- Guideline-majority κ vs Gio: 0.48 (all 111) and 0.64 (blind 32), against
  0.24 and 0.18 for the sarc-v2 v3 labels.
- llama-guide: blind κ 0.67 (sarc-v2: −0.18).
- qwen3-guide: 0.46 (sarc-v2: 0.39).
- sealion-guide gets worse: 0.32 against 0.46, with a 49% positive rate.
- Caveats: blind n=32; the assisted labels follow a guideline-based suggestion.
- Open decision for Gio and the adviser: a guideline-prompt re-annotation
  ("v4"), or report this as a finding. The adviser update in tsis now has
  question 8.

**Gold:** still 113/300 (80 assisted).

### 2026-11-23 — gold stats at 113; definition card; guideline-prompt test armed

**Context (Gio):**
- Asked for statistics with 113/300 gold items labeled, an LLM run update,
  and whether the annotators know the guideline.
- He is fine with Claude as a guide; he treats Claude-assisted labels as
  human-assisted.
- He wanted the definition (and what isn't sarcasm: hate speech, irony) on
  the website.
- ComfyUI may be closed.
- The guideline-prompt test is approved for after v3.

**Findings:**
- 80 of 113 gold first passes were labeled with Claude's take showing, and
  77 of those are identical to it on every field.
- κ vs shipped: 0.24 overall, −0.14 on blind first passes only.
- Gio says 9.9% sarcastic; the LLMs over-call relative to him (14 vs 6
  disagreements).
- Per-model κ vs Gio: SEA-LION 0.32, qwen3 0.22, gemma3 0.10, Llama 0.04.
- The LLM annotators use pre-guideline sarc-v2.
- SEA-LION runs 31% on the CPU. Closing ComfyUI changed nothing: the rest
  of the VRAM is GPT-SoVITS plus Windows.

**Done:**
- ComfyUI stopped (headless, via comfort\scripts\comfyui-headless.bat).
- `8cc7be0`: evaluators can use `prompt: guideline` (prompts_guideline.py,
  stored as guide-v1.1); qwen3-guide, llama-guide and sealion-guide are
  local. eval-report gains a blind-only table, and the card gains
  `sarcastic_blind_only`.
- `9df462e`: definition card on the labeling page; GUIDELINE H14 and the
  extended H10.
- `f8b2349`: .gitignore now covers all of data/.
- Post-v3 chain armed: PowerShell pid 91856 runs
  `data/.guideline-eval-after-v3.ps1`. It waits for orchestrator 199228,
  then runs the three guide evaluators and eval-report into
  `data/eval-report-guideline.md`; the log is `data/.guideline-eval.log`.

**Left open:** read the guideline-prompt report. Then the v3 wrap-up (LID
for the new items, retry qwen3's 2 failures, gold sync into v3, aggregate,
export).

### 2026-11-22 (late night) — media blind spot measured and flagged; labeling UX

**Context (Gio):** labeling felt slow; asked for a separate labeling site,
Claude's take before labeling with all fields, form rules, language rules,
and what the thesis says about images. On the media question: "do what is
apt".

**Done:**
- Speed and labeling UX:
  - `1294a88`: lazy tabs; a Review rerun went from ~10 s to 0.1 s.
  - `931192a`: labeling-only site on :8502.
  - `8e1d38d`, `bb73abc`: an opt-in Claude toggle that prefills the form
    from a full-label suggestion (recorded as `assist_before_first_pass`).
    "Not sarcastic" disables span and cues, and intended follows literal
    (S3).
  - 300 full-label suggestions loaded.
- Media:
  - Audit: 43% of the labels reply to image or video posts (63% of
    positives).
  - The manuscript excludes image-only posts as targets (filter iv, scope
    §1.5) but says nothing about their comments.
  - leische v1 F1 is not lower there (it shares the labels' blindness).
  - Decision: keep everything, flag it, slice it. No text-only rebuild,
    which would remove most positives.
  - `bbcfbfe`: `media_context` on every exported row, card stats per slice
    including gold κ, and `uyam annotate media-context --version v2` →
    `data/annotated/media_context-v2.jsonl` (written: 15,000 rows, text-only
    7,798 / 565 positives).
  - `cc35236`: contract, UYAM_LATEST §9 request and §9.1 finding, guideline
    L4b and the H13 scale note, plus a reminder to send the side file to
    leische.

- `2532110`: media indicator on the labeling page; on media items the Reddit
  link waits for the save (GUIDELINE 6.3b: label text-only, like the models).
  Pending gold: 132 of 266 hang on unseen media (114 image, 18 video).
- v3 check (2026-11-22 17:24): Llama-SEA-LION finished 21,000/21,000 with 0
  failures. qwen3 is on the 6,000 new items (~16/min); SEA-LION is next. ETA
  Mon 23 Nov ~15:20. Llama is sarcasm-happier (19.5%) than qwen3 and SEA-LION,
  but the v3 majority (17.9% on the 14,998 three-vote v2 items) ≈ v2's own
  annotator majority (2,747/15,000). The jump from v2's shipped 10.7% comes
  from majority vote, not from Llama.

**Left open:** Gio sends `media_context-v2.jsonl` to the leische machine.
leische reports text-only slices. The manuscript limitation goes in a tsis
chat.

### 2026-11-22 (night) — dataset-v3: equal-size re-annotation set up and piloted

**Decisions (Gio):**
- Re-annotate (leische retrains anyway). Keep the 15,000 and add about
  6,000 items from other months.
- Installing models on the laptop is OK, and so is a 100-item pilot there.
- The second human annotator is deferred (it doesn't fit the time).

**Done:**
- Model choice: Llama-SEA-LION-v3-8B-IT q4_k_m (Llama 3.1 family,
  Filipino-tuned, 4.9 GB) replaces gemma3:27b. The lineup is now 8B / 9B /
  8B across three families, two of them Filipino-tuned, all fitting the 8 GB
  RTX 4070. I checked ollama.com/aisingapore.
- `32f859a`: AggregationConfig (majority / confidence), lineup filtering in
  aggregation, agreement and export, the target_priority table, `uyam
  annotate fork`, an optional adjudicator, `config/annotation-v3.yaml`, and
  `tests/test_annotate_v3.py`.
- `00afe06`: the fork also drops older-prompt-version votes. The first
  fork had kept 117 sarc-v1 pilot votes, so it was re-made.
- `b0d64ac`: **a safety bug fixed before any v3 pipeline ran.** Pipeline
  child jobs didn't get `--config-path`, so a v3 pipeline would have run the
  default config: the main DB, gemma3 and the 32B adjudicator on the gaming
  PC. Jobs now inherit it and are prefixed `annotation-v3-`.
- Pilot of llamasealion on 100 v2 items: 0 failures, mean 2.32 s/item,
  sarcasm agreement with qwen3 / gemma3 / SEA-LION 90 / 86 / 90, and a 9%
  sarcastic rate. On those items the v3 majority has 10 positives against 4
  shipped in v2. The main DB was confirmed untouched.
- Measured: unlabeled items in every month have 58–64% same-author history
  at 48 h, against 46.5% for the v2 labeled items.
- `8548884`: UYAM_LATEST and reminders.

**Left open:** the full v3 run (Gio's go), the gold sync command for v3,
then the v3 aggregate and export.

### 2026-11-22 (evening) — adviser action items recorded; LLM-run and end-of-chat rules

**Context (Gio):** pasted his adviser's next steps and action items. He meets
the adviser on Wed 2026-11-25, before the final defense.

**Done:**
- `62168c0`: RULES. §1.3: every chat ends with ALL of Gio's open action
  items. §6: always ask before any LLM run, especially on 100.102.10.69
  (Gio's gaming PC; also noted in annotation.yaml). §5.10: collection frozen
  until API access and ethics clearance. §5.11: no release before
  validation.
- `23f36e5`: UYAM_LATEST §9 (requests to the tsis and leische sessions,
  including Gio's end-of-chat rule for their RULES) and §10 (every adviser
  item with its owner and status). Mirrored as tsis `e22cb4d`. Six new
  reminders.
- Memory: ask-before-llm-runs, end-with-action-items.

**How the adviser's items fit what exists:**
- Gold set, guideline, majority-vote labels, context-only data and the
  no-release rule are already supported.
- Second annotator: not supported yet. The DB keeps one human label per
  item, so it needs a per-annotator table and an annotator picker.
- Equal-size re-annotation: conflicts with the frozen v2 labels, so it
  would be a separate `dataset-v3`. The cheapest route replaces only
  gemma3:27b with a ~9B model, runs entirely on the local GPU, and uses
  majority vote with no adjudicator.
- "Taglish" → "Filipino": a wording change only. The `taglish` label covers
  both intra- and inter-sentential switching.

**Left open:** everything in the Reminders tab's *Adviser items* section.
Second-annotator tooling and the v3 pilot wait on Gio's go.

### 2026-11-22 (later) — v2 exported and delivered; tsis-first sync rule

**Decisions (Gio):** every session pulls tsis first, to stay in sync with
leische.

**Done:**
- Gio exported v2 and delivered it to leische. I verified it against v1:
  the same 15,000 ids, and 0 differences in sarcastic, language, both
  sentiments or cues. The card matches the trial.
- `c47263b`: RULES §1.1 and §9.1, the tsis-first rule.
- `b1e3be4`: UYAM_LATEST announces v2 (mirrored as tsis `905c9ab`).
- Reminders `export-dataset-v2` and `send-v2-to-leische` ticked.

**leische (LEISCHE_LATEST 2026-11-22, leische `7c7e291`):** v2 received,
readiness done, runs paused at 1 of 11, nothing quotable yet. No new
requests to uyam. Its RULES now also pull tsis first (`1ddc9e8`).

### 2026-11-22 — D1 broad, sarcastic-weighted gold redraw, v2 unblocked

**Decisions (Gio):**
- D1 uses the broad reading: hugot, self-pity and playful praise may count as
  sarcasm.
- Weight sarcastic items in the gold set.
- He will label the 269 gold items himself.
- Manuscript revisions come later.
- The Reddit API application is still pending.

**Done:**
- `2f42212`: `review.gold_sarcastic_oversample` (2.0), multiplied with the
  split weight. Redrawn after a backup: 62 of 300 are ensemble-sarcastic,
  the 31 labels were kept, and labels and reviews were unchanged.
- `5841142`: GUIDELINE v1.1, D1 broad, with the `D1` notes tag so κ can be
  reported both ways.
- Old `guideline-v1-draft` suggestions were deleted. 300 new ones were made
  by 5 blind agents and loaded (`guideline-v1.1`, 24 sarcastic, 8 under 0.6
  confidence).
- `9f2b9a4`: the Download bundle button works from any browser (falls back
  to the newest zip on disk), and the app's export version defaults to v2,
  not the frozen v1.
- `0f3db3e`: new leische v2 brief and reminder updates.
- Reminders ticked in `data/reminders.json`: backup, backfill, proxies,
  latest days, keyword searches, retag, reindex, low-confidence review.
- `8d02e92`: UYAM_LATEST, mirrored as tsis `f8c444c`.

**Findings:**
- The two repos were waiting on each other: leische's LEISCHE_LATEST
  (2026-11-17) waits on uyam's export.
- The v2 bundle contains a file named `dataset_card.json`. Unzipped into
  leische `data/`, it would overwrite the v1 card, so the brief says to unzip
  into `data/incoming-v2/`.
- Ethics: all 15,000 labeled items came from the live Shreddit scrape, so
  "a public archive was used" can't describe them (reminder updated). If the
  API is approved, re-fetching the labeled ids through it is a mitigation to
  report.
- Guideline gaps the new agents raised (for a v1.2):
  - a silver-lining joke vs D1 playful elevation
  - parody in someone else's imagined voice
  - /s on a playful joke (H8 vs H4)
  - absurd one-up proposals and fake games used to ridicule
  - stock ironic phrases ("the world is healing")
  - polite gratitude toward concern-trolling
  - mock concessions whose words are literally true

**Left open:** the export (Gio), the leische chat, gold labeling, and D2, D3
and D5.

### 2026-11-19 (late) — v2 prep: phatic flag, retag, gold redraw, guideline, assist, nimble

**Context (Gio):** "do the stuff you can do now". He approved the DB backup,
the reindex, the gold redraw and the retag. His choices: phatic is a flag only
with labels frozen; Claude assist via Claude Code agents with a reveal
toggle; the v2 export waits for leische.

**Done:**
- Schema (`2d81141`): `candidates.phatic`, review span/unsure/first-pass/
  assist_seen, and the `eval_annotations` and `assist_suggestions` tables.
  It migrates in place on open.
- Retag (`9a457f9`): `uyam retag-calendar-walk` re-tagged 600 records with
  backups and manifest notes. It is idempotent.
- U9 and the gold redraw (`bce2167`): labeled phatic items stay eligible and
  are flagged. The gold strata are sarcasm × language × split × length band,
  proportional with spill-over; `--redraw` replaces only unlabeled items.
  Real run: 31 kept, 269 new; long submissions went 20 → 4 and greeting posts
  3 → 0.
- Index streams JSONL (`9bc89ad`): 588,507 records in 74 s.
- Export (`3738c04`): `sarcastic_majority`, `sarcasm_vote_share`,
  `flags.phatic`, coverage by window, slim corpus (`--corpus-scope`), and a
  versioned card.
- Review tab (`2469da3`): span checkboxes, unsure, and blind-first reveal.
  The first pass is never overwritten and is what gold κ uses.
- nimble as an evaluation-only annotator (`badaa2b`): `annotate evaluate` /
  `eval-report`. A NaN gold κ is now null, so the card stays strict JSON.
- `docs/GUIDELINE.md` draft v1 (`2090ba9`). It shares no 6-gram with the
  labeled texts. Gio's own examples are private, in
  `data/guideline_examples.md`.
- `load-assist` (`49c8839`). 300 suggestions loaded (11 sarcastic, 13 under
  0.6 confidence).
- UYAM_LATEST (`7716aa5`) mirrored to tsis (`b79f629`). Reminders updated
  (`4a1ce32`).

**Guideline gaps the assist agents hit** (for D1–D5 or a v2 of the
guideline):
- A curt or dismissive "good luck" (H9 vs H11).
- Leading rhetorical questions that push the author's view without pretending
  ignorance (H5 vs H10).
- An ironic concession whose words are literally true ("he did accomplish
  something — for X").
- Mocking "daw" next to an openly stated rebuttal (H8 vs H10).
- Scare quotes inside a sincere argument (H8).
- Parody quoting other people's voice (not covered by T3).
- Half-meant joking quips (H4 step 4b).
- H13 when the text itself describes the unseen video, or the target is
  someone the thread never introduces.

**Code review (second-opinion subagent) and fixes:**
- `2ca90ab`: eval-report and LID-vs-gold had read the revised label. All
  gold scores now go through `db.blind_gold_labels()`. Also added
  `votes_seen` and `first_reviewed_at`, a one-sentence span default, the
  `first_pass` export shape, migration race tolerance, and tests that
  actually discriminate.
- `c034f1d`: raw rewrites split on newlines only (U+2028 could corrupt a
  record), keeping other bytes.
- `19fdc6f`: no verdict hints from load-assist.
- `0969434`: documented the limits.
- `9c8e87d`: contract plus UYAM_LATEST (mirrored as tsis `04cbc88`).
- Tests: 331 → 338.
- Not fixed, deliberately:
  - L3: first_pass stays frozen even for a misclick fixed before any reveal.
    This is now documented in GUIDELINE §6.4.
  - L4: Claude's influence on low-confidence labels isn't exported. The
    queue is empty.
  - L7 handled by first_reviewed_at.
  - L9: prompt-version-agnostic labeled set. It has no effect today: 0
    sarc-v1-only items.

**Left open:** everything in *Next steps*. Ask Gio whether the gold strata
should over-weight sarcastic items: about 11–33 positives of 300 is thin for
κ on the positive class.

### 2026-11-19 — collection complete; size, length and oversampling analysis

**Context (Gio):** asked whether 500k+ records is too much, how to
preprocess them, and whether to limit items to ≤3 sentences.

**Findings:**
- Natural pass complete for all three subreddits: **588,507 records** (40,125 /
  548,382), ~770 MB. Most of it is temporal context, not annotation work. At
  the 48 h rule only 9,637 records reach the model. Coverage on the 15,000 v1
  labels: 48 h 36.8% → 46.5%, 7 d 64.9%, 30 d 82.1%. Widening the window is a
  leische/method decision (UYAM_LATEST §2.1).
- Length (v1 labels): 1-sentence items are the hardest (F1 ~0.33, 41% split
  votes); 2–5 are fine; 6+ fail but hold 66 of 1,601 positives. A ≤3 cut
  drops 27% of items and 190 positives (mostly r/OffMyChestPH) and breaks
  v1/v2 comparability. Advised against it; a token cap (≤192) or ≤5 sentences
  works as a reported evaluation slice. Table in UYAM_LATEST §2.2.
- Archive keyword searches: fixed `limit=auto` with query (`f842667`). They
  only re-found existing posts (natural pass is complete), so they were
  stopped. Markers are rare (1,196 items; "sana all" is the only promising
  one). Oversampling now means optional target selection (a reminder for
  Gio).
- leische (LEISCHE_LATEST, 2026-11-17): v2 should keep the 15,000 labels.
  Must-haves are U5 (coverage in the card), U8 (re-tag the 57 trial posts
  `natural`), U9 (keep labeled rows; flag `phatic`, don't drop); U1 should be
  included; U2 is held.

**Left open:** implement U9 (phatic as a flag for labeled rows) and U1 before
the v2 export. Re-tagging the 57 trial posts needs Gio's yes (raw JSONL
rewrite).

### 2026-11-17 — cross-repo rules: tsis + leische, UYAM_LATEST.md

**Decisions (Gio):** a third repo, **tsis** (`../tsis`, the LaTeX
manuscript), is now part of the workflow. uyam agents always check it and
never edit its LaTeX. The only file they write there is `UYAM_LATEST.md`
(a copy of uyam's), updated whenever uyam changes. tsis gets its own
`RULES.md`: check uyam and leische for new commits and pull. leische runs on
another device, so GitHub is the channel.

**Done:** `UYAM_LATEST.md` (dataset versions, provisional corpus snapshot,
method changes for the methodology/ethics text, affected manuscript passages,
status of leische's U1–U8 brief, change log). RULES.md §1.1/§1.5 and new §9.
tsis gets `RULES.md`, `CLAUDE.md` and the `UYAM_LATEST.md` mirror.

**Findings:** leische's `suggestions/UYAM_CHANGE_BRIEF.md` (2026-09-17) holds
eight requests for uyam (U1–U8) that never reached this handoff. Only U5
(author history, via the archive backfill) and U8 (oversampling) are in
progress. The manuscript still describes v1 (20,573 records, Aug 1 → Sep 4),
which is correct until v2 is exported. The local `../leische` clone is on
`single-notebook`, 31 commits behind, with an uncommitted notebook change
(not ours, untouched).

### 2026-11-17 — archive backfill resumed after three stop causes

**Context (Gio):** the app showed only r/Philippines had grown; asked why not
all subreddits.

**Findings:** the run ended 2026-11-13 00:46. r/Philippines got through Oct 11
(stored) and was then throttled for over an hour (60-wait budget).
r/CasualPH got through Aug 17, then hit Cloudflare HTTP 530 ("origin
unreachable", archive down). r/OffMyChestPH hit the same 530 instantly.
All 24 keyword jobs failed with "needs a date window" (`make_oversample_job`
strips `--since` for Shreddit, and archive jobs were stripped too). Pace while
it ran was ~17 min per r/Philippines day.

**Done:** `5b72a10`: 5xx/530 and network errors use the throttle budget, now
360 waits (~6 h). Archive keyword jobs keep the window and search it with one
range query. Bulk mode skips comment streaming for stored days. New
`--no-oversample` flag. Fixed a CLI test that leaked log handlers (14
cascading failures). Tests: 275 → 281. Resumed per subreddit (see *Next
steps* 2). Today the archive answers within seconds.

### 2026-11-11 — archive backfill started; archive throughput fixes

**Decisions (Gio):** run the archive backfill Jun 1 → Nov 10 at 300 posts per
day, after asking whether Shreddit could walk back that far. It can't: `/new`
stops after ~1,000 posts, which is about 8–10 days on the busy subreddits.

**Done:** backed up `data/db/collection.sqlite3` →
`collection-2026-11-11.bak.sqlite3`. First run crawled (1–4 posts/min). The
Arctic Shift docs say limits are dynamic, by server load and query cost, and
point to monthly dumps for bulk use (all of Reddit, impractical here).
`6a64caf`: a subreddit-wide comment stream with a ≥3-day reply horizon
(state it as a limitation), `limit=auto`, adaptive pacing, and throttling with
its own ~1 h budget plus Stop checks. Tests: 265 → 275. Restarted 03:13. It
stays throttled most of the time, but ~1 request/min gets through.

**Findings:** my own test requests share the same IP allowance, so don't
probe the API while a run is going. r/Philippines is ~250–270 posts/day,
more than double the earlier estimate.

**Left open:** watch the run (2–3 days). Then `uyam annotate index && select`
and the v2 export.

### 2026-11-11 — live Shreddit walks; three more scraper fixes

**Decisions (Gio):** asked to commit the other chat's work (already committed
and pushed as `cb0dc37`) and to run the walk again.

**Done:** `fe55611` tests isolate the scrape status files (via
`UYAM_SCRAPE_STATUS_DIR` for worker processes). `a63687c` bottom-jump
scrolling, rate-limit toast handling, short expansion timeouts. `0e53b76`
network-security IP-ban detection. `5c76e83` 360 s toast cap. (Its commit
message also says HANDOFF was updated; that edit failed, and this entry is
the real update.) Tests: 259 → 265, all offline. ruff clean; mypy stays at
50.

**Findings:**
- Walk 1 (`cb0dc37` only): every listing stopped at 27 cards (`idle`), and
  a megathread hit "rate limited, take a break for 96 seconds" while the
  scraper kept clicking "more comments".
- Walk 2: listings reached `--since`. r/Philippines then hit "You've been
  blocked by network security" and spent ~30 s per post on the banned IP.
- Walk 3 (r/Philippines only): 58 posts / 1,649 comments, clean.
- Runs used a walk-only config (keyword oversampling off) pointed at the
  real `data/`. Nov 8–10 is now covered for every subreddit.

**Left open:** Shreddit keyword searches (`--oversample-only`). The 57
mis-tagged trial posts are waiting on Gio's call.

### 2026-11-10 — fix Shreddit listing walk after Reddit's JSON 403

**Decisions (Gio):** none this session. The trial rows were left as stored.
Retagging them rewrites `data/raw/` and needs an explicit yes.

**Done:** Shreddit calendar mode no longer depends on `/new.json`. Listing
cards are snapshotted in one `evaluate_all`, each feed page waits ~4s, and
the walk stops once a card is older than `--since`. Keyword jobs search,
and each job gets its own Chrome profile. A throwaway smoke (temp profile,
nothing written to `data/raw/`) read 25 CasualPH cards spanning 2026-11-08
→ 2026-11-10. Tests: 249 → 259, all offline. ruff clean; mypy stays at 50.

**Findings:**
- Trial log: `new.json` 403 on all three subs; r/Philippines died on
  `shreddit-post.nth(17)` (45s); the other two DOM walks stored 3 posts
  each; the oversample batch walked `/new` again (not search) and then
  failed with "profile already in use".
- Of the 63 stored posts, 57 are `keyword_oversampled` with the UTC day in
  `matched_query_or_keyword`. Six are natural. Comments follow the parent.

**Left open / blocked:** Gio runs the latest-days command. The mis-tagged
trial rows stay until Gio asks for a retag.

**Commands for Gio:**
`uyam collect --source shreddit --since 2026-11-08 --per-day 40`

### 2026-11-10 — rules/agents/handoff docs; archive backfill source; phatic cleaning

**Decisions (Gio):**
- New collection window: **2026-06-01 → 2026-11-10** (backfill before August
  and fill the Sep 4 → Nov gap).
- Historical source: the **Arctic Shift archive API**. It's a third-party
  Reddit archive, so its use has to be stated in the methodology/ethics.
  **Shreddit stays the fallback** ("can just use shreddit pipeline if ever"):
  it covers the latest days and stands in if the archive fails. It can't
  reach back months.
- Cleaning: a new `phatic` exclusion reason, reported in the card. Phatic items
  already voted sarcastic go to human review rather than being dropped.
  Re-export as v2.
- The September export-bundle work got committed.
- Doc files are uppercase (`RULES.md`, `AGENTS.md`, `HANDOFF.md`) to match
  the repo convention. `CLAUDE.md` imports RULES + HANDOFF so Claude Code
  auto-loads them.

**Done:**
- `a8d184d` feat(export): bundle every artifact into one zip with SHA256SUMS
- `a56724d` docs: add agent rules, subagent roles, and a session handoff log
- `38a7faa` feat(select): exclude phatic items; export only currently-eligible rows
- `5bd9d13` feat(collect): Arctic Shift archive source for date-range backfill
- `f77bb32` docs: archive backfill, phatic filter, v2 caveats (README, contract)
- Tests: 198 → 230, all offline. ruff is clean; mypy stays at the 50-error baseline.

**Findings:**
- The corpus is 2026-08-01 → 2026-09-04, not June as assumed.
- Arctic Shift covers all three subs from 2026-06 to ~2 days ago, with every
  field the mappers need. Its quirks: throttling comes back as
  `{"data": null, "error": "Timeout. Maybe slow down a bit"}` on HTTP 200 or
  422, with `x-ratelimit-reset`; `after=0` is rejected with a 400. Both are
  handled, and both were found by the live smoke run.
- Pure phatic text is rarer than it looked: 107 items excluded plus 21
  routed to review, out of 17,424 eligible. A looser regex count (~395) mostly
  matched longer comments with real content.
- "Welcome to the club / to Silent Hill" are idioms, not greetings, so
  "welcome to …" is excluded from the phatic phrases.

**Left open / blocked:** nothing is blocked. See *Next steps* and the open questions.

**Later in the session:** added the Streamlit **Reminders** tab (first tab,
with the pending count in its label). It's backed by `config/reminders.yaml`
plus `data/reminders.json`. Seven read-only detectors show "Detected:" hints.
Gio can add their own notes. A **Proxies** tab followed. It lists proxies by
provider (passwords masked) and tests a sample against reddit.com. It can also
remove a provider, replace or append a pasted or uploaded list, and restore
backups from `data/proxy-backups/`. The parser now also accepts
`user:pass@host:port`, `user:pass:host:port` and `https://` / `socks5://` URLs,
and it percent-encodes credentials. **Diagnosis:** proxies.txt held only the
5,000 wolveproxy.com proxies (unchanged since 2026-08-15, plan expired). They
went 0/8 in a probe while a direct connection got HTTP 200. That's why
Shreddit had been failing; the last real run on 2026-09-05 logged 1,267 failed
page loads. Gio has to load the new provider's list (it's a reminder). The app was relaunched detached via
`run-app.bat` at http://100.71.83.125:8501 and logs to
`data/.streamlit-app.log`.

---

<!-- Session entry template — copy to the top of the log:

### YYYY-MM-DD — <one-line topic>

**Decisions (Gio):** …
**Done:** <commit hash> <subject> …
**Findings:** …
**Left open / blocked:** …
**Commands for Gio:** …
-->
