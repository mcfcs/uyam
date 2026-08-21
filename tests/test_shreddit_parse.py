"""Tests for Shreddit HTML parsing and URL construction.

No browser is launched; these tests use a compact HTML fixture whose
attribute schema matches the live www.reddit.com HAR.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from uyam.dedup import DedupDatabase
from uyam.pipeline import run_collection
from uyam.privacy import pseudonymize_author, reset_hmac_key_cache
from uyam.sources.base import CollectionRequest
from uyam.sources.mapping import map_comment_dict, map_submission_dict
from uyam.sources.shreddit_parse import (
    comment_raw_from_shreddit,
    comments_url,
    listing_url,
    parse_shreddit_html,
    parse_shreddit_timestamp,
    submission_raw_from_shreddit,
)

FIXTURE = Path(__file__).parent / "fixtures" / "shreddit_comment_page.html"
TEST_KEY = "test-hmac-key-shreddit"


@pytest.fixture(autouse=True)
def set_hmac_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTHOR_HMAC_KEY", TEST_KEY)
    reset_hmac_key_cache()
    yield
    reset_hmac_key_cache()


class TestListingUrl:
    def test_new(self) -> None:
        assert listing_url(subreddit="Philippines", listing_type="new") == (
            "https://www.reddit.com/r/Philippines/new/"
        )

    def test_strips_r_prefix(self) -> None:
        assert listing_url(subreddit="r/CasualPH", listing_type="hot") == (
            "https://www.reddit.com/r/CasualPH/hot/"
        )

    def test_top_time_filter(self) -> None:
        assert listing_url(
            subreddit="Philippines", listing_type="top", time_filter="week"
        ) == "https://www.reddit.com/r/Philippines/top/?t=week"

    def test_search(self) -> None:
        url = listing_url(
            subreddit="Philippines",
            listing_type="search",
            search_query="sana all",
            sort="new",
            time_filter="year",
        )
        assert url.startswith("https://www.reddit.com/r/Philippines/search/?")
        assert "q=sana+all" in url
        assert "restrict_sr=1" in url
        assert "sort=new" in url
        assert "t=year" in url


class TestCommentsUrl:
    def test_relative_permalink(self) -> None:
        url = comments_url(
            "/r/Philippines/comments/1vthze0/x/", sort="confidence"
        )
        assert url == (
            "https://www.reddit.com/r/Philippines/comments/1vthze0/x/?sort=confidence"
        )

    def test_absolute_permalink_keeps_query_separator(self) -> None:
        url = comments_url(
            "https://www.reddit.com/r/Philippines/comments/abc/?foo=1",
            sort="new",
        )
        assert url.endswith("&sort=new")


class TestTimestamp:
    def test_iso_offset_without_colon(self) -> None:
        ts = parse_shreddit_timestamp("2026-08-20T12:22:10.758000+0000")
        assert abs(ts - 1787228530.758) < 1.0

    def test_unix_ms(self) -> None:
        ts = parse_shreddit_timestamp(1787228530758)
        assert abs(ts - 1787228530.758) < 0.01

    def test_unix_seconds(self) -> None:
        ts = parse_shreddit_timestamp(1752537600)
        assert ts == 1752537600.0


class TestParseHtmlFixture:
    def test_post_fields_from_har_schema(self) -> None:
        html = FIXTURE.read_text(encoding="utf-8")
        posts, comments = parse_shreddit_html(html, fallback_subreddit="Philippines")

        assert len(posts) == 1  # promoted post skipped
        post = posts[0]
        assert post["id"] == "1vthze0"
        assert post["name"] == "t3_1vthze0"
        assert post["subreddit"] == "Philippines"
        assert post["title"].startswith("Group of pickpockets")
        assert "Kabagang TV" in post["selftext"]
        assert post["score"] == 1765
        assert abs(post["upvote_ratio"] - 0.9834345665378245) < 1e-9
        assert post["num_comments"] == 147
        assert post["permalink"].startswith("/r/Philippines/comments/1vthze0/")
        assert post["url"] == "https://v.redd.it/b7jyctwzvikh1"
        assert post["is_self"] is False
        assert post["over_18"] is False
        assert post["link_flair_text"] == "ViralPH"
        assert post["author"] == "nayryanaryn"
        assert comments  # used below

    def test_comment_tree_relationships(self) -> None:
        html = FIXTURE.read_text(encoding="utf-8")
        _posts, comments = parse_shreddit_html(html, fallback_subreddit="Philippines")

        by_id = {c["id"]: c for c in comments}
        assert set(by_id) >= {"p4tq3wn", "p4twfc6", "p4t8ne3", "deleted1"}

        parent = by_id["p4tq3wn"]
        reply = by_id["p4twfc6"]
        sibling = by_id["p4t8ne3"]

        assert parent["depth"] == 0
        assert parent["parent_id"] == "t3_1vthze0"
        assert parent["link_id"] == "t3_1vthze0"
        assert "Patay gutom" in parent["body"]
        assert parent["is_submitter"] is False

        assert reply["depth"] == 1
        assert reply["parent_id"] == "t1_p4tq3wn"
        assert reply["is_submitter"] is True
        assert "lookout" in reply["body"]

        assert sibling["score"] == 40
        assert sibling["parent_id"] == "t3_1vthze0"

        assert by_id["deleted1"]["author"] == "[deleted]"
        assert by_id["deleted1"]["body"] == "[deleted]"

    def test_maps_to_normalized_records_and_strips_usernames(self) -> None:
        html = FIXTURE.read_text(encoding="utf-8")
        posts, comments = parse_shreddit_html(html, fallback_subreddit="Philippines")

        sub = map_submission_dict(posts[0], collection_run_id="run-shreddit")
        assert sub.reddit_fullname == "t3_1vthze0"
        assert sub.author_status == "pseudonymized"
        assert sub.author_hash == pseudonymize_author("nayryanaryn")[0]
        assert sub.author == "nayryanaryn"
        assert sub.id == "1vthze0"
        assert sub.link_flair_text == "ViralPH"

        reply = next(c for c in comments if c["id"] == "p4twfc6")
        rec = map_comment_dict(reply, collection_run_id="run-shreddit")
        assert rec.parent_record_type == "comment"
        assert rec.is_submitter is True
        assert rec.submission_id == "1vthze0"
        assert rec.author == "nayryanaryn"
        assert rec.id == "p4twfc6"

        deleted = next(c for c in comments if c["id"] == "deleted1")
        drec = map_comment_dict(deleted, collection_run_id="run-shreddit")
        assert drec.author_status == "deleted"
        assert drec.author_hash is None


class TestRawHelpers:
    def test_aria_label_status_icons_not_hidden(self) -> None:
        html = """
        <shreddit-post id="t3_abc" post-title="x" subreddit-name="Philippines"
            author="u1" score="10" upvote-ratio="0.9" comment-count="2"
            permalink="/r/Philippines/comments/abc/x/"
            created-timestamp="2026-08-20T12:22:10.758000+0000" post-type="text">
          <svg aria-label="Stickied post" class="stickied-status"></svg>
          <svg aria-label="Locked post" class="lock-status"></svg>
          <div id="t3_abc-post-rtjson-content">hello</div>
        </shreddit-post>
        """
        posts, _ = parse_shreddit_html(html, fallback_subreddit="Philippines")
        assert posts[0]["stickied"] is True
        assert posts[0]["locked"] is True

    def test_hidden_status_icons_are_false(self) -> None:
        html = """
        <shreddit-post id="t3_abc" post-title="x" subreddit-name="Philippines"
            author="u1" score="10" upvote-ratio="0.9" comment-count="2"
            permalink="/r/Philippines/comments/abc/x/"
            created-timestamp="2026-08-20T12:22:10.758000+0000" post-type="text">
          <svg aria-label="Stickied post" class="hidden stickied-status"></svg>
          <svg aria-label="Locked post" class="hidden lock-status"></svg>
          <div id="t3_abc-post-rtjson-content">hello</div>
        </shreddit-post>
        """
        posts, _ = parse_shreddit_html(html, fallback_subreddit="Philippines")
        assert posts[0]["stickied"] is False
        assert posts[0]["locked"] is False

    def test_boolean_flags_nsfw_locked(self) -> None:
        raw = submission_raw_from_shreddit(
            attrs={
                "id": "t3_abc",
                "post-title": "x",
                "subreddit-name": "Philippines",
                "author": "u1",
                "score": "10",
                "upvote-ratio": "0.9",
                "comment-count": "2",
                "permalink": "/r/Philippines/comments/abc/x/",
                "created-timestamp": "2026-08-20T12:22:10.758000+0000",
                "post-type": "text",
            },
            selftext="hello",
            flags=["nsfw", "locked", "stickied"],
        )
        assert raw["over_18"] is True
        assert raw["locked"] is True
        assert raw["stickied"] is True
        assert raw["is_self"] is True

    def test_comment_parent_defaults_to_post(self) -> None:
        raw = comment_raw_from_shreddit(
            attrs={
                "thingId": "t1_zzz",
                "postId": "t3_abc",
                "author": "bob",
                "score": "3",
                "depth": "0",
                "created": "2026-08-20T12:22:10.758000+0000",
                "permalink": "/r/Philippines/comments/abc/comment/zzz/",
            },
            body="hi",
            fallback_subreddit="Philippines",
        )
        assert raw["parent_id"] == "t3_abc"
        assert raw["name"] == "t1_zzz"
        assert raw["subreddit"] == "Philippines"


class _HtmlRedditSource:
    """Minimal RedditSource over parsed Shreddit HTML (no browser)."""

    def __init__(self, html: str, subreddit: str) -> None:
        self._posts, self._comments = parse_shreddit_html(
            html, fallback_subreddit=subreddit
        )

    def iter_submissions(self, request: CollectionRequest):
        for raw in self._posts:
            yield map_submission_dict(
                raw,
                collection_run_id=request.collection_run_id,
                sampling_strategy=request.sampling_strategy,
                matched_query_or_keyword=request.matched_query_or_keyword,
            )

    def iter_comments(self, submission, request: CollectionRequest):
        for raw in self._comments:
            if raw.get("link_id") != submission.reddit_fullname:
                continue
            body = raw.get("body") or ""
            if not request.include_deleted and body in ("[deleted]", "[removed]"):
                continue
            yield map_comment_dict(raw, collection_run_id=request.collection_run_id)


class TestPipelineFromHtml:
    def test_html_fixture_flows_through_pipeline(self, tmp_path: Path) -> None:
        html = FIXTURE.read_text(encoding="utf-8")
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        db = DedupDatabase(tmp_path / "db" / "collection.sqlite3")
        source = _HtmlRedditSource(html, "Philippines")
        ctx = run_collection(
            source,
            CollectionRequest(
                subreddit="Philippines",
                listing_type="new",
                collection_run_id="run-html",
                include_deleted=False,
            ),
            data_dir=data_dir,
            db=db,
            source_type="shreddit",
        )
        assert ctx.actual_submissions_stored == 1
        assert ctx.comments_stored == 3  # deleted body excluded
        db.close()
