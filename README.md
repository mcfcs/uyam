# Uyám — Reddit Data Collection Pipeline

**Uyám** is the data-collection subsystem for an undergraduate Computer Science thesis on **context-aware sarcasm detection in Tagalog-English (Taglish) code-switched social-media discourse**.

This collector gathers Reddit submissions and comments from Filipino discourse communities, normalizes them into a reproducible schema, pseudonymizes author identifiers, and stores them as structured JSONL + SQLite for downstream annotation and modeling.

---

## Architecture Overview

```
Reddit API (PRAW)    Shreddit HTML (Chrome)    Fixture JSON
      ↓                      ↓                       ↓
 PrawRedditSource   ShredditBrowserSource     FixtureRedditSource
           ↘                 ↓                 ↙
          CollectionRequest / RedditSource (Protocol)
                     ↓
              Normalization  (shared mappers in sources/mapping.py)
              Pseudonymization (HMAC-SHA256)
                     ↓
          Deduplication (SQLite)
                     ↓
          JSONL Storage + Manifest
```

**Source-adapter pattern:** downstream code depends only on the `RedditSource` protocol. Switching sources is a `--source` flag change — no processing code changes.

**Sources:**

| `--source` | Credentials | How | Use when |
|---|---|---|---|
| `fixture` | none | Local JSON | Offline development and tests |
| `shreddit` | none (proxies.txt required) | Headful Chrome, Shreddit HTML | Live collection without a Reddit API app |
| `public` | none (proxies.txt required) | Alias of `shreddit` | Same as shreddit |
| `reddit` | API app | PRAW | Optional official API path |

Reddit's unauthenticated `.json` endpoints are blocked. `shreddit` reads the same custom elements a logged-out browser sees (`<shreddit-post>`, `<shreddit-comment>`).

---

## Installation

```bash
# Clone and set up a virtual environment
git clone https://github.com/mcfcs/uyam.git
cd uyam
python -m venv .venv
.venv\Scripts\activate      # Windows
# or: source .venv/bin/activate  # Linux/macOS

pip install -e ".[dev]"

# Shreddit live scrape uses Patchright + your installed Chrome
# (no extra browser download if Google Chrome is already installed)
```

**Requires Python 3.11+.** Chrome must be installed for `--source shreddit`.

---

## Configuration

### Environment Variables (`.env`)

Copy `.env.example` to `.env` and fill in your values:

```bash
cp .env.example .env
```

```ini
REDDIT_CLIENT_ID=your_client_id
REDDIT_CLIENT_SECRET=your_client_secret
REDDIT_USER_AGENT=python:uyam-collector:v0.1.0 (by /u/your_reddit_username)
AUTHOR_HMAC_KEY=your_hmac_key  # generate: python -c "import secrets; print(secrets.token_hex(32))"
```

- `.env` is gitignored and must never be committed.
- `AUTHOR_HMAC_KEY` must remain constant across all collection runs. Changing it makes previously stored author hashes irreconcilable.

### Collection Config (`config/collection.yaml`)

Controls which subreddits to collect from, listing type, comment settings, and oversampling:

```yaml
subreddits:
  - Philippines
  - CasualPH
  - OffMyChestPH

collection:
  listing: new
  limit_per_subreddit: 100

comments:
  enabled: true
  max_comments_per_submission: 100
  replace_more_limit: 32   # PRAW MoreComments expansion limit
```

---

## Fixture Workflow (No Credentials Required)

The fixture source runs the entire pipeline — normalization, pseudonymization, deduplication, JSONL writing — without Reddit credentials.

```bash
# Validate fixture data (strict mode by default)
uyam validate-fixtures

# Collect from fixtures
uyam collect --source fixture

# Check status
uyam status
uyam runs
```

See [`fixtures/README.md`](fixtures/README.md) for instructions on replacing placeholder entries with real Reddit posts permitted under your research protocol.

---

## Live Shreddit scrape (no Reddit API app)

`--source shreddit` (and `--source public`, an alias) drives a **headful Chrome window** through **proxies.txt**, then:

1. Opens `/r/{sub}/{new|hot|top|search}/` and scrolls the feed until the submission limit.
2. Visits each post's comments page.
3. Reads `<shreddit-post>` / `<shreddit-comment>` attributes (id, title, body, score, upvote-ratio, author, created, permalink, flair, parent/depth, is-op, …).
4. Clicks Shreddit's `more-comments` partials to expand truncated threads.
5. Maps into the same `SubmissionRecord` / `CommentRecord` schema as every other source.

