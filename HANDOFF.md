# Session handoff

The chat-to-chat log for agents working in uyam. Read *Current state* and the
newest session entry before starting. Update all three sections before ending
a chat (see [RULES.md §1](RULES.md)).

> Not to be confused with [UYAM_HANDOFF.md](UYAM_HANDOFF.md), which holds the
> model repo's (leische's) requests to uyam and uyam's responses.

---

## Current state (as of 2026-11-10, end of session)

**Corpus (raw JSONL, `data/raw/`)**: 21,190 records (2,017 submissions,
19,173 comments). The Aug–Sep core is 1,954 submissions / 18,619 comments
from r/Philippines, r/CasualPH and r/OffMyChestPH, span **2026-08-01 →
2026-09-04**, plus one stray 2025-07 post, all natural-sampled. Plaintext
authors were scrubbed on 2026-09-08. The 2026-11-10 Shreddit trial appended
63 posts and 554 comments; 57 of those posts are mis-tagged (see below).

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

**Collection is complete (2026-11-19):** 588,507 records, Jun 1 → Nov 10, all
three subreddits. Nothing is running. Next is the v2 prep: index/select, U9
phatic flagging, U1, then the export.

- **Temporal extension, 2026-06-01 → 2026-11-10.** `uyam collect --source
  archive` pulls the window from the Arctic Shift archive. Authors are hashed
  with the same `AUTHOR_HMAC_KEY`, so they link to the Shreddit-era corpus.
  The goal is author history for the labeled Aug–Sep rows (temporal channel),
  not necessarily more labels.
- **Cleaning.** The `phatic` exclusion reason is live in `select`. Export now
  drops items that are no longer eligible. The real DB has **not** been
  re-selected yet.

## Next steps (for Gio unless marked)

These are also the to-dos in the Streamlit **Reminders** tab
(`config/reminders.yaml`), where Gio ticks them off. `data/reminders.json`
records which ones are done.

1. **Back up the annotation DB** (RULES §5.3), e.g.
   `copy data\db\annotation.sqlite3 data\db\annotation-2026-11-10.bak.sqlite3`.
2. **Archive backfill: resumed 2026-11-17 18:01 PHT** as a detached
   PowerShell sequence (pid 43812). The first run (2026-11-11 → 11-13)
   stored r/Philippines Jun 1 → **Oct 11** (20,266 posts / 361,635 comments)
   and r/CasualPH Jun 1 → **Aug 17** (1,949 / 13,515), then stopped. Causes,
   all fixed in `5b72a10`: an hour of throttling used up its budget, a
   Cloudflare 530 outage was treated as fatal, and the keyword jobs had lost
   their date window. The resume runs, in order:
   `--subreddit CasualPH --since 2026-08-18`, `--subreddit Philippines
   --since 2026-10-12`, `--subreddit OffMyChestPH --since 2026-06-01` (each
   `--until 2026-11-10 --per-day 300 --no-oversample`), then
   `--oversample-only --since 2026-06-01 --until 2026-11-10`.
   - Progress: `data/.archive-backfill.err.log`. The first run's logs are
     kept as `data/.archive-backfill-2026-11-11*.log`.
   - Stop: the app's Stop button. Resume: re-run from any earlier `--since`.
     Days whose posts are all stored no longer re-stream comments, so a
     resume costs only post listings.
   - Backups: `data/db/collection-2026-11-17.bak.sqlite3` (and `-11-11`).
   - The machine has to stay on and awake.
3. **Latest days: done 2026-11-11 (walk only).** Nov 8–10 is collected for
   all three subreddits. The **Shreddit keyword searches** were switched off
   for that test and are still to run:
   `uyam collect --source shreddit --oversample-only`.
   For later top-ups, or as a fallback if the archive is down:
   `uyam collect --source shreddit --since <YYYY-MM-DD> --per-day 40`.
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

The stopped trial's 63 posts / 554 comments are already in `data/raw/`.
**57 posts are mis-tagged** `keyword_oversampled` (the keyword was replaced
by the UTC day, e.g. `2026-11-10`) because those jobs walked `/new`. Six
posts are correctly `natural`. Raw JSONL was not rewritten. A later run
will skip those ids as duplicates. See the Reminders tab.

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
