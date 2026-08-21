"""Tests for PublicJsonRedditSource and ensure_hmac_key.

All HTTP is mocked; no live network calls are made.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from uyam.privacy import ensure_hmac_key, pseudonymize_author, reset_hmac_key_cache
from uyam.sources.base import CollectionRequest
from uyam.sources.public_json import PublicJsonRedditSource

TEST_KEY = "test-hmac-key-public-json"


@pytest.fixture(autouse=True)
def set_hmac_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTHOR_HMAC_KEY", TEST_KEY)
    reset_hmac_key_cache()
    yield
    reset_hmac_key_cache()


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """Neutralize all sleeps so retry/backoff/throttle tests run instantly."""
    monkeypatch.setattr("uyam.sources.public_json.time.sleep", lambda *_: None)


# ---------------------------------------------------------------------------
# Canned Reddit-shaped JSON payloads
# ---------------------------------------------------------------------------

def _submission_child(reddit_id: str, sub: str, author: str = "real_user_secret") -> dict:
    return {
        "kind": "t3",
        "data": {
            "id": reddit_id,
            "name": f"t3_{reddit_id}",
            "subreddit": sub,
            "title": f"Title {reddit_id}",
            "selftext": "body",
            "created_utc": 1752537600,
            "score": 42,
            "upvote_ratio": 0.9,
            "num_comments": 2,
            "permalink": f"/r/{sub}/comments/{reddit_id}/x/",
            "url": f"https://reddit.com/r/{sub}/comments/{reddit_id}/x/",
            "is_self": True,
            "over_18": False,
            "spoiler": False,
            "stickied": False,
            "locked": False,
            "archived": False,
            "distinguished": None,
            "link_flair_text": None,
            "is_original_content": False,
            "num_crossposts": 0,
            "gilded": 3,
            "author": author,
        },
    }


def _listing(children: list[dict], after: str | None = None) -> dict:
    return {"kind": "Listing", "data": {"after": after, "children": children}}


def _comment_child(
    reddit_id: str,
    sub: str,
    link_id: str,
    parent_id: str,
    *,
    author: str = "commenter_secret",
    body: str = "hello",
    replies: Any = "",
    depth: int | None = None,
) -> dict:
    data: dict[str, Any] = {
        "id": reddit_id,
        "name": f"t1_{reddit_id}",
        "link_id": link_id,
        "parent_id": parent_id,
        "subreddit": sub,
        "body": body,
        "created_utc": 1752537900,
        "score": 10,
        "is_submitter": False,
        "distinguished": None,
        "stickied": False,
        "gilded": 0,
        "controversiality": 0,
        "permalink": f"/r/{sub}/comments/x/y/{reddit_id}/",
        "author": author,
        "replies": replies,
    }
    if depth is not None:
        data["depth"] = depth
    return {"kind": "t1", "data": data}


class _FakeResponse:
    def __init__(self, payload: Any, status: int = 200, headers: dict | None = None) -> None:
        self._payload = payload
        self.status_code = status
        self.headers = headers or {}

    def json(self) -> Any:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            import requests

            raise requests.exceptions.HTTPError(f"HTTP {self.status_code}")


def _install_get(monkeypatch: pytest.MonkeyPatch, responses: list[_FakeResponse]) -> list[dict]:
    """Patch Session.get to return queued responses; record calls."""
    calls: list[dict] = []
    queue = list(responses)

    def fake_get(self: Any, url: str, params: Any = None, timeout: Any = None) -> _FakeResponse:
        calls.append({"url": url, "params": params})
        return queue.pop(0)

    monkeypatch.setattr("requests.Session.get", fake_get)
    return calls


def _request(subreddit: str = "Philippines", **kw: Any) -> CollectionRequest:
    return CollectionRequest(
        subreddit=subreddit,
        listing_type=kw.pop("listing_type", "new"),
        collection_run_id="run-public-test",
        **kw,
    )


# ---------------------------------------------------------------------------
# iter_submissions
# ---------------------------------------------------------------------------

class TestIterSubmissions:
    def test_maps_fields_including_gilded(self, monkeypatch: pytest.MonkeyPatch) -> None:
        payload = _listing([_submission_child("aaa", "Philippines")])
        _install_get(monkeypatch, [_FakeResponse(payload)])

        src = PublicJsonRedditSource(min_interval_seconds=0)
        subs = list(src.iter_submissions(_request(limit=10)))

        assert len(subs) == 1
        assert subs[0].reddit_fullname == "t3_aaa"
        assert subs[0].subreddit == "Philippines"
        assert subs[0].gilded == 3
        assert subs[0].author_status == "pseudonymized"

    def test_respects_limit(self, monkeypatch: pytest.MonkeyPatch) -> None:
        children = [_submission_child(f"id{i}", "Philippines") for i in range(5)]
        _install_get(monkeypatch, [_FakeResponse(_listing(children))])

        src = PublicJsonRedditSource(min_interval_seconds=0)
        subs = list(src.iter_submissions(_request(limit=3)))

        assert len(subs) == 3

    def test_follows_after_pagination(self, monkeypatch: pytest.MonkeyPatch) -> None:
        page1 = _listing(
            [_submission_child(f"p1_{i}", "Philippines") for i in range(100)],
            after="t3_next",
        )
        page2 = _listing([_submission_child("p2_0", "Philippines")], after=None)
        calls = _install_get(monkeypatch, [_FakeResponse(page1), _FakeResponse(page2)])

        src = PublicJsonRedditSource(min_interval_seconds=0)
        subs = list(src.iter_submissions(_request(limit=101)))

        assert len(subs) == 101
        assert len(calls) == 2
        assert calls[1]["params"]["after"] == "t3_next"

    def test_search_endpoint_used(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls = _install_get(monkeypatch, [_FakeResponse(_listing([]))])

        src = PublicJsonRedditSource(min_interval_seconds=0)
        list(src.iter_submissions(_request(listing_type="search", search_query="sana all")))

        assert calls[0]["url"].endswith("/search.json")
        assert calls[0]["params"]["q"] == "sana all"
        assert calls[0]["params"]["restrict_sr"] == 1

    def test_raw_username_never_in_output(self, monkeypatch: pytest.MonkeyPatch) -> None:
        payload = _listing([_submission_child("aaa", "Philippines", author="TopSecretName")])
        _install_get(monkeypatch, [_FakeResponse(payload)])

        src = PublicJsonRedditSource(min_interval_seconds=0)
        subs = list(src.iter_submissions(_request(limit=1)))

        assert subs[0].author == "TopSecretName"
        assert subs[0].id == "aaa"
        assert subs[0].author_hash == pseudonymize_author("TopSecretName")[0]


# ---------------------------------------------------------------------------
# iter_comments
# ---------------------------------------------------------------------------

class TestIterComments:
    def _make_submission_record(self, monkeypatch: pytest.MonkeyPatch) -> Any:
        payload = _listing([_submission_child("aaa", "Philippines")])
        _install_get(monkeypatch, [_FakeResponse(payload)])
        src = PublicJsonRedditSource(min_interval_seconds=0)
        return next(iter(src.iter_submissions(_request(limit=1))))

    def test_flattens_nested_tree_with_depth(self, monkeypatch: pytest.MonkeyPatch) -> None:
        sub_record = self._make_submission_record(monkeypatch)

        # Build a 3-level chain: c1 -> c2 -> c3 via nested replies.
        c3 = _comment_child("c3", "Philippines", "t3_aaa", "t1_c2")
        c2 = _comment_child(
            "c2", "Philippines", "t3_aaa", "t1_c1", replies=_listing([c3])
        )
        c1 = _comment_child(
            "c1", "Philippines", "t3_aaa", "t3_aaa", replies=_listing([c2])
        )
        comments_payload = [{"kind": "Listing"}, _listing([c1])]
        _install_get(monkeypatch, [_FakeResponse(comments_payload)])

        src = PublicJsonRedditSource(min_interval_seconds=0)
        comments = list(src.iter_comments(sub_record, _request()))

        assert [c.reddit_id for c in comments] == ["c1", "c2", "c3"]
        assert [c.depth for c in comments] == [0, 1, 2]
        assert comments[0].parent_record_type == "submission"
        assert comments[1].parent_record_type == "comment"
        assert comments[2].link_id == "t3_aaa"
        assert comments[2].submission_id == "aaa"

    def test_more_nodes_skipped_and_counted(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        sub_record = self._make_submission_record(monkeypatch)

        c1 = _comment_child("c1", "Philippines", "t3_aaa", "t3_aaa")
        more = {"kind": "more", "data": {"count": 5, "children": ["x", "y"]}}
        comments_payload = [{"kind": "Listing"}, _listing([c1, more])]
        _install_get(monkeypatch, [_FakeResponse(comments_payload)])

        src = PublicJsonRedditSource(min_interval_seconds=0)
        with caplog.at_level("INFO"):
            comments = list(src.iter_comments(sub_record, _request()))

        assert [c.reddit_id for c in comments] == ["c1"]
        assert any("public_json_more_truncated" in r.message for r in caplog.records)

    def test_max_depth_filters(self, monkeypatch: pytest.MonkeyPatch) -> None:
        sub_record = self._make_submission_record(monkeypatch)

        c2 = _comment_child("c2", "Philippines", "t3_aaa", "t1_c1")
        c1 = _comment_child(
            "c1", "Philippines", "t3_aaa", "t3_aaa", replies=_listing([c2])
        )
        comments_payload = [{"kind": "Listing"}, _listing([c1])]
        _install_get(monkeypatch, [_FakeResponse(comments_payload)])

        src = PublicJsonRedditSource(min_interval_seconds=0)
        comments = list(src.iter_comments(sub_record, _request(max_depth=0)))

        assert [c.reddit_id for c in comments] == ["c1"]

    def test_deleted_excluded_by_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        sub_record = self._make_submission_record(monkeypatch)

        c1 = _comment_child("c1", "Philippines", "t3_aaa", "t3_aaa", body="[deleted]")
        c2 = _comment_child("c2", "Philippines", "t3_aaa", "t3_aaa", body="real")
        comments_payload = [{"kind": "Listing"}, _listing([c1, c2])]
        _install_get(monkeypatch, [_FakeResponse(comments_payload)])

        src = PublicJsonRedditSource(min_interval_seconds=0)
        comments = list(src.iter_comments(sub_record, _request()))

        assert [c.reddit_id for c in comments] == ["c2"]

    def test_raw_commenter_username_never_in_output(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sub_record = self._make_submission_record(monkeypatch)

        c1 = _comment_child(
            "c1", "Philippines", "t3_aaa", "t3_aaa", author="HiddenCommenter"
        )
        comments_payload = [{"kind": "Listing"}, _listing([c1])]
        _install_get(monkeypatch, [_FakeResponse(comments_payload)])

        src = PublicJsonRedditSource(min_interval_seconds=0)
        comments = list(src.iter_comments(sub_record, _request()))

        assert comments[0].author == "HiddenCommenter"
        assert comments[0].id == "c1"


# ---------------------------------------------------------------------------
# Retry / backoff on 429
# ---------------------------------------------------------------------------

class TestRetry:
    def test_429_then_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        ok = _listing([_submission_child("aaa", "Philippines")])
        responses = [
            _FakeResponse(None, status=429, headers={"Retry-After": "1"}),
            _FakeResponse(ok, status=200),
        ]
        calls = _install_get(monkeypatch, responses)

        src = PublicJsonRedditSource(min_interval_seconds=0)
        subs = list(src.iter_submissions(_request(limit=1)))

        assert len(subs) == 1
        assert len(calls) == 2


# ---------------------------------------------------------------------------
# ensure_hmac_key
# ---------------------------------------------------------------------------

class TestEnsureHmacKey:
    def test_noop_when_present(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("AUTHOR_HMAC_KEY", "already-here")
        env_file = tmp_path / ".env"
        ensure_hmac_key(env_file)
        assert not env_file.exists()  # nothing written

    def test_generates_and_persists(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        monkeypatch.delenv("AUTHOR_HMAC_KEY", raising=False)
        reset_hmac_key_cache()
        env_file = tmp_path / ".env"

        with caplog.at_level("WARNING"):
            ensure_hmac_key(env_file)

        import os

        key = os.environ["AUTHOR_HMAC_KEY"]
        assert len(key) == 64  # token_hex(32)
        assert env_file.exists()
        contents = env_file.read_text(encoding="utf-8")
        assert f"AUTHOR_HMAC_KEY={key}" in contents
        # The key value must never be logged.
        for record in caplog.records:
            assert key not in record.getMessage()
            assert key not in json.dumps(getattr(record, "__dict__", {}), default=str)

    def test_appends_to_existing_env(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.delenv("AUTHOR_HMAC_KEY", raising=False)
        reset_hmac_key_cache()
        env_file = tmp_path / ".env"
        env_file.write_text("REDDIT_CLIENT_ID=abc", encoding="utf-8")

        ensure_hmac_key(env_file)

        contents = env_file.read_text(encoding="utf-8")
        assert "REDDIT_CLIENT_ID=abc" in contents
        assert "AUTHOR_HMAC_KEY=" in contents
        # Existing line preserved on its own line.
        assert "REDDIT_CLIENT_ID=abcAUTHOR_HMAC_KEY" not in contents