```bash
# proxies.txt is required and is used by default
copy proxies.example.txt proxies.txt   # then add real proxies

uyam collect --source shreddit --subreddit Philippines --listing new --limit 10
```

On first run, if `AUTHOR_HMAC_KEY` is missing it is **auto-generated and saved to `.env`**. Keep that key stable.

A Chrome window will open. That is intentional: headful Patchright + system Chrome is how the collector gets past Reddit's logged-out bot checks. Use `--headless` only if you know your proxies already pass.

**Needs:** Google Chrome installed, and at least one working HTTP proxy in `proxies.txt`. Dead proxies are marked unhealthy and the browser is relaunched on the next one.

---

## Live Reddit Workflow (API app — recommended)

> **You do not need "Reddit for Researchers"** (the slow approval program) for this. A self-service **"script" app** is instant and free.

**Create an app (~2 minutes):**

1. Go to reddit.com/prefs/apps → **create another app**
2. Type: **script**; redirect URI: `http://localhost:8080` (required but unused)
3. The string under the app name is `REDDIT_CLIENT_ID`; `secret` is `REDDIT_CLIENT_SECRET`

The collector runs **read-only** — only `client_id` + `client_secret` are needed (no username/password, no login), at ~100 requests/min. Put all four values in `.env` (`AUTHOR_HMAC_KEY` is auto-generated if omitted):

```bash
# Collect new posts from r/Philippines
uyam collect --source reddit --subreddit Philippines --listing new --limit 100

# Search for a keyword
uyam collect --source reddit --subreddit Philippines --search "sana all" --limit 100

# Use a proxy file
uyam collect --source reddit --subreddit Philippines --limit 100 --proxies proxies.txt
```

---

## Dataset Folder Layout

```
data/
  raw/
    Philippines/
      2026-08-15.jsonl      ← one normalized record per line, appended
    CasualPH/
      2026-08-15.jsonl
    OffMyChestPH/
      2026-08-15.jsonl
  manifests/
    <uuid>.json             ← one per collection run
  db/
    collection.sqlite3      ← deduplication + run tracking
```

Raw JSONL files are never overwritten; new records are appended. The date in the filename is the collection date (UTC).

---

## Schema Documentation

### SubmissionRecord (in JSONL)

| Field | Type | Description |
|---|---|---|
| `schema_version` | string | Schema version (e.g., `"1.0"`) |
| `record_type` | string | `"submission"` |
| `collection_run_id` | string | UUID of the collection run |
| `reddit_id` | string | Reddit post ID without prefix |
| `reddit_fullname` | string | `t3_{reddit_id}` |
| `subreddit` | string | Subreddit name |
| `title` | string | Post title |
| `selftext` | string | Body text |
| `created_utc` | datetime | Submission creation time (UTC ISO 8601) |
| `score` | int | Net upvotes |
| `upvote_ratio` | float | Upvote ratio 0.0–1.0 |
| `num_comments` | int | Total comments |
| `permalink` | string | Reddit permalink |
| `url` | string | Full URL |
| `is_self` | bool | True if text post |
| `over_18` | bool | NSFW flag |
| `spoiler` | bool | Spoiler flag |
| `stickied` | bool | Stickied by moderator |
| `locked` | bool | Locked from new comments |
| `archived` | bool | Archived (>6 months old) |
| `distinguished` | string? | `"moderator"`, `"admin"`, or null |
| `link_flair_text` | string? | Flair label |
| `is_original_content` | bool | OC flag |
| `num_crossposts` | int | Crosspost count |
| `author_hash` | string? | HMAC-SHA256 pseudonymized author identifier |
| `author_status` | string | `"pseudonymized"`, `"deleted"`, or `"unavailable"` |
| `retrieved_at_utc` | datetime | Time of collection (UTC ISO 8601) |
| `sampling_strategy` | string | `"natural"` or `"keyword_oversampled"` |
| `matched_query_or_keyword` | string? | Oversampling keyword, if applicable |

### CommentRecord (in JSONL)

