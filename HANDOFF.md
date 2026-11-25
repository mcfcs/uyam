# Session handoff

The chat-to-chat log for agents working in uyam. Read *Current state* and the
newest session entry before starting. Update all three sections before ending
a chat (see [RULES.md §1](RULES.md)).

> Not to be confused with [UYAM_HANDOFF.md](UYAM_HANDOFF.md), which holds the
> model repo's (leische's) requests to uyam and uyam's responses.

---

## Current state (as of 2026-11-24)

**Corpus (raw JSONL, `data/raw/`)**: **588,507 records** (40,125
submissions, 548,382 comments) from r/Philippines, r/CasualPH and
r/OffMyChestPH, **2026-06-01 → 2026-11-10**, complete and **frozen** until
Reddit API approval and ethics clearance (RULES §5.10). Every record is
`natural` (the 57 trial posts were re-tagged 2026-11-19, with backups).

**Databases** (each run has its own DB; all read the same corpus):
- `data/db/annotation.sqlite3` (prompt `sarc-v2`): the original v1/v2 run,
  frozen (15,000 items, 1,601 sarcastic). **Gio labels gold here** (the
  labeling site writes to it): 113 of 300 done, 80 of them with Claude's
  take showing (human-assisted), 32 blind. It also holds the definition test
  (`eval_annotations`, guide-v1.1, 900 votes; report
  `data/eval-report-guideline.md`).
- `data/db/annotation-v3.sqlite3` (`config/annotation-v3.yaml`): the
  equal-size majority-vote run, complete. Gold synced 2026-11-23.
- `data/db/annotation-v4.sqlite3` (`config/annotation-v4.yaml`, prompt
  `guide-v1.1`): the guideline-prompt pilot, **complete** (2026-11-24
  00:52, 13.8 h, 0 failures) and exported as `dataset-v4` (a pilot; don't
  send it as training data). Results are in UYAM_LATEST and in the session
  log below.
- Backups from 2026-11-23: `annotation-20261123T104802.bak.sqlite3` and
  `annotation-v3-20261123T104802.bak.sqlite3`.

**Exports (`data/annotated/`)**:
- `dataset-v1`: in the manuscript, and leische's current results.
- `dataset-v2`: exported 2026-11-22; leische has it, with runs paused at 1
  of 11.
- **`dataset-v3`**: exported 2026-11-23 (21,000 rows, 4,011 sarcastic, gold
  κ 0.24 / blind 0.18). Not sent to leische yet.
- `dataset-v4`: the 5,000-item pilot (2026-11-24). `dataset_card.json` is
  now the v4 card; use the versioned cards.
- `media_context-v2.jsonl`: per-row flags for v2.

**Known gaps the thesis must address**: no official API (ethics); a single
human annotator; gold κ 0.24 (0.18 blind) on the sarc-v2 labels, which
rises to 0.48 / 0.64 with the guideline prompt (definition test);
guideline decisions D2, D3 and D5 are open.

## In progress

