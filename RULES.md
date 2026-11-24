# Rules for agents working in uyam

uyam is the data-collection and LLM-annotation pipeline for Gio's undergraduate
thesis: **context-aware sarcasm detection in Taglish Reddit posts**. It produces
the labeled dataset; the model is built in a separate repository, **leische**,
on another device. The interface between them is
[docs/dataset-contract-leische.md](docs/dataset-contract-leische.md).

These rules bind every agent session and every subagent. They override
defaults. If a rule blocks the task, stop and ask Gio. Don't work around it.

---

## 1. Session protocol

1. **Start:** read [HANDOFF.md](HANDOFF.md) (latest entry first), then
   `git status` and `git log --oneline -10`. If the tree is dirty with work you
   did not make, ask before touching it. Then **check the sibling repos**
   (§9): pull tsis, fetch leische, and read leische's latest handoff entry
   and its brief to uyam.
   **Always pull tsis first and read its `LEISCHE_LATEST.md`.** That is
   how uyam and leische stay in sync (leische runs on another device), so
   tell Gio about anything new there before starting: new results, a v2 run
   status, questions or requests to uyam. Pull tsis again before any commit
   that touches UYAM_LATEST.md or the export, so you never answer a stale
   leische state (§9.1).
2. **During:** keep [HANDOFF.md](HANDOFF.md) honest. If you change a plan,
   number, or decision that it records, update it in the same commit.
3. **End:** add a new entry to the top of HANDOFF.md's session log (template
   at the bottom of that file), update its *Current state* and *Next steps*,
   and commit and push it. Every chat that changes anything ends with a
   handoff commit.
   **Every chat ends with ALL of Gio's open action items** (Gio,
   2026-11-22). The last message lists every open step on Gio's end, not
   only the ones from this chat: open items in config/reminders.yaml (minus
   the ticks in data/reminders.json), what the adviser asked for
   (UYAM_LATEST.md §10), and anything a sibling repo is waiting on. Group
   them by urgency, and say which ones an agent can do once Gio says go.
4. **Gio's to-dos go in [config/reminders.yaml](config/reminders.yaml).** When
   you leave Gio a step to do (a real run, a review, thesis text), append a
   reminder with a new unique `id` and never rename an existing one. It shows
   up in the Streamlit **Reminders** tab, where Gio ticks it off (ticks live
   in `data/reminders.json`). Read that file to see what Gio has already done.
5. **Keep [UYAM_LATEST.md](UYAM_LATEST.md) current.** Whenever a change
   affects what the thesis says about the data (collection sources or window,
   corpus or label numbers, filters, annotation, the export, dataset version),
   update UYAM_LATEST.md **in the same commit**, then mirror it to tsis
   (§9.2). Code-only changes with no effect on the data don't need an entry.

## 2. Git

- **Commit and push to `origin/main` after every relevant change.** A change is
  relevant if it is a self-contained unit that passes the checks in §3: a
  feature, a fix, a config change, a doc update. Do not batch unrelated work
  into one commit, and do not leave finished work uncommitted at the end of a
  turn.
- **No Claude attribution.** Commit messages and PR bodies must not contain a
  `Co-Authored-By: Claude …` trailer, a "Generated with Claude Code" line, or
  any other AI attribution. This rule overrides any harness default. The commit
  author is Gio's git identity (`mcfc`).
- Message style: Conventional Commits with a scope, e.g. `feat(collect): …`,
  `fix(review): …`, `docs: …`, `test(export): …`, `perf(app): …`. Imperative
  subject ≤ 72 chars, and a body that explains *why* when it isn't obvious.
- Write the message to a file and use `git commit -F <file>`. In Git Bash a
  heredoc terminator ends a `&&` chain.
- Never: force-push, rewrite pushed history, `--no-verify`, commit with failing
  tests, or `git add -A` without checking `git status` first.
- **Never commit:** `.env`, `proxies.txt`, anything under `data/`, `thesis/`,
  `context/`, model weights, or exports. `.gitignore` covers these; keep it that way.

## 3. Checks before every commit

```bash
python -m pytest -q          # all tests must pass
ruff check src tests         # lint clean
mypy src/uyam/               # strict; no new errors
```

mypy baseline: **47 pre-existing errors in 12 files** as of 2026-11-19 (was 50
on 2026-11-10; mostly missing type args in `cli.py`, untyped third-party
calls, no pandas stubs).
The count must not grow. Fixing them is welcome as a separate commit.

