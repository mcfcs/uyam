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

**Export (`data/annotated/`)**: still `dataset-v1` (2026-09-15). A trial v2
export to the scratchpad matched v1 (same 15,000 items, 0 label changes;
majority positives 2,747, rows differing 1,712, κ shipped vs majority 0.545,
48 h coverage 46.5%). **Not exported yet.** leische's LEISCHE_LATEST waits on
uyam's export, so waiting for leische's readiness left both sides waiting.
Exporting now is recommended; Gio runs `uyam annotate export --version v2`.
The v1 card is preserved as `data/annotated/dataset_card-v1.json`.

**Known gaps the thesis must address**: data didn't come through Reddit's
official API (ethics reminder); gold κ on 31 items is −0.148; guideline
decisions D2, D3 and D5 are open (D1 broad, D4 done).

## In progress

Nothing is running. Gio is labeling the 269 gold items. The v2 export waits on
Gio's go, then a leische chat (brief: `docs/briefs/LEISCHE_BRIEF.md`).

## Next steps (for Gio unless marked)

These are also the to-dos in the Streamlit **Reminders** tab
(`config/reminders.yaml`), where Gio ticks them off. `data/reminders.json`
records which ones are done.

1. **Label the 269 gold items** in Annotation Review (guideline v1.1): blind
   first, write `D1` in notes for wry-pose items, then optionally reveal
   Claude's suggestion. D2, D3 and D5 are open but don't block labeling.
2. **v2 export** (`uyam annotate export --version v2`), then transfer the
   bundle (Download bundle from the leische device over Tailscale, or
   Taildrop) and paste `docs/briefs/LEISCHE_BRIEF.md` into a leische chat.
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