**dataset-v5 run STARTED 2026-11-24 19:50 PHT** (Gio's "go v5"). It's
detached: PowerShell pid 164172 runs `data/.v5-run.ps1` (stage 1
`--target 16362`, then stage 2 `--target 21000`). Lane job
`annotation-v5-run-all-local` (qwen3 → sealion → llamasealion), local only.
Logs: `data/.v5-run.log` (UTF-16) and
`data/.annotate-status/annotation-v5-run-all-local.log`; also visible in
Annotation → Current annotation runs.
- ETA at 10.6 s per item across the three models: stage 1 is 11,362 items,
  about 33 h (around Thu 26 Nov 05:00); stage 2 is 4,638 items, about 14 h
  (around Thu 26 Nov 19:00).
- After stage 1 (optional early cut) or stage 2: `sync-gold`, aggregate, and
  `export --version v5 --config-path config/annotation-v5.yaml`. Language for
  analysis is `aux.lid.language_rule` (already computed for all 21,000).
- To resume after a stop, re-run the script; finished items are skipped.

Streamlit runs on 8501 (main) and 8502 (labeling site).
Gio's apps (arbit, manabi/GPT-SoVITS) share the laptop: **don't close
them**. ComfyUI is closed.

## Next steps (for Gio unless marked)

These are also the to-dos in the Streamlit **Reminders** tab
(`config/reminders.yaml`), where Gio ticks them off. `data/reminders.json`
records which ones are done.

1. **Decide on a guideline re-annotation after the adviser meeting.** The
   v4 pilot says yes for sarcasm (κ doubles) but no for language as it
   stands. A full run would need `guide-v1.2` with a fixed language section,
   or language from v3/LID, and maybe without SEA-LION: another pilot, then
   about 2–3 days of laptop GPU.
2. ~~Label the remaining 187 gold items~~ **done 2026-11-25** (300/300).
3. **Adviser meeting Wed 2026-11-25**: `../tsis/ADVISER_UPDATE_2026-11-23.md`
   has the questions, including Q8 (v4 / full guideline re-annotation).
4. Send `uyam-dataset-v3.zip` to leische when Gio wants v3 runs. Before any
   re-export of v2/v3 with more gold, the agent runs `sync-gold` for that
   config.
5. **nimble** (gaming PC, ask first): `ollama pull nimble:9b`, `uyam
   annotate evaluate --model nimble`, `uyam annotate eval-report`.
6. Thesis text (reminders in the *Thesis manuscript* section).

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

### 2026-11-25 (evening) — gold set complete; final agreement

**Context (Gio):** finished the gold annotation; asked what now.

**Done:**
- Backups `annotation-{v3,v4,v5}-*-pre-goldsync.bak.sqlite3` (sqlite backup
  API; v5 was being written). `sync-gold` into v3/v4/v5: 187 added each.
- Final agreement (297 usable, 26 +; numbers in UYAM_LATEST): guideline κ 0.31
  [0.19, 0.43], v1 0.27, v3 0.20. Guideline − v3 is +0.11 [+0.01, +0.21],
  the only paired difference that clears zero. 85% of gold positives are
  under unseen media.
- Progress notes prose updated to the final numbers. The plan is now to
  pre-declare majority as primary and unanimous (from the vote share) as a
  sensitivity check, so the gold is never tuned on.

**Decisions (Gio):** majority vote is the primary v5 label, and unanimous
(from the vote share) is a sensitivity check. Re-export now.

**Then done:** `9f15d67` adds `flags.gold_queue` (all 300 queue posts; parquet
`flag_gold_queue`; card `counts.gold_queue_rows`). v3 and v4 were
re-exported: labels unchanged, 300 human_gold, 300 flagged, card gold κ
0.203 / 0.314 on 297. Taildropped to phillmyeol (v3 1ae3ec92…, v4
fb8910fb…). The UYAM_LATEST §9 request asks leische to re-score.

**Left open:** v5 export Thu evening (retry 3 qwen3 failures, majority, gold,
flag); leische training (Gio's go); the descriptive error analysis.

### 2026-11-24 (late night) — dataset-v5 started; gold-assist decision

**Decisions (Gio):**
- "go v5": the guideline-prompt dataset at v3's size, with the same
  prompt, models and majority vote as the v4 pilot.
- **The remaining 187 gold items may be labeled with the Claude toggle on,
  and they count as his gold labels.** The code needs no change: assisted
  first passes already count in the main gold κ (ALL). The
  `assist_before_first_pass` flag is still recorded truthfully, so the
  blind-only view stays available as a sensitivity check. It is not the
  primary number.

**Done:**
- `7289dbe`: `fork-extend` (copies the pilot, keeps its votes, and stages the
  target set: gold 4, pilot 3, v1/v2 posts 2, new months 1) and
  `config/annotation-v5.yaml`. Tests: 428 → 430.
- `data/db/annotation-v5.sqlite3` built from v4 (5,000 kept, 300 gold) plus
  v3's targets: 11,362 first-stage and 4,638 later. `language_rule` computed
  for all 21,000. The dry run showed the guideline prompt.
- Run started (see *In progress*).

**Not done:** leische's `gold_queue` export flag (§5e.4) waits on Gio's go.
It fits best with the v5 export.

**Later:** `7a08f9b` adds a **Progress notes** tab (second tab on 8501) for
Gio's consultation and defense. Its 11 sections cover: adviser items, Arctic
Shift collection, annotators and before-vs-now agreement with the gold, the
5 dataset versions, data usage, media flags, model results with leische's
exploratory comparison, the routing plan, the gold set, next steps with
expected results, and likely panel questions. It has 8 Graphviz diagrams.
Gold progress, agreement and v5 progress are live and read-only
(`progress_notes.py`); the prose lives in `progress_notes_ui.py`. **When
numbers change (v5 export, leische results), update the prose there.**
Tests: 430 → 437.

**2026-11-25 02:50:** Gio said 8501 wasn't really loading, though its health
endpoint answered in ~0.01 s, so the main app's page work is slow, not the
server. `57402c0` puts the notes on the light **8502** site as a second,
lazy tab ("Progress notes"); labeling still runs only the review page. 8502
was restarted. Open question: why 8501 loads slowly for Gio (startup work
or tab bodies). Not investigated yet.

**2026-11-25 15:15:** gold is at 240/300 (237 usable, 22 sarcastic). The
label sets' agreement with the gold shifted: guideline majority κ 0.33,
v1 0.27, v3 0.21 (at 111 items it was 0.48 / 0.24 / 0.24). Every LLM label
set over-calls (59 model + vs 22 human). The same guideline votes under a
stricter rule match Gio better: unanimous κ 0.44, qwen3 AND Llama 0.41,
Llama alone 0.38, against majority 0.33. That's exploratory, on all labels;
choose on a dev split. The Progress notes get section 10 ("After the gold
set": why gold matters, dev/test split, next actions, live vote-rule table,
diagram), and the stale 111-item prose was corrected.

### 2026-11-24 (night) — leische's exploratory initial-vs-current comparison

**leische (LEISCHE_LATEST 2026-11-24 night, leische `0a995e3`, §5d/§5e):**
30 trainings (A v1 / B v2 / C v3 on v1 posts / D0 v3 on v4 posts / D v4;
text-only and conv+temp; 3 seeds; fold 0; gold held out), scored on Gio's
gold (111 items, 11 +; blind 32, 6 +).
- A→D, context model: F1 0.158 → 0.353 (+0.195 [+0.042, +0.350]), κ 0.03 →
  0.25. Exploratory; 1 of 16 step tests.
- B→C (equal-size + majority), text-only: +0.188 [+0.065, +0.313]. Recall vs
  Gio goes 0.15 → 0.67, precision 0.11 → 0.21: v1-trained models missed most
  of Gio's positives.
- D0→D (guideline prompt): clears zero only on blind (+0.19 / +0.22).
- A→B (more history): +0.09 F1 for the context model (CI spans zero); Δκ
  +0.121 clears zero. The noise floor is about 0.06–0.10 F1.
- 9 of Gio's 11 gold positives are media-dependent; the text-only slice has
  2 positives.
- **Request to uyam (§5e.4):** flag all 300 gold-queue rows in the export
  (`gold_queue: true`) so leische can hold out the unlabeled 187 too. Not
  done yet; offered to Gio (needs a re-export and a re-send).
- leische's view: labels are a bigger lever than corpus size; a full
  guideline re-annotation looks worth doing; more blind gold positives
  matter most.

### 2026-11-24 (late afternoon) — Tagalog fix for the rule-based language

**Context (Gio):** "yes, try to fix the Tagalog LID, may download".

**Done:**
- `pip install wordfreq` (3.1.1; it has `small_en` and `small_fil`).
- One-shot evaluation with the 10× threshold set a priori: κ 0.71 → 0.84,
  accuracy 84% → 91%, English 48 → 46/49, Taglish 44 → 48/51, Tagalog
  1 → 7/11. No tuning on the gold.
- `8e63f08`: `lexicon_language()` and `LanguageIdentifier.rule_tags()`
  (`L-rules-v2`; falls back to v1 without wordfreq). wordfreq added to the
  `annotate` extras. Tests: 417 → 426.
- Backups (`*-pre-lrule2.bak.sqlite3`); `--refresh-rule` for v3 (21,000)
  and v4 (5,000); both re-exported. Labels unchanged, checksums OK, all rows
  `L-rules-v2`.
- v3 rule language: English 6,537, Tagalog 1,217, Taglish 13,246. The
  smallest cell is Tagalog-sarcastic, 360. v4: 1,549 / 290 / 3,161.

**Left open:** the rule still calls 4 of 11 Tagalog gold items Taglish. Any
more tuning needs a separate language dev set, not the gold.

**Later:** Gio downloaded the v4 bundle to the leische machine and has the
smoke-test prompt for the leische chat. `74c4bb1` adds a bundle picker to the
Annotation tab's download (every `uyam-dataset-*.zip`, newest first, with
rows and sarcastic counts from its own card); the 8501 app was restarted.
Gio said leische doesn't have `media_context-v2.jsonl`. The same section now
offers it under *Side files* (`list_side_files`). It is never committed to
tsis or any repo (RULES §5.11); UYAM_LATEST §9 has the status.
Gio asked to commit the downloads to tsis. I declined (RULES §5.11, ethics
storage, files over 100 MB) and offered Taildrop. **The leische computer is
`phillmyeol` (100.102.10.69), the gaming PC.** Sent by Taildrop:
`uyam-dataset-v3.zip` (sha256 9dbefa9f…) and `media_context-v2.jsonl`
(a6627cac…). Gio has the full A/B/C/D/D0 comparison prompt for leische.

### 2026-11-24 (afternoon) — rule-based language field; v4 bundle for a smoke retrain

**Context (Gio):** asked whether language can be redone for v4, and about
v4's next steps. His choices: the language fix only, no new LLM run; send v4
to leische for a retrain smoke test.

**Done:**
- `520df28`: `lid_results.language_rule` and `rule_version` (GUIDELINE
  L2–L4b on the fastText token tags), `annotate lid --refresh-rule`,
  `aux.lid.language_rule` in the export, and `language_rule_vs_human_gold`
  in the card. Tests: 406 → 417.
- Backups (`*-pre-lrule.bak.sqlite3`), then the rule was computed for v3
  (21,000) and v4 (5,000). v3 and v4 were re-exported: labels unchanged
  (4,011 / 1,104 sarcastic), bundle checksums OK.
- Result on the gold: κ 0.71, 84%. English 48/49, Taglish 44/51, **Tagalog
  1/11**. The rule says `tagalog` for only 200 of 21,000 v3 rows, because
  the tagger takes the first of en/tl in fastText's top 5, and fastText ranks
  English first for common Tagalog words.
- A request to leische for a v4 smoke retrain (UYAM_LATEST §9); contract
  updated.

**Blocked:** `pip install wordfreq`, for an English/Filipino word-frequency
tagger (it has `small_en` and `small_fil` lists). Auto mode denied it, so
it needs Gio's OK. Plan: tag each token by the higher zipf frequency
(en vs fil), keep fastText as the fallback, bump to `L-rules-v2`, and score
it once against the gold without tuning.

### 2026-11-24 — v4 pilot finished, exported and compared with v3

**Context (Gio):** "what's the current update".

**Done:**
- v4 finished at 00:52 (13.8 h, 15,000 votes, 0 failures). I backed up the
  v4 DB, ran `sync-gold` (113 unchanged), aggregated (859 unanimous, 4,141
  majority) and exported `dataset-v4` (5,000 rows, `uyam-dataset-v4.zip`).
- leische: nothing new (LEISCHE_LATEST still 2026-11-22, origin/main
  `1ddc9e8`). Gold is still 113/300.

**v3 vs v4 on the same 5,000 ids:**
- Sarcastic: 972 → 1,104.
- κ v3–v4 on sarcasm: 0.59 (407 items flip to 1, 275 flip to 0).
- Gold κ: all 0.24 → 0.48, blind 0.18 → 0.64. These are the same votes as
  the definition test.
- Fleiss κ on sarcasm: 0.40 → 0.26. Per-model positive rates: Llama 9%,
  qwen3 26%, SEA-LION 41%.
- Language κ vs gold: 0.28 → 0.08. 39 of Gio's 49 English items become
  taglish, and qwen3 says English for 76 of 5,000. Only 24
  English-sarcastic rows.
- Gold κ by media: text-only 0.22 (n 64), media-dependent 0.70 (n 47). The
  n is small.

**Left open:** the decision on a full guideline re-annotation (adviser Q8).
If yes, write `guide-v1.2` (fix the L-rules in the prompt so a lone particle
doesn't flip English to taglish, or take language from LID), re-pilot, and
consider replacing SEA-LION.

### 2026-11-23 (evening) — dataset-v3 exported; v4 guideline-prompt pilot prepared

**Context (Gio):** "go" for the v3 wrap-up. He also asked for a v4: about
5k items re-annotated with the new guideline and prompt, as a test. His
choices: all 300 gold + a stratified sample, and the same three models.

**Done:**
- Backups: `data/db/annotation-20261123T104802.bak.sqlite3` and
  `annotation-v3-20261123T104802.bak.sqlite3`.
- qwen3 retry of the 2 failed v3 items (local, about 10 s): both stored.
- `bc950c2`: `annotate lid --target N`. LID was run on v3's 6,000 new items.
- `fdac22b`: `annotate sync-gold` (main → fork: gold reviews, queue flags,
  assist suggestions; read-only source, refuses mismatched queues). Real
  run: 82 added, 31 unchanged, 300 assist suggestions copied.
- v3 aggregate: 4,785 unanimous, 16,215 majority, 0 unresolved.
- **`dataset-v3` exported** (`data/annotated/*-v3*`, bundle
  `uyam-dataset-v3.zip`). Card verified: 21,000 rows, 4,011 sarcastic, the
  months as planned, gold κ 0.24 (blind 0.18), Fleiss κ 0.40.
- `badebe7`: v4 plumbing. The runner takes its prompt from the config's
  `prompt_version` (sarc-v2 or guide-v1.1, each hash-guarded); context
  stays the sarc-v2 snapshot. Also `fork-subset`, `import-eval-votes`
  (separate DB only), `config/annotation-v4.yaml`, and the pipeline's
  closing hint fixed. Tests: 396 → 406.
- v4 DB built: `data/db/annotation-v4.sqlite3`. 300 gold + 4,700
  stratified (972 v3-sarcastic, 19.4%); the definition test's 900 gold
  votes imported. A dry run confirmed the guideline prompt and the hash.

**Left open:**
- The v4 run (an LLM run; Gio's go). Command:
  `uyam annotate pipeline --config-path config/annotation-v4.yaml --skip-prep --skip-gold`,
  about 14 h on the laptop. Then aggregate and export v4 with the v4 config.
  Compare v4 with v3 on the same 5,000 ids and against the gold labels (blind
  and all).
- Re-sync gold before any re-export (`sync-gold --config-path ...` for v3
  and for v4).

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