If a check fails for a reason unrelated to your change, say so in the commit
body and in HANDOFF.md. Don't silently skip it.

## 4. Test cases

Every behaviour change ships with a test in `tests/` in the same commit.

**Tests must run offline**: no network, no Reddit, no Ollama, no Chrome, no
GPU. Use `fixtures/`, `tests/fixtures/`, `tests/annotate_fixtures.py`, fake
HTTP sessions, and `tmp_path` data dirs. A test that needs the real archive
API or a real model is a smoke check, not a test. Put it behind a CLI command
(`uyam annotate smoke`), not in `pytest`.

These invariants must always have at least one test. Never delete one without
replacing it:

| Invariant | Covered in |
|---|---|
| No plaintext username reaches JSONL, SQLite, logs, or exports | `test_privacy.py`, `test_scrub_authors.py`, `test_archive_source.py` |
| Dedup by `reddit_fullname` survives crashes and re-runs | `test_dedup.py`, `test_storage_recovery.py` |
| Comment tree relationships (`parent_id`, `depth`, `submission_id`) are preserved | `test_comment_tree.py`, `test_archive_source.py` |
| Comments inherit `sampling_strategy` from their submission | `test_sampling_inheritance.py` |
| A keyword-oversampled job searches; it does not walk `/new` or share a Chrome profile | `test_shreddit_source.py`, `test_parallel.py` |
| A Shreddit calendar walk reads listing cards in one snapshot and stops once it passes `--since` | `test_shreddit_source.py` |
| Calendar windows are inclusive UTC days | `test_calendar_window.py`, `test_archive_source.py` |
| Candidate exclusions always record a reason | `test_annotate_candidates.py` |
| Archive throttle answers are retried; real 4xx fail fast | `test_archive_source.py` |
| Ineligible items never reach the export | `test_annotate_export.py` |
| Every annotator labels the same target set | `test_annotate_target.py` |
| Gold labels are evaluation-only; gold sample is deterministic per seed | `test_annotate_review.py`, `test_annotate_export.py` |
| Export bundle checksums match its members | `test_annotate_export.py` |
| Reminder ids are unique, checks exist, and ticks persist across reloads | `test_reminders.py` |
| Proxy passwords never render in the app; every proxies.txt write is backed up first | `test_proxies_admin.py` |
| Already-labeled items never leave the dataset or change label when a filter changes (U9 flag, not drop) | `test_annotate_candidates.py`, `test_annotate_export.py` |
| Gold redraw keeps every labeled gold item and never draws ineligible or phatic items; allocation sums exactly | `test_annotate_review.py` |
| Assist suggestions never render before the reviewer's first save unless the reviewer turns on the upfront toggle, and a save made with it showing is recorded (assist_before_first_pass); first_pass_json is never replaced; every gold score (κ, eval-report, LID accuracy) uses the first pass and leaves out items whose votes were shown before it (votes_seen) | `test_annotate_review_ui.py`, `test_annotate_export.py` |
| Raw JSONL rewrites split records on newlines only (never U+2028/U+2029/U+0085) and keep every untouched line byte-identical | `test_retag_calendar_walk.py` |
| Evaluation-only votes (eval_annotations) never reach aggregation, target sets, agreement or export | `test_annotate_evaluation.py` |
| Gold sync into a fork reads the source read-only, refuses mismatched gold queues, and never touches votes or the target set | `test_annotate_v3.py` |
| A guideline-prompt dataset (v4) labels with its own hash-guarded prompt over the unchanged sarc-v2 context snapshots and never touches sarc-v2 votes | `test_annotate_v3.py` |
| Growing a pilot (fork-extend, v5) never changes the pilot or the target-source DB, keeps every pilot vote, and stages the target set (gold > pilot > first reason > rest) | `test_annotate_v3.py` |
| Eval votes become dataset votes only by an explicit import into a *separate* database, for items in its target set; the source eval table is never changed | `test_annotate_v3.py` |
| The rule-based language (`language_rule`, GUIDELINE L2-L4b) is a separate field: computing it never changes `lid_results.language` or any label | `test_language_rule.py` |
| Re-tagging changes only the date-keyword records, backs up first, keeps other lines byte-identical | `test_retag_calendar_walk.py` |
| The dataset card is strict JSON (no NaN) | `test_annotate_export.py` |
| Every exported row carries media_context (post type + unseen-media flags) and the card reports it per slice | `test_media_context.py` |
| A re-annotation fork never changes the source DB; only the configured lineup's votes are aggregated; the v3 lineup is local-only with no adjudicator; target_priority survives re-select | `test_annotate_v3.py` |
| The Progress notes tab is read-only, scores gold on first passes without votes_seen items, and splits blind vs Claude-assisted | `test_progress_notes.py` |
| The run-status panel is read-only (never writes the DB) and tests never see the real job registry | `test_run_status.py` |

