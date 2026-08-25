# How the Shreddit pipeline works

Uyám’s live collector (`--source shreddit`) does **not** use the Reddit API (PRAW). It drives a real Google Chrome window against `www.reddit.com` the same way a logged-out person would, then maps what the page already rendered into the same `SubmissionRecord` / `CommentRecord` schema as every other source.

This note covers three things: the pipeline around the scraper, how Reddit’s bot checks are handled, and the scrape itself.

---

## 1. End-to-end pipeline

```
Streamlit / CLI
    → one worker process per subreddit (optional parallel)
        → ShredditBrowserSource  (Chrome + proxy)
            → listing (/new, /hot, /top, or calendar walk of /new)
            → per post: JSON-in-browser, then comments page if needed
        → mapping.py  (normalize + HMAC author)
        → pipeline.py
            → SQLite UNIQUE(reddit_fullname)  [reserve id first]
            → append JSONL  data/raw/<subreddit>/<YYYY-MM-DD>.jsonl
            → manifest JSON  data/manifests/<run-id>.json
```

| Piece | Role |
|---|---|
| `uyam.cli collect` / Streamlit **Start Collection** | Builds a job per subreddit and launches workers. |
| `uyam.parallel` | One OS process + one Chrome profile per subreddit. |
| `ShredditBrowserSource` | Browser, proxies, listing, harvest. |
| `uyam.pipeline.run_collection` | Dedup, JSONL, run stats. |
| `data/db/collection.sqlite3` | Authoritative “already stored” index. |
| `data/.scrape-status/` | Pause / stop, live log, captcha screenshot for the website. |

The pipeline never imports Playwright. It only sees `RedditSource.iter_submissions` / `iter_comments`. Switching `--source fixture` or `--source reddit` does not change storage.

**Re-runs do not duplicate.** SQLite inserts `reddit_fullname` (`t3_…` / `t1_…`) **before** a JSONL line is written. A second Start Collection skips ids already in the index. The website also hides leftover duplicate lines by fullname.

---

## 2. Why a browser at all?

Unauthenticated `https://www.reddit.com/r/{sub}/new.json` (and similar) is blocked or CAPTCHA-gated. A bare HTTP client with a custom User-Agent is an easy bot tell.

The current site (internally called **Shreddit**) server-renders the fields we need as attributes on custom elements:

- `<shreddit-post id="t3_…" post-title="…" score="…" author="…" permalink="…" …>`
- `<shreddit-comment thingid="t1_…" parentid="…" depth="…" …>`

So the collector reads the DOM (and, when the session allows it, JSON fetched **inside** that same Chrome context) instead of a public API.

---

## 3. How bot protection is handled

This is not an exploit and captchas are **not** auto-solved. The idea is: look like a normal headed Chrome session, and if Reddit still challenges, wait for a human.

### 3.1 Headful system Chrome + Patchright

1. **Real Chrome** (`channel="chrome"`), not a default bundled Chromium, unless Chrome is missing.
2. **Patchright** (a Playwright fork) patches automation fingerprints at the CDP layer (the usual `navigator.webdriver` / leaky Playwright tells).
3. **Headed window** by default. Headless Chrome is a common block. A visible window is required so you can click a challenge.
4. **Persistent profile** under `data/.browser-profile` (or `data/.browser-profiles/<subreddit>` when parallel). Cookies / challenge state survive relaunches. A probe profile may be copied in on first use.
5. **No extra headers and no fake User-Agent.** Those are fingerprint tells. Chrome sends its own.

### 3.2 Human captcha

If the page looks like “prove your humanity” / “whoa there” / etc.:

- The scraper **pauses** (default wait from `collection.yaml` / the website).
- It keeps Chrome in front and writes a screenshot to `data/.scrape-status/captcha.png`.
- Streamlit shows that snapshot. You solve it **in the Chrome window**, not in the website.
- Pause / Stop still work via `data/.scrape-status/control.json`.

There is no captcha-solving service.

### 3.3 Proxies (`proxies.txt`, required)

There is **no direct-connection fallback**. Every live shreddit session goes through the pool.

| Failure | What happens |
|---|---|
| Tunnel / connection refused / 407 | Proxy marked **dead** for this run; Chrome relaunched on the next one. |
| HTTP 429, `ERR_HTTP_RESPONSE_CODE_FAILURE` | **Cooldown** (~45s), immediately switch IP. Not permanently killed. |
| Timeout / empty Playwright `Error` | Retry the **same** proxy a few times, then cooldown-rotate. |
| Captcha / block page | Cooldown-rotate so another IP can try. |

