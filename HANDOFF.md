# Session handoff

The chat-to-chat log for agents working in uyam. Read *Current state* and the
newest session entry before starting. Update all three sections before ending
a chat (see [RULES.md §1](RULES.md)).

> Not to be confused with [UYAM_HANDOFF.md](UYAM_HANDOFF.md), which holds the
> model repo's (leische's) requests to uyam and uyam's responses.

---

## Current state (as of 2026-11-10, end of session)

**Corpus (raw JSONL, `data/raw/`)**: 20,573 records (1,954 submissions,
18,619 comments) from r/Philippines, r/CasualPH and r/OffMyChestPH, collected
with the Shreddit browser scraper. Span: **2026-08-01 → 2026-09-04**, plus one
stray 2025-07 post. All records are natural-sampled. Plaintext authors were
scrubbed on 2026-09-08.

**Annotation (`data/db/annotation.sqlite3`, prompt `sarc-v2`)**: 17,424
eligible candidates; 15,000 items aggregated, all resolved (unanimous 3,355 /
majority 6,759 / adjudicator 4,886); 1,601 sarcastic (10.7%). 50,201 LLM
annotation rows. Gold review: 31 of 300 queued items labeled by Gio.

**Export (`data/annotated/`, `dataset-v1`)**: written 2026-09-15 from commit
`039c3aa`, 15,000 rows. leische consumes this JSONL, not the Streamlit CSVs.

**Known gaps the thesis must address**:
- Collection window is ~5 weeks; the thesis scope says ≥3 months.
- Temporal-context coverage is thin: ~35% of rows have ≥1 prior post by the
  same author within 48 h.
- Gold subset is only 10% labeled, so no Cohen's κ yet (leische H1).
- Phatic noise (pure thanks / congrats / HBD / greetings): the new filter
  would exclude 107 eligible items and route 21 more, which carry a sarcastic
  vote, to review. That's not applied to the real DB yet.

## In progress

Nothing is running. The code for both workstreams is merged. The real runs
are Gio's (see *Next steps*).

- **Temporal extension, 2026-06-01 → 2026-11-10.** `uyam collect --source
  archive` pulls the window from the Arctic Shift archive. Authors are hashed
  with the same `AUTHOR_HMAC_KEY`, so they link to the Shreddit-era corpus.
  The goal is author history for the labeled Aug–Sep rows (temporal channel),
  not necessarily more labels.
- **Cleaning.** The `phatic` exclusion reason is live in `select`. Export now
  drops items that are no longer eligible. The real DB has **not** been
  re-selected yet.

## Next steps (for Gio unless marked)

1. **Back up the annotation DB** (RULES §5.3), e.g.
   `copy data\db\annotation.sqlite3 data\db\annotation-2026-11-10.bak.sqlite3`.
2. **Archive backfill.** Pick a size: r/Philippines ~115 posts/day,
   r/CasualPH ~27/day, r/OffMyChestPH unmeasured (assume ~100). The API
   allows about 1 request/s, plus one comment request per post.
   - full window, every post: roughly 40–55k posts, ~12–20 h
     `uyam collect --source archive --since 2026-06-01 --until 2026-11-10`
   - bounded: ≤40 posts/sub/day ≈ 19k posts, ~6–8 h
     `uyam collect --source archive --since 2026-06-01 --until 2026-11-10 --per-day 40`
   It resumes safely: dedup skips already-stored posts. The run also does the
   8 oversampling keywords as archive searches over the window afterwards.
   Run `--oversample-only` separately if the natural pass gets stopped.
3. **Latest days** (the archive lags ~2 days), or a fallback if the archive
   is down: `uyam collect --source shreddit --since 2026-11-08 --per-day 40`.
4. **Re-select + review.** `uyam annotate index && uyam annotate select`. The
   dry run predicts 107 `phatic` exclusions and 21 phatic items with a
   sarcastic vote sent to review (20 new `low_confidence` items; 1 is already
   in the gold queue). Label those, and keep going on the gold queue (31/300
   done), in the Annotation Review tab.
5. **Re-export as v2.** `uyam annotate aggregate && uyam annotate export
   --version v2`. Check `temporal_context_coverage` and `collection_window`
   in the card against v1 (35% ≥1 history post; 2026-08-01 → 09-04).
   Then tell leische v2 exists (bundle zip over Tailscale).
6. Optional: annotating new Jun–Nov items means raising
   `pipeline.target_items` above 12,000 and running `uyam annotate pipeline`.
7. Thesis text: state the Arctic Shift source and its ingest-time scores in
   the methodology/ethics section, plus the real window and the phatic filter.

**Open questions / ideas for agents:**
- Records don't carry their source; it's only in the run manifest. If leische
  needs per-row provenance (e.g. to ignore archive scores), add a
  `source_type` column to the corpus dump, joined from manifests.
- `compute_agreement` (κ/α) still counts every annotated item, including ones
  a later filter made ineligible. Consider restricting it to eligible items
  for v2.
- Keyword search through the archive returned 0 hits for "sana all" on one
  CasualPH day. That's plausible, but check the oversampled counts after the
  real run.

---

## Session log

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

---

<!-- Session entry template — copy to the top of the log:

### YYYY-MM-DD — <one-line topic>

**Decisions (Gio):** …
**Done:** <commit hash> <subject> …
**Findings:** …
**Left open / blocked:** …
**Commands for Gio:** …
-->