| Field | Type | Description |
|---|---|---|
| `record_type` | string | `"comment"` |
| `reddit_id` | string | Comment ID without prefix |
| `reddit_fullname` | string | `t1_{reddit_id}` |
| `submission_id` | string | Parent submission ID (bare, no prefix) |
| `parent_id` | string | `t3_{id}` if top-level; `t1_{id}` if reply |
| `parent_record_type` | string | `"submission"` or `"comment"` |
| `subreddit` | string | Subreddit name |
| `body` | string | Comment text |
| `created_utc` | datetime | UTC ISO 8601 |
| `score` | int | Net score |
| `depth` | int | Nesting depth (0 = top-level) |
| `is_submitter` | bool | Author is the OP |
| `distinguished` | string? | Moderator/admin distinction |
| `stickied` | bool | Pinned comment |
| `controversiality` | int | Reddit controversiality score |
| `permalink` | string | Direct link to comment |
| `author_hash` | string? | Pseudonymized author identifier |
| `author_status` | string | `"pseudonymized"`, `"deleted"`, `"unavailable"` |
| `retrieved_at_utc` | datetime | UTC ISO 8601 |

---

## Pseudonymization Methodology

Raw Reddit usernames are **never** written to JSONL, SQLite, logs, or any output file. All author identifiers are pseudonymized using **HMAC-SHA256**:

```
author_hash = HMAC-SHA256(AUTHOR_HMAC_KEY, normalize(username))
```

where `normalize` lowercases and strips the username.

**Important:** The resulting identifiers are *pseudonymized*, not anonymous. The persistent HMAC digest can still link a user's contributions within this dataset. This linkage is intentional — it enables duplicate detection and research auditing — but must be accurately described in the thesis methodology.

The HMAC key is stored only in `.env` and never committed to the repository.

---

## Deduplication Behavior

Deduplication uses SQLite with a `UNIQUE` constraint on `reddit_fullname`. The system remains correct if:

- The collector crashes midway
- The collector is run twice with the same parameters
- The same submission appears in a keyword search and a `new` listing
- The same comment is encountered through multiple collection runs

**Failure model:** JSONL is written first, then SQLite is updated. If the process crashes between these two steps, the record exists in JSONL but SQLite allows re-collection on the next run, potentially creating a JSONL duplicate. This failure window is narrow. Since every JSONL line contains `reddit_fullname`, post-hoc deduplication during dataset construction can detect and remove these duplicates by fullname.

---

## Collection Provenance

Each collection run produces:

- A `CollectionContext` with UUID run ID, timestamps, source, subreddit, listing configuration, limits, sampling strategy, counts, and Git commit hash.
- A manifest JSON file at `data/manifests/<run_id>.json`.
- SQLite run records accessible via `uyam runs` and `uyam inspect-run <run_id>`.

The manifest supports the thesis methodology chapter by providing a machine-readable record of exactly how each dataset was collected.

---

## Comment and Context Relationships

The collector preserves all relational information needed for later context reconstruction:

```
SUBMISSION TITLE / BODY
↓
PARENT COMMENT (via parent_id)
↓
TARGET COMMENT
↓
REPLIES (via parent_id references)
```

These relationships are stored as IDs — comments are never concatenated during collection. The dataset-building stage decides what constitutes "context" for a given sarcasm sample.

Key fields for reconstruction:

| Field | Purpose |
|---|---|
| `submission_id` | Links comment to its submission |
| `parent_id` | Links comment to its direct parent |
| `parent_record_type` | Whether parent is submission or comment |
| `depth` | Nesting level in the comment tree |
| `reddit_fullname` | Stable identifier for graph traversal |

---

## Proxy Behavior

Live sources (`shreddit` / `public` / `reddit`) **always** read `proxies.txt` unless `--proxies` points elsewhere. The shreddit source **refuses to start** if the file is missing or empty.

Supported formats in `proxies.txt`:

```
http://host:port
http://username:password@host:port
host:port:username:password
```

Comments (`#`) and blank lines are ignored. If the file is missing or empty, the collector uses a direct connection. Proxy credentials are never logged — only `hostname:port` appears in log output.

See [`proxies.example.txt`](proxies.example.txt) for format examples.

---

## Rate Limiting

PRAW automatically respects Reddit's `X-Ratelimit-*` headers. The collector additionally implements bounded exponential backoff with jitter for network-level failures (connection errors, timeouts, transient 5xx):

- Max 5 retry attempts
- Delay: `min(60s, 2^attempt + jitter)`
- Not retried: authentication failures (401/403), not-found (404), malformed requests (400)

---

## Current Limitations

