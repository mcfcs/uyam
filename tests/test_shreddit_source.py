"""Unit tests for ShredditBrowserSource construction (no live browser)."""

from __future__ import annotations

import pytest

from uyam.sources.proxy_pool import ProxyPool
from uyam.sources.shreddit import (
    ShredditBrowserSource,
    _merge_comment_dicts,
    _reddit_id_from_permalink,
)


def test_requires_non_empty_proxy_pool() -> None:
    with pytest.raises(RuntimeError, match="proxies.txt"):
        ShredditBrowserSource(proxy_pool=ProxyPool([]))


def test_permalink_id() -> None:
    assert (
        _reddit_id_from_permalink(
            "/r/Philippines/comments/1vthze0/group_of_pickpockets/"
        )
        == "1vthze0"
    )


def test_merge_prefers_primary() -> None:
    primary = [{"id": "a", "body": "json"}]
    extra = [{"id": "a", "body": "dom"}, {"id": "b", "body": "only-dom"}]
    merged = _merge_comment_dicts(primary, extra)
    assert [c["id"] for c in merged] == ["a", "b"]
    assert merged[0]["body"] == "json"
