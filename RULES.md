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
   did not make, ask before touching it.
2. **During:** keep [HANDOFF.md](HANDOFF.md) honest. If you change a plan,
   number, or decision that it records, update it in the same commit.
3. **End:** add a new entry to the top of HANDOFF.md's session log (template
   at the bottom of that file), update its *Current state* and *Next steps*,
   and commit and push it. Every chat that changes anything ends with a
   handoff commit.

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

mypy baseline: **50 pre-existing errors in 12 files** as of 2026-11-10 (mostly
missing type args in `cli.py`, untyped third-party calls, no pandas stubs).
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
| Calendar windows are inclusive UTC days | `test_calendar_window.py`, `test_archive_source.py` |
| Candidate exclusions always record a reason | `test_annotate_candidates.py` |
| The phatic filter never drops an item an annotator voted sarcastic (it goes to review) | `test_annotate_candidates.py` |
| Archive throttle answers are retried; real 4xx fail fast | `test_archive_source.py` |
| Ineligible items never reach the export | `test_annotate_export.py` |
| Every annotator labels the same target set | `test_annotate_target.py` |
| Gold labels are evaluation-only; gold sample is deterministic per seed | `test_annotate_review.py`, `test_annotate_export.py` |
| Export bundle checksums match its members | `test_annotate_export.py` |

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
   copy first. `uyam scrub-authors` is the one sanctioned in-place rewrite.
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

## 6. Long-running jobs

- **Gio starts real runs**: full collection crawls, `uyam annotate pipeline` /
  Start ALL, adjudication, and re-exports of the real dataset. Agents prepare
  the command, explain it, and put it in HANDOFF.md.
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
