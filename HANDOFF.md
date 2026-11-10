# Session handoff

The chat-to-chat log for agents working in uyam. Read *Current state* and the
newest session entry before starting. Update all three sections before ending
a chat (see [RULES.md §1](RULES.md)).

> Not to be confused with [UYAM_HANDOFF.md](UYAM_HANDOFF.md), which holds the
> model repo's (leische's) requests to uyam and uyam's responses.

---

## Current state (as of 2026-11-10)

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
- Short phatic comments (thanks / congrats / HBD / greetings): ~395 eligible,
  ~371 annotated, 23 labeled sarcastic.

## In progress

**Temporal extension, 2026-06-01 → 2026-11-10.** Reddit's `/new` listing only
reaches a few days back, so the historical window comes from the **Arctic
Shift** archive API (`--source archive`). Records are hashed with the same
`AUTHOR_HMAC_KEY`, so authors link across both sources and the temporal channel
thickens for the already-labeled Aug–Sep rows.

**Cleaning.** A `phatic` candidate-exclusion reason for short
greeting/thanks/congrats-only items. Phatic items that already carry a
sarcastic vote stay eligible and go to the human `low_confidence` queue
instead of being dropped. Export skips items that are no longer eligible.
Re-export as `dataset-v2`.

## Next steps (for Gio unless marked)

See the newest session entry for the exact commands.

---

## Session log

### 2026-11-10 — rules, agents, handoff; temporal extension + cleaning plan

**Decisions (Gio):**
- New collection window: **2026-06-01 → 2026-11-10** (backfill before August
  and fill the Sep 4 → Nov gap).
- Source for historical dates: **Arctic Shift archive API**. It's a
  third-party Reddit archive, so its use has to be stated in the
  methodology/ethics section.
- Cleaning: a new `phatic` exclusion reason, reported in the card. Phatic items
  that are already labeled sarcastic go to human review, not out of the dataset.
  Re-export as v2.
- The September export-bundle work was committed (`a8d184d`).

**Done:**
- `a8d184d` feat(export): zip bundle with SHA256SUMS (September work, now committed).
- RULES.md, AGENTS.md, HANDOFF.md, CLAUDE.md added.

**Findings:**
- Arctic Shift covers all three subreddits from 2026-06 to ~2 days ago, and
  every field the mappers need is present (comments include `depth`). Scores
  and `num_comments` are archive-time snapshots. The API throttles bursts with
  `{"data": null, "error": "Timeout. Maybe slow down a bit"}`, so it needs
  pacing and retry.
- The corpus is 2026-08-01 → 2026-09-04, not June as assumed.

---

<!-- Session entry template — copy to the top of the log:

### YYYY-MM-DD — <one-line topic>

**Decisions (Gio):** …
**Done:** <commit hash> <subject> …
**Findings:** …
**Left open / blocked:** …
**Commands for Gio:** …
-->
