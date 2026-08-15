# Fixture Population Guide

## Important

> These records are **synthetic placeholders** for exercising the collection
> pipeline. Replace them only with material permitted under the approved
> research/data-collection protocol.

The `sample_posts.json` file ships with obviously fake placeholder content
(IDs like `FAKE001`, usernames like `placeholder_user_1`, titles like
`[PLACEHOLDER] Sample post`). Its purpose is to allow full end-to-end testing
of the pipeline — including normalization, pseudonymization, deduplication,
JSONL writing, and manifest generation — without requiring Reddit credentials.

---

## How to Populate the Fixture

Replace placeholder entries only with Reddit content you have collected through
normal Reddit browsing and that is permitted under:

1. Reddit's [Data API terms](https://www.redditinc.com/policies/data-api-terms)
   and [Developer terms of service](https://www.reddit.com/dev/api/terms)
2. Your institution's ethics approval / IRB protocol
3. Any additional requirements from your thesis supervisor

**Do not manufacture realistic Reddit usernames or fabricate post content
and present it as real.** The fixture is for pipeline testing, not for
constructing a synthetic dataset.

---

## Field Reference

Each entry in `submissions` maps directly to the raw Reddit API response shape.
The `FixtureRedditSource` runs every fixture entry through the same normalization
path as the live `PrawRedditSource`, so the fields mirror what PRAW exposes.

### Submission fields

| Field | Type | Notes |
|---|---|---|
| `id` | string | Reddit post ID without prefix (e.g., `abc123`) |
| `name` | string | Fullname with prefix: `t3_{id}` |
| `subreddit` | string | Subreddit name (no r/) |
| `title` | string | Post title |
| `selftext` | string | Body text; `""` for link posts |
| `created_utc` | number | Unix timestamp (UTC seconds) |
| `score` | number | Upvotes minus downvotes |
| `upvote_ratio` | number | 0.0 – 1.0 |
| `num_comments` | number | Total comment count from Reddit |
| `permalink` | string | `/r/subreddit/comments/id/slug/` |
| `url` | string | Submission URL |
| `is_self` | boolean | True if text post |
| `over_18` | boolean | NSFW flag |
| `spoiler` | boolean | Spoiler flag |
| `stickied` | boolean | Stickied by moderator |
| `locked` | boolean | Locked from new comments |
| `archived` | boolean | Archived (>6 months old) |
| `distinguished` | string or null | `"moderator"`, `"admin"`, or null |
| `link_flair_text` | string or null | Flair label or null |
| `is_original_content` | boolean | OC flag |
| `num_crossposts` | number | How many times crossposted |
| `author` | string | Raw username — will be pseudonymized; use fake names here |

### Comment fields

| Field | Type | Notes |
|---|---|---|
| `id` | string | Comment ID without prefix |
| `name` | string | Fullname with prefix: `t1_{id}` |
| `link_id` | string | Parent submission fullname: `t3_{submission_id}` |
| `parent_id` | string | `t3_{sub_id}` if top-level; `t1_{comment_id}` if reply |
| `subreddit` | string | Same as parent submission |
| `body` | string | Comment text |
| `created_utc` | number | Unix timestamp |
| `score` | number | Net score |
| `depth` | number | Nesting depth (0 = top-level) |
| `is_submitter` | boolean | Author is the OP |
| `distinguished` | string or null | Moderator/admin distinction |
| `stickied` | boolean | Pinned comment |
| `controversiality` | number | Reddit's controversiality score |
| `permalink` | string | Direct link to comment |
| `author` | string | Raw username — will be pseudonymized |

---

## Reddit IDs and the Prefix System

Reddit uses a prefix system to distinguish content types:

| Prefix | Meaning |
|---|---|
| `t1_` | Comment |
| `t2_` | Account |
| `t3_` | Submission (link/post) |
| `t4_` | Message |
| `t5_` | Subreddit |
| `t6_` | Award |

`parent_id` on a top-level comment is `t3_{submission_id}`.
`parent_id` on a reply is `t1_{parent_comment_id}`.

---

## Getting a Unix Timestamp

Use the bundled helper:

```bash
python tools/to_unix.py "2026-08-15 14:30 Asia/Manila"
```

---

## Subreddit Coverage

The fixture should cover:

- `Philippines` — general Filipino discourse
- `CasualPH` — casual Taglish conversation
- `OffMyChestPH` — emotional/rant content with sarcasm-rich discourse

When replacing placeholders with real posts, try to maintain coverage across
all three subreddits and include a variety of:

- Short and long selftext posts
- Highly upvoted and near-zero posts
- Posts with rich comment threads
- Examples of obvious sarcasm in the comment thread (for later annotation)

---

## Fixture File Location

```
fixtures/sample_posts.json
```

This file is committed to the repository so the test suite can run without
Reddit credentials. Raw usernames in the fixture are **fake** and will be
pseudonymized when the fixture runs through the pipeline. When you replace
placeholder entries with real posts, the real author names will similarly be
pseudonymized and never written to JSONL or SQLite.