The pool wraps. After cooldown, an IP can be used again. Parallel workers start at different offsets in `proxies.txt`.

### 3.4 JSON “in the browser”

Listing and comments JSON (`/r/{sub}/new.json`, `/comments/{id}.json`) are requested with Playwright’s `page.request.get` — **same cookies, proxy, and TLS session as the visible tab**. A standalone `requests` call would 403; the browser context often succeeds after the challenge is passed.

If comments JSON returns 429, the HTML comments page on that same IP will too. The harvester then **skips DOM** for that post and stores whatever listing JSON already has, instead of burning proxies on one URL.

---

## 4. How a scrape actually runs

### 4.1 Launch

1. CLI / Streamlit starts `uyam collect --source shreddit …`.
2. For several subreddits, **one process per subreddit** (default up to 3 browsers).
3. Each process: load proxies → Patchright → persistent Chrome → first `page.goto` of the listing.

### 4.2 Listing (normal mode)

Listing type is `/new`, `/hot`, `/top`, or search.

1. Navigate to the listing URL; wait for `<shreddit-post>` or a captcha pause.
2. Prefer **JSON pagination** (`after=` tokens, up to the limit).
3. If JSON is empty, **scroll the feed** and read `<shreddit-post>` `id` / `permalink`.
4. For each permalink, skip `t3_{id}` already in SQLite **before** opening comments.

### 4.3 Calendar mode (From date → Until, N posts/day)

Reddit **search `timestamp:unix..unix` is dead** on the current UI (it searches the literal string and returns no results). Calendar mode does **not** use search.

It walks **`/r/{sub}/new/` newest-first**, buckets `created_utc` into UTC days, keeps up to N **new** posts per day, and **stops** once posts are older than From.

Example: From `2026-08-01`, 15/day, until today → 15 newest unseen posts per UTC day, then halt at 1 Aug.

### 4.4 Harvesting one post

For each listing card that is in-window and not yet stored:

1. **Comments JSON** in the browser session (fast, full fields).
2. If JSON is missing/short and “all replies” is on, **open the comments HTML**, click Shreddit “more comments” partials, parse `<shreddit-comment>`.
3. If HTML `goto` fails but listing JSON exists, **keep the post** (and any JSON comments). Do not rotate eight proxies for that one thread.
4. Map through `mapping.py` (HMAC `author_hash`, keep `author` for this project’s schema).
5. Pipeline: `mark_seen` → append JSONL.

Comments preserve `parent_id` / depth so reply-to-post vs reply-to-reply is recoverable (`post_id`, `parent_comment_id`, URLs in the website).

### 4.5 Navigation policy (comment pages)

`page.goto(..., wait_until="commit")` is used because full `domcontentloaded` is flaky through residential proxies.

A generic Playwright `Error` (often with an empty `str()`) is treated as **transient**, not “proxy dead”. Rate-limit responses switch IP immediately.

### 4.6 Stop / pause / time limit

Workers poll `check_control()`:

- **pause** — sleep until Resume.
- **stop** — raise `ScrapeStopRequested`.
- **deadline** — optional wall-clock `--max-seconds` / website time limit.

---

## 5. Data on disk

```
data/
  raw/<Subreddit>/YYYY-MM-DD.jsonl    # one normalized record per line
  manifests/<uuid>.json               # per-run counts and errors
  db/collection.sqlite3               # dedup + run history
  .browser-profile/                   # default Chrome profile
  .browser-profiles/<Subreddit>/      # parallel workers
  .scrape-status/                     # log, control, captcha.png
```

JSONL is append-only. Schema is Pydantic (`src/uyam/models.py`). `collected_at` is scrape time (`retrieved_at_utc`).

---

## 6. Website

```powershell
$env:PYTHONPATH = "src"
streamlit run src/uyam/app.py
```

Live collection is a **background CLI process** so Pause/Stop stay clickable. The page tails `collect.log`, shows the captcha snapshot, and refreshes tables from JSONL. Streamlit is configured **not** to reload when `data/` changes (that used to kill the UI when the scraper wrote files).

---

## 7. What this is not

- Not the official Reddit Data API / not PRAW (that is `--source reddit`).
- Not Pushshift.
- Not captcha auto-solve.
- Not a guarantee Reddit will never challenge. Headful Chrome + proxy + human challenge is the baseline; blocks still happen (429, “whoa there”, dead proxies).

If Chrome never opens, check that Google Chrome is installed and `proxies.txt` has at least one working HTTP proxy (`host:port:user:pass` or URL form).