1. **Live HTML scrape** — `--source shreddit` needs Chrome + `proxies.txt`. Large threads are expanded by clicking Shreddit "more comments" (capped by `shreddit.more_comments_clicks`). `--source reddit` remains an optional PRAW path. Fixture mode remains fully offline.
2. **No recurring sync** — the collector does not automatically reconcile edited or deleted content. A future reconciliation workflow may be needed depending on Reddit's terms and thesis protocol requirements.
3. **Comment tree size** — `replace_more_limit=32` (default) expands up to 32 MoreComments objects per submission. For full trees, set `replace_more_limit: null` in `collection.yaml` (much slower; 1 API call per expansion, capped by Reddit at ~1 req/2s).
4. **Single-process** — no distributed crawling. SQLite + JSONL is intentionally sufficient for a thesis-scale dataset.

---

## Ethical and Research-Use Considerations

> Possession of Reddit API credentials does not itself determine whether collected content may be used for a particular research or machine-learning purpose. The researcher remains responsible for following Reddit's applicable terms, institutional ethics requirements, and the approved research protocol.

Key points:

- Reddit content may be edited or deleted after collection. The researcher is responsible for any reconciliation or deletion workflow required by Reddit's terms or institutional ethics approval.
- Author identifiers in this dataset are pseudonymized, not anonymous. The thesis must accurately describe this distinction.
- Collected data should be stored securely, shared only as permitted by the research protocol, and not used for commercial purposes without a separate Reddit Data API agreement.

---

## Reproducing a Collection Run

Each manifest (`data/manifests/<run_id>.json`) contains the complete configuration of a collection run including:

- Subreddit, listing type, search query
- Limits and comment settings
- Sampling strategy and oversampling keywords
- Collector version and Git commit hash
- Collection timestamps

To reproduce a run configuration, read the manifest and pass the same parameters to `uyam collect`.

---

## Pipeline Stages

```
Reddit API
      ↓
collection               ← uyam collect
      ↓
normalized raw records   ← JSONL in data/raw/
      ↓
candidate selection      ← uyam annotate index / select
      ↓
context reconstruction   ← uyam annotate run (thread context per target)
      ↓
annotation dataset       ← LLM ensemble + adjudication + human review
      ↓
sarcasm labels           ← uyam annotate aggregate / export
      ↓
train/validation/test split   ← leische repository
      ↓
modeling                      ← leische repository
```

The raw records contain all relational information (submission IDs, parent IDs, depths) needed to assemble context windows in later stages. No sarcasm classification occurs during collection.

---

## Annotation Pipeline (`uyam annotate`)

Turns the collected corpus into the labeled training dataset for the model
repository (leische). Three local/remote Ollama LLMs act as independent
annotators (sarcasm + literal/intended sentiment + language + cue flags per
item, full thread context in the prompt), a fast transformer classifier
provides a model-independent sentiment vote, disagreements go to a blind
large-model adjudicator, and a stratified gold subset is human-labeled in the
Streamlit "Annotation Review" tab.

```bash
pip install -e .[annotate]   # + CUDA torch: pip install torch --index-url https://download.pytorch.org/whl/cu124

uyam annotate index                    # raw JSONL -> annotation DB
uyam annotate select                   # candidate filters (bots, deleted, too short)
uyam annotate lid                      # fastText language ID (english/tagalog/taglish)
uyam annotate sentiment                # transformer literal sentiment (before local Ollama!)
uyam annotate smoke                    # verify endpoints + models end-to-end
uyam annotate run --annotator gemma3   # remote 24GB box    (run concurrently...)
uyam annotate run --annotator qwen3    # local 8GB          (...with these two,)
uyam annotate run --annotator sealion  # local 8GB          (sequentially local)
uyam annotate aggregate                # votes, kappa, escalation queue
uyam annotate adjudicate               # blind large-model pass over disagreements
uyam annotate aggregate
uyam annotate gold-sample              # then label in Streamlit "Annotation Review"
uyam annotate aggregate && uyam annotate export --version v1
uyam annotate status                   # progress dashboard at any point
```

Configuration lives in `config/annotation.yaml` (endpoints, models, prompt
version, filters, thresholds). All passes are resumable and idempotent. The
dataset contract for leische — file formats, label semantics, agreement
statistics, fold guidance — is documented in
[docs/dataset-contract-leische.md](docs/dataset-contract-leische.md).

---

## Running Tests

```bash
pytest --tb=short -v
```

All tests run without Reddit credentials. Test coverage includes fixture validation, end-to-end pipeline, deduplication, privacy guarantees, comment tree relationships, proxy parsing, and storage recovery.

```bash
# Lint
ruff check src tests

# Type checking
mypy src/uyam/
```

---

## Utilities

```bash
# Convert a Manila local time to UTC Unix seconds
python tools/to_unix.py "2026-08-15 14:30 Asia/Manila"
```