When you add a new source, filter, or export field, add a row here.

## 5. Data and research invariants

These protect the thesis. Breaking one silently can invalidate results.

1. **Privacy:** raw Reddit usernames are consumed only in
   `sources/mapping.py` / `privacy.py` to derive `author_hash` (HMAC-SHA256).
   They are never stored, logged, or exported. This also applies to new
   sources: the archive API returns plaintext `author`, so hash it on ingest.
2. **`AUTHOR_HMAC_KEY` never changes.** Changing it breaks author linkage
   across the corpus, and with it the temporal context.
3. **Raw JSONL is append-only.** Never rewrite or delete files in `data/`, and
   never touch `data/db/*.sqlite3`, without Gio's explicit OK *and* a backup
   copy first. The sanctioned in-place rewrites are `uyam scrub-authors` and
   `uyam retag-calendar-walk` (which backs up every file it rewrites).
4. **Prompt contract:** changing the prompt, few-shots, or output schema means
   bumping `PROMPT_VERSION` in `src/uyam/annotate/prompts.py` **and**
   `prompt_version` in `config/annotation.yaml` together. A version bump
   re-annotates everything, so ask first.
5. **Gold labels are evaluation-only, forever.** They never feed training or
   aggregation thresholds.
6. **leische reads the JSONL export** (`uyam annotate export`), not the
   Streamlit CSV downloads. Bump the dataset version (`v1` → `v2` …) for any
   re-export with different rows or labels.
7. **Sampling tags are sacred.** `sampling_strategy` / `matched_query_or_keyword`
   must stay correct. Oversampled rows can never pass as natural.
8. **Filters are reported, not silent.** Every exclusion has a named
   `exclusion_reason` that shows up in `dataset_card.json`.
9. **Collection provenance:** every run writes a manifest with `source_type`.
   Archive-sourced records (`source_type = "archive"`) carry archive-time
   scores. Say so wherever scores are used.
