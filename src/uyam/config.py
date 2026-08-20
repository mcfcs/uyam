"""Configuration loading for Uyám.

Loads collection.yaml for runtime configuration and .env for secrets.
Config values are validated at load time so CLI commands fail fast.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

_DEFAULT_CONFIG_PATH = Path(__file__).parent.parent.parent / "config" / "collection.yaml"


def load_env(env_path: Path | None = None) -> None:
    """Load .env into os.environ. Safe to call multiple times."""
    if env_path is None:
        env_path = Path(__file__).parent.parent.parent / ".env"
    load_dotenv(env_path, override=False)


@dataclass
class CommentsConfig:
    enabled: bool = True
    max_comments_per_submission: int | None = 100
    max_depth: int | None = None
    include_deleted: bool = False
    sort: str = "confidence"
    replace_more_limit: int = 32


@dataclass
class SearchConfig:
    enabled: bool = False
    query: str | None = None
    sort: str = "relevance"
    time_filter: str = "all"


@dataclass
class OversamplingConfig:
    enabled: bool = False
    keywords: list[str] = field(default_factory=list)


@dataclass
class RetryConfig:
    max_attempts: int = 5
    max_delay_seconds: float = 60.0


@dataclass
class PublicConfig:
    """Settings for the credential-free public-JSON source."""

    min_interval_seconds: float = 6.0
    timeout_seconds: float = 30.0


@dataclass
class ShredditConfig:
    """Settings for the headful Shreddit (www.reddit.com) browser source."""

    headless: bool = False
    timeout_seconds: float = 45.0
    min_interval_seconds: float = 1.5
    max_scrolls: int = 40
    more_comments_clicks: int = 40
    scroll_wait_ms: int = 1500
    expand_wait_ms: int = 1200
    use_system_chrome: bool = True
    captcha_wait_seconds: float = 300.0


@dataclass
class CollectionConfig:
    listing: str = "new"
    limit_per_subreddit: int | None = 100
    sort: str | None = None
    time_filter: str = "all"


@dataclass
class AppConfig:
    data_dir: Path
    subreddits: list[str]
    collection: CollectionConfig
    comments: CommentsConfig
    search: SearchConfig
    oversampling: OversamplingConfig
    retry: RetryConfig
    public: PublicConfig
    shreddit: ShredditConfig


def _get(d: dict[str, Any], key: str, default: Any = None) -> Any:
    return d.get(key, default) or default


def load_config(config_path: Path | None = None) -> AppConfig:
    """Load and parse collection.yaml."""
    path = config_path or _DEFAULT_CONFIG_PATH
    raw: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    data_dir = Path(raw.get("data_dir", "data"))

    subreddits: list[str] = raw.get("subreddits", [])

    col_raw: dict[str, Any] = raw.get("collection", {})
    collection = CollectionConfig(
        listing=col_raw.get("listing", "new"),
        limit_per_subreddit=col_raw.get("limit_per_subreddit", 100),
        sort=col_raw.get("sort"),
        time_filter=col_raw.get("time_filter", "all"),
    )

    com_raw: dict[str, Any] = raw.get("comments", {})
    comments = CommentsConfig(
        enabled=com_raw.get("enabled", True),
        max_comments_per_submission=com_raw.get("max_comments_per_submission", 100),
        max_depth=com_raw.get("max_depth"),
        include_deleted=com_raw.get("include_deleted", False),
        sort=com_raw.get("sort", "confidence"),
        replace_more_limit=com_raw.get("replace_more_limit", 32),
    )

    srch_raw: dict[str, Any] = raw.get("search", {})
    search = SearchConfig(
        enabled=srch_raw.get("enabled", False),
        query=srch_raw.get("query"),
        sort=srch_raw.get("sort", "relevance"),
        time_filter=srch_raw.get("time_filter", "all"),
    )

    over_raw: dict[str, Any] = raw.get("oversampling", {})
    oversampling = OversamplingConfig(
        enabled=over_raw.get("enabled", False),
        keywords=over_raw.get("keywords", []) or [],
    )

    retry_raw: dict[str, Any] = raw.get("retry", {})
    retry = RetryConfig(
        max_attempts=retry_raw.get("max_attempts", 5),
        max_delay_seconds=float(retry_raw.get("max_delay_seconds", 60.0)),
    )

    pub_raw: dict[str, Any] = raw.get("public", {})
    public = PublicConfig(
        min_interval_seconds=float(pub_raw.get("min_interval_seconds", 6.0)),
        timeout_seconds=float(pub_raw.get("timeout_seconds", 30.0)),
    )

    sh_raw: dict[str, Any] = raw.get("shreddit", {})
    shreddit = ShredditConfig(
        headless=bool(sh_raw.get("headless", False)),
        timeout_seconds=float(sh_raw.get("timeout_seconds", 45.0)),
        min_interval_seconds=float(sh_raw.get("min_interval_seconds", 1.5)),
        max_scrolls=int(sh_raw.get("max_scrolls", 40)),
        more_comments_clicks=int(sh_raw.get("more_comments_clicks", 40)),
        scroll_wait_ms=int(sh_raw.get("scroll_wait_ms", 1500)),
        expand_wait_ms=int(sh_raw.get("expand_wait_ms", 1200)),
        use_system_chrome=bool(sh_raw.get("use_system_chrome", True)),
        captcha_wait_seconds=float(sh_raw.get("captcha_wait_seconds", 300.0)),
    )

    return AppConfig(
        data_dir=data_dir,
        subreddits=subreddits,
        collection=collection,
        comments=comments,
        search=search,
        oversampling=oversampling,
        retry=retry,
        public=public,
        shreddit=shreddit,
    )