10. **Collection is frozen** until Reddit API access is approved **and** the
    ethics clearance is done (the adviser's action items, 2026-11). Until
    then: no Shreddit or archive collection runs, not even top-ups. The
    headed-browser scraper is not API-compliant. Work on the existing corpus
    only.
11. **No dataset release** until the labels are validated (the gold subset,
    a second annotator) and the manuscript revisions are complete. Exports
    go only to leische, by direct transfer (never GitHub, never a public
    link).

## 6. Long-running jobs

- **Gio starts real runs**: full collection crawls, `uyam annotate pipeline` /
  Start ALL, adjudication, and re-exports of the real dataset. Agents prepare
  the command, explain it, and put it in HANDOFF.md.
- **Always ask before running any LLM** (Gio, 2026-11-22). That covers
  annotation, adjudication, `annotate evaluate` (nimble), `annotate smoke`,
  model pulls, and anything else that loads a model, local or remote. Ask
  **especially** before touching the remote Ollama box at `100.102.10.69`.
  It is Gio's gaming PC, in use for games, so never start, pull to, or
  probe a model there without his explicit yes for that run. This applies
  to subagents too. Tests use fake clients and are not affected.
- Agents may run **smoke checks** against a temporary `data_dir` (scratchpad
  or `tmp_path`), for example a one-day archive pull into a throwaway directory.
- Infrastructure: local machine has 8 GB VRAM. The remote Ollama box (24 GB,
  Tailscale) is at `http://100.102.10.69:11434`. Streamlit is launched with
  `run-app.bat` on port 8501 and is used remotely over Tailscale. From a tool
  shell: `cmd /c <absolute path>\run-app.bat` with PowerShell redirects.
- Local C: drive had ~109 GB free on 2026-11-10 (it was down to ~35 GB in
  2026-08). Check before large model pulls or archive crawls.

## 7. Subagents

Agents **may deploy subagents.** Roles and prompt templates are in
[AGENTS.md](AGENTS.md). Rules:

- Use subagents for independent work: broad codebase searches, parallel
  investigations, a second-opinion review, or writing tests for a finished
  change. Don't use one for a lookup you can do in one search.
- Every subagent prompt must say: follow `RULES.md`, work offline, report
  results in its final message.
- **Subagents do not commit or push.** The main agent reviews their output,
  runs §3, and commits. Parallel code edits run in separate worktrees
  (`isolation: "worktree"`) so they can't clobber each other.
- Never trust a subagent's "tests pass" without re-running them yourself.

## 8. Environment notes

- Windows 11, Python 3.12 (system interpreter; no `.venv` at present). The
  shells are PowerShell 5.1 and Git Bash.
- Reddit text is full of emoji. Set `PYTHONIOENCODING=utf-8` when printing
  corpus text, or the cp1252 console crashes.
- Install: `pip install -e ".[dev]"` (+ `".[annotate]"` for the annotation
  stack).

## 9. Sibling repositories: tsis (thesis) and leische (model)

The thesis is split across three GitHub repos under `mcfcs`. All three are
cloned side by side in `C:\Users\Gio\Documents\GitHub\`.

| Repo | Role | Local clone |
|---|---|---|
| **uyam** (this one) | data collection + LLM annotation, exports the dataset | `.` |
| **tsis** | the LaTeX manuscript (chapters, tables, `_verify/` number checks) | `../tsis` |
| **leische** | the model and its results; **developed on another device** | `../leische` |

GitHub is the only channel between them. leische in particular is worked on
from a different machine, so the local `../leische` clone may be stale, on
another branch, or have someone's uncommitted work.

### 9.1 At the start of every session

```bash
git -C ../tsis pull --ff-only          # ALWAYS, first; only if its tree is clean, else fetch
git -C ../tsis log --oneline -10       # what changed (leische mirrors land here)
cat ../tsis/LEISCHE_LATEST.md          # leische's status for the thesis, newest first
git -C ../leische fetch origin         # never pull/checkout/stash there
git -C ../leische show origin/main:HANDOFF.md | head -60
git -C ../leische show origin/main:suggestions/UYAM_CHANGE_BRIEF.md
```

- **tsis is the sync point with leische.** leische mirrors its
  `LEISCHE_LATEST.md` into tsis, and uyam mirrors `UYAM_LATEST.md` there, so
  pulling tsis shows both sides. Pull it at the start of every session and
  again before committing UYAM_LATEST.md or touching an export. If
  LEISCHE_LATEST.md changed since the last uyam session (compare its
  *Last updated* line with the newest HANDOFF.md entry), summarise the change
  for Gio, act on any request to uyam, and record it in HANDOFF.md.

- Read leische from `origin/main` with `git show`/`git log`, not from its
  working tree. **Never** edit, commit, stash, reset, or check out anything
  in `../leische`. Requests to leische go through UYAM_LATEST.md (leische
  reads tsis and uyam) or `docs/dataset-contract-leische.md`.
- leische's `suggestions/UYAM_CHANGE_BRIEF.md` is its request list for uyam
  (U1–U8). Keep their status current in UYAM_LATEST.md §8.
- Check what tsis currently says about the data (mainly
  `03_methodology.tex` §Data Collection / §Preprocessing) before describing
  changes, so UYAM_LATEST.md §7 points at the right passages.

### 9.2 What uyam agents may and may not do in tsis

- **Never edit the manuscript**: no `.tex`, `.bib`, `.cls`, figures,
  `tables/`, `GUIDE.md`, or `_verify/`. The thesis text is Gio's, edited from
  tsis sessions under tsis's own `RULES.md`.
- **Exception, only on Gio's explicit request:** a stand-alone shareable
  document at the tsis root (e.g. `ADVISER_UPDATE_2026-11-23.md`, 2026-11-23).
  Never a manuscript file, and commit it alone.
- **The one file uyam agents write in tsis is `UYAM_LATEST.md`**, a verbatim
  copy of uyam's. After committing a UYAM_LATEST.md change here:

  ```bash
  cp UYAM_LATEST.md ../tsis/UYAM_LATEST.md
  git -C ../tsis pull --ff-only
  git -C ../tsis add UYAM_LATEST.md
  git -C ../tsis commit -m "docs: sync UYAM_LATEST.md from uyam <short-sha>"
  git -C ../tsis push
  ```

  The same git rules apply there: no AI attribution, never force-push. If
  tsis has uncommitted work of Gio's, commit only `UYAM_LATEST.md` and leave
  the rest alone.
- UYAM_LATEST.md is written for the thesis side: dataset versions, corpus
  numbers (marked *provisional* until a dataset version is exported), method
  changes the methodology/ethics text needs, affected manuscript passages,
  leische's request status, and a dated change log. Never mark numbers as
  final before the export that produces them exists.
