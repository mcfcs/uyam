"""Headful Shreddit (www.reddit.com) source using Patchright / Playwright.

Reddit's unauthenticated `.json` endpoints are blocked or CAPTCHA-gated.
The current web UI (Shreddit) SSR-renders the fields we need as attributes
on `<shreddit-post>` and `<shreddit-comment>` custom elements. This source
drives a real Chrome window through a proxy from proxies.txt, walks listing
pages, then harvests each comments page.

Bypass strategy (in order of importance):
  1. Headful Chrome (`channel="chrome"`) via Patchright, which patches
     Playwright's automation fingerprints at the protocol level.
  2. When Reddit shows a captcha, pause so a human can solve it in Chrome
     (mirrored as a screenshot on the Streamlit site). Captchas are not
     auto-solved.
  3. Residential / rotating proxies from proxies.txt — required, no direct
     fallback. A blocked or dead proxy is marked unhealthy and the browser
     is relaunched on the next one.
  4. No custom User-Agent / extra headers (those are fingerprint tells).

The Reddit API / PRAW path is intentionally unused.
"""

from __future__ import annotations

import contextlib
import importlib
import logging
import random
import shutil
import time
from collections.abc import Iterable
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlparse

from uyam.models import CommentRecord, SubmissionRecord
from uyam.scrape_status import (
    SCREENSHOT_FILE,
    ScrapeStopRequested,
    check_control,
    clear_status,
    write_status,
)
from uyam.sources.base import CollectionRequest
from uyam.sources.mapping import map_comment_dict, map_submission_dict
from uyam.sources.proxy_pool import ProxyPool, to_playwright_proxy
from uyam.sources.public_json import (
    _MORE_SENTINEL,
    _flatten_comment_tree,
    _listing_endpoint,
)
from uyam.sources.shreddit_parse import (
    comment_raw_from_shreddit,
    comments_url,
    listing_url,
    parse_shreddit_html,
    submission_raw_from_shreddit,
)

logger = logging.getLogger(__name__)

_BLOCK_SNIPPETS = (
    "whoa there, pardner",
    "whoa there",
    "request blocked",
    "unusual traffic",
    "verify you are human",
    "prove your humanity",
    "access denied",
    "checking your browser",
    "just a moment",
)
_MAX_PROXY_TRIES = 8
_HYDRATE_WAIT_S = 20.0
_REPO_ROOT = Path(__file__).resolve().parents[3]
_PROFILE_DIR = _REPO_ROOT / "data" / ".browser-profile"

_POST_SELECTOR = "shreddit-post"
_COMMENT_SELECTOR = "shreddit-comment"
_MORE_COMMENTS_SELECTOR = "faceplate-partial.more-comments-partial"

_EXTRACT_POST_JS = """
(el) => {
  const isShown = (root, sel) => {
    const n = root.querySelector(sel);
    if (!n) return false;
    if (n.classList.contains("hidden")) return false;
    return true;
  };
  if (!el) return null;
  const attrs = {};
  const flags = [];
  for (const a of el.attributes) {
    attrs[a.name] = a.value;
    if (a.value === "") flags.push(a.name);
  }
  if (isShown(el, ".stickied-status, [aria-label='Stickied post']"))
    flags.push("stickied");
  if (isShown(el, ".lock-status, [aria-label='Locked post']"))
    flags.push("locked");
  if (isShown(el, ".archived-status, [aria-label='Archived post']"))
    flags.push("archived");
  if (isShown(el, ".spoiler-status, [aria-label='Spoiler post']"))
    flags.push("spoiler");
  if (isShown(el, ".nsfw-status, [aria-label='NSFW post']"))
    flags.push("nsfw");
  if (isShown(el, "[aria-label='Original Content']"))
    flags.push("is-original-content");
  const distEl = el.querySelector("shreddit-distinguished-post-tags");
  const distText = distEl ? (distEl.innerText || "").trim().toLowerCase() : "";
  if (distText.includes("admin")) attrs.distinguished = "admin";
  else if (distText.includes("mod")) attrs.distinguished = "moderator";
  const id = el.getAttribute("id") || "";
  const rt = id
    ? el.querySelector("#" + CSS.escape(id) + "-post-rtjson-content")
    : el.querySelector('[id$="-post-rtjson-content"]');
  const flair = el.querySelector("shreddit-post-flair .flair-content")
    || el.querySelector("shreddit-post-flair");
  let nview = null;
  const sv = document.querySelector("shreddit-screenview-data");
  if (sv) {
    try { nview = JSON.parse(sv.getAttribute("data") || "null"); }
    catch (e) { nview = null; }
  }
  return {
    attrs,
    flags,
    selftext: rt ? (rt.innerText || "").trim() : "",
    flair_text: flair ? (flair.innerText || "").trim() : "",
    nview,
  };
}
"""

_EXTRACT_COMMENTS_JS = """
(els) => {
  return els.map((el) => {
    const attrs = {};
    const flags = [];
    for (const a of el.attributes) {
      attrs[a.name] = a.value;
      if (a.value === "") flags.push(a.name);
    }
    if (el.hasAttribute("stickied") || el.hasAttribute("pinned")) {
      flags.push("stickied");
    }
    const mod = el.querySelector(
      "shreddit-comment-author-modifier-icon, shreddit-comment-author-modifier"
    );
    if (mod) {
      const t = ((mod.getAttribute("type") || "") + " " + (mod.innerText || "")).toLowerCase();
      if (t.includes("admin")) attrs.distinguished = "admin";
      else if (t.includes("mod")) attrs.distinguished = "moderator";
    }
    const id = el.getAttribute("thingId") || el.getAttribute("thingid") || "";
    let rt = null;
    if (id) rt = el.querySelector("#" + CSS.escape(id) + "-post-rtjson-content");
    if (!rt) {
      rt = Array.from(el.querySelectorAll('[id$="-post-rtjson-content"]')).find((node) => {
        return node.closest("shreddit-comment") === el;
      }) || null;
    }
    return {
      attrs,
      flags,
      body: rt ? (rt.innerText || "").trim() : "",
    };
  });
}
"""

_LISTING_CARDS_JS = """
() => {
  return Array.from(document.querySelectorAll("shreddit-post")).map((el) => {
    const cls = (el.getAttribute("class") || "").toLowerCase();
    const promoted = el.hasAttribute("promoted") || cls.includes("promoted")
      || !!el.getAttribute("ad-type");
    return {
      id: el.getAttribute("id") || "",
      permalink: el.getAttribute("permalink") || "",
      promoted,
    };
  });
}
"""


def _proxy_label(proxy_url: str) -> str:
    parsed = urlparse(proxy_url)
    return f"{parsed.hostname}:{parsed.port}"


def _reddit_id_from_permalink(permalink: str) -> str:
    parts = [p for p in permalink.split("/") if p]
    try:
        idx = parts.index("comments")
        return parts[idx + 1]
    except (ValueError, IndexError):
        return ""


def _merge_comment_dicts(
    primary: list[dict[str, Any]], extra: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Keep JSON rows (complete fields) and append DOM-only replies."""
    by_id: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for row in primary + extra:
        cid = str(row.get("id") or "")
        if not cid:
            continue
        if cid not in by_id:
            order.append(cid)
            by_id[cid] = row
    return [by_id[cid] for cid in order]


def _import_sync_playwright() -> tuple[Any, str]:
    """Prefer Patchright (stealth fork); fall back to stock Playwright."""
    last_exc: ImportError | None = None
    for module_name, label in (
        ("patchright.sync_api", "patchright"),
        ("playwright.sync_api", "playwright"),
    ):
        try:
            mod = importlib.import_module(module_name)
            return mod.sync_playwright, label
        except ImportError as exc:
            last_exc = exc
    raise RuntimeError(
        "The shreddit source needs Patchright (preferred) or Playwright. "
        "Install with: pip install patchright   "
        "then either install Chrome, or run: python -m patchright install chromium"
    ) from last_exc


class ShredditBrowserSource:
    """Live Reddit source that scrapes Shreddit HTML through a headful browser.

    Implements the RedditSource protocol. A proxy pool is required — there is
    no direct-connection fallback.
    """

    def __init__(
        self,
        *,
        proxy_pool: ProxyPool,
        headless: bool = False,
        timeout_seconds: float = 45.0,
        min_interval_seconds: float = 1.5,
        max_scrolls: int = 40,
        more_comments_clicks: int = 40,
        scroll_wait_ms: int = 1500,
        expand_wait_ms: int = 1200,
        use_system_chrome: bool = True,
        captcha_wait_seconds: float = 300.0,
    ) -> None:
        if proxy_pool.is_empty():
            raise RuntimeError(
                "The shreddit source requires at least one proxy in proxies.txt. "
                "Copy proxies.example.txt to proxies.txt and add proxies."
            )

        self._pool = proxy_pool
        self._headless = headless
        self._timeout_s = timeout_seconds
        self._nav_timeout_ms = int(timeout_seconds * 1000)
        self._min_interval = max(0.0, min_interval_seconds)
        self._max_scrolls = max(1, max_scrolls)
        self._more_comments_clicks = max(0, more_comments_clicks)
        self._scroll_wait_ms = scroll_wait_ms
        self._expand_wait_ms = expand_wait_ms
        self._use_system_chrome = use_system_chrome
        self._captcha_wait_s = max(0.0, captcha_wait_seconds)

        self._last_request_at: float | None = None
        self._playwright: Any = None
        self._driver_name = "patchright"
        self._context: Any = None
        self._page: Any = None
        self._profile_dir: Path | None = None
        self._active_proxy_label: str | None = None

        # reddit_id -> parsed comment raw dicts harvested with the submission
        self._comment_cache: dict[str, list[dict[str, Any]]] = {}
        self._json_post_cache: dict[str, dict[str, Any]] = {}
        self._json_more: set[str] = set()

        logger.info(
            "shreddit_source_initialized",
            extra={
                "headless": headless,
                "proxies": proxy_pool.healthy_count,
                "captcha_wait_seconds": self._captcha_wait_s,
            },
        )

    # ------------------------------------------------------------------
    # Browser lifecycle
    # ------------------------------------------------------------------

    def close(self) -> None:
        """Shut down the browser. Persistent profile is kept."""
        clear_status()
        self._drop_browser()
        if self._playwright is not None:
            with contextlib.suppress(Exception):
                self._playwright.stop()
            self._playwright = None

    def _ensure_playwright(self) -> None:
        if self._playwright is not None:
            return
        ctor, name = _import_sync_playwright()
        self._driver_name = name
        self._playwright = ctor().start()
        logger.info("shreddit_browser_backend", extra={"backend": name})

    def _ensure_browser(self) -> None:
        if self._page is not None:
            return
        self._ensure_playwright()
        proxy_url = self._pool.current()
        if not proxy_url:
            raise RuntimeError("Proxy pool exhausted; no healthy proxy remaining.")
        pw_proxy = to_playwright_proxy(proxy_url)
        self._active_proxy_label = _proxy_label(proxy_url)
        self._profile_dir = _PROFILE_DIR
        self._profile_dir.mkdir(parents=True, exist_ok=True)
        probe = _REPO_ROOT / ".probe-profile"
        if probe.exists() and not (self._profile_dir / "Default").exists():
            shutil.copytree(probe, self._profile_dir, dirs_exist_ok=True)

        launch_kwargs: dict[str, Any] = {
            "user_data_dir": str(self._profile_dir),
            "headless": self._headless,
            "proxy": pw_proxy,
            "no_viewport": True,
            "ignore_https_errors": True,
        }
        if self._use_system_chrome:
            launch_kwargs["channel"] = "chrome"

        try:
            self._context = self._playwright.chromium.launch_persistent_context(
                **launch_kwargs
            )
        except Exception as exc:
            if self._use_system_chrome:
                logger.warning(
                    "shreddit_chrome_channel_unavailable",
                    extra={"error_type": type(exc).__name__},
                )
                launch_kwargs.pop("channel", None)
                self._context = self._playwright.chromium.launch_persistent_context(
                    **launch_kwargs
                )
            else:
                raise

        self._page = (
            self._context.pages[0] if self._context.pages else self._context.new_page()
        )
        self._page.set_default_timeout(self._nav_timeout_ms)
        logger.info(
            "shreddit_browser_launched",
            extra={
                "proxy": self._active_proxy_label,
                "headless": self._headless,
                "backend": self._driver_name,
            },
        )

    def _drop_browser(self) -> None:
        page, context = self._page, self._context
        self._page = None
        self._context = None
        self._profile_dir = None
        if page is not None:
            with contextlib.suppress(Exception):
                page.close()
        if context is not None:
            with contextlib.suppress(Exception):
                context.close()
        # Persistent profiles are kept so the humanity challenge stays solved.

    def _rotate_proxy(self, *, reason: str) -> None:
        logger.warning(
            "shreddit_proxy_rotate",
            extra={"proxy": self._active_proxy_label, "reason": reason},
        )
        self._pool.mark_current_unhealthy()
        self._drop_browser()

    def _throttle(self) -> None:
        if self._last_request_at is None or self._min_interval <= 0:
            return
        elapsed = time.monotonic() - self._last_request_at
        wait = self._min_interval - elapsed
        if wait > 0:
            time.sleep(wait + random.uniform(0, 0.4))

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------

    def _page_text(self) -> str:
        if self._page is None:
            return ""
        title = (self._page.title() or "").lower()
        body = ""
        with contextlib.suppress(Exception):
            body = (self._page.inner_text("body", timeout=2000) or "")[:1500].lower()
        return f"{title}\n{body}"

    def _is_challenge(self) -> bool:
        hay = self._page_text()
        return any(s in hay for s in _BLOCK_SNIPPETS)

    def _has_feed(self) -> bool:
        if self._page is None:
            return False
        return (
            self._page.locator(_POST_SELECTOR).count() > 0
            or self._page.locator(_COMMENT_SELECTOR).count() > 0
        )

    def _focus_page(self) -> None:
        if self._page is None:
            return
        with contextlib.suppress(Exception):
            self._page.bring_to_front()

    def _snapshot_challenge(self, message: str) -> None:
        assert self._page is not None
        with contextlib.suppress(Exception):
            SCREENSHOT_FILE.parent.mkdir(parents=True, exist_ok=True)
            self._page.screenshot(path=str(SCREENSHOT_FILE), full_page=False)
        title = ""
        url = ""
        with contextlib.suppress(Exception):
            title = self._page.title() or ""
            url = self._page.url or ""
        write_status("captcha", message, url=url, title=title)

    def _wait_for_feed(self) -> bool:
        """Wait for posts, or pause so a human can solve the Chrome captcha."""
        assert self._page is not None
        hydrate_until = time.monotonic() + _HYDRATE_WAIT_S
        while time.monotonic() < hydrate_until:
            check_control()
            if self._has_feed():
                write_status("ok", "Feed ready")
                return True
            if self._is_challenge():
                break
            self._page.wait_for_timeout(1000)

        if self._has_feed():
            write_status("ok", "Feed ready")
            return True
        if not self._is_challenge():
            return False
        if self._headless:
            raise RuntimeError(
                "Reddit showed a captcha but Chrome is headless. "
                "Run headed (--headed, or the website checkbox) and solve it there."
            )
        if self._captcha_wait_s <= 0:
            return False

        logger.warning(
            "shreddit_captcha_waiting",
            extra={
                "seconds": self._captcha_wait_s,
                "note": (
                    "Solve the captcha in the Chrome window. "
                    "The website preview updates while you do."
                ),
            },
        )
        self._focus_page()
        deadline = time.monotonic() + self._captcha_wait_s
        last_shot = 0.0
        while time.monotonic() < deadline:
            check_control()
            if self._has_feed():
                write_status("ok", "Captcha passed")
                logger.info("shreddit_captcha_passed")
                return True
            now = time.monotonic()
            if now - last_shot >= 2.0:
                remaining = int(deadline - now)
                self._snapshot_challenge(
                    f"Solve the captcha in the Chrome window ({remaining}s left)."
                )
                last_shot = now
            self._page.wait_for_timeout(1000)
        return self._has_feed()

    def _is_blocked(self) -> bool:
        if self._page is None:
            return True
        try:
            # Real Shreddit content always wins — noscript / cookie copy must not
            # look like a block page if posts already rendered.
            if self._page.locator(_POST_SELECTOR).count() > 0:
                return False
            if self._page.locator(
                "shreddit-app, shreddit-title, shreddit-feed, shreddit-comment"
            ).count() > 0:
                return False
            title = (self._page.title() or "").lower()
            body = ""
            with contextlib.suppress(Exception):
                body = (self._page.inner_text("body", timeout=2000) or "")[:1500].lower()
            hay = f"{title}\n{body}"
            if any(s in hay for s in _BLOCK_SNIPPETS):
                return True
            url = self._page.url or ""
            return bool("/login" in url and "dest=" in url)
        except Exception:
            return True

    def _dismiss_chrome(self) -> None:
        """Best-effort dismiss cookie / app-install / NSFW gates."""
        assert self._page is not None
        for selector in (
            'button:has-text("Accept all")',
            'button:has-text("Accept All")',
            'button:has-text("Accept")',
            'button:has-text("Continue")',
            'button:has-text("Yes, I\'m over 18")',
            'button:has-text("Update")',
            '[data-testid="cookie-banner"] button',
        ):
            try:
                loc = self._page.locator(selector)
                if loc.count() > 0:
                    loc.first.click(timeout=800)
            except Exception:
                continue

    def _goto(self, url: str, *, wait_selector: str | None = None) -> None:
        """Navigate, rotating proxies on connection / block failures."""
        last_exc: Exception | None = None
        attempts = min(_MAX_PROXY_TRIES, max(1, self._pool.healthy_count))
        for _ in range(attempts):
            check_control()
            self._ensure_browser()
            self._throttle()
            assert self._page is not None
            try:
                self._page.goto(
                    url, wait_until="domcontentloaded", timeout=self._nav_timeout_ms
                )
                self._last_request_at = time.monotonic()
                self._page.wait_for_timeout(1200)
                self._dismiss_chrome()
                if wait_selector:
                    ready = self._wait_for_feed()
                    if not ready and self._is_challenge():
                        raise RuntimeError("reddit_block_or_challenge")
                    if not ready:
                        loc = self._page.locator(wait_selector)
                        with contextlib.suppress(Exception):
                            loc.first.wait_for(state="attached", timeout=8000)
                if self._is_blocked():
                    raise RuntimeError("reddit_block_or_challenge")
                host = urlparse(url).path
                logger.info(
                    "shreddit_navigated",
                    extra={"path": host, "proxy": self._active_proxy_label},
                )
                return
            except Exception as exc:
                last_exc = exc
                logger.warning(
                    "shreddit_navigation_failed",
                    extra={
                        "path": urlparse(url).path,
                        "reason": type(exc).__name__,
                        "proxy": self._active_proxy_label,
                    },
                )
                self._rotate_proxy(reason=type(exc).__name__)
                continue
        raise RuntimeError(
            "All proxies failed while navigating Shreddit "
            f"({type(last_exc).__name__ if last_exc else 'unknown'})"
        )

    # ------------------------------------------------------------------
    # Listing
    # ------------------------------------------------------------------

    def _fetch_json(self, url: str) -> Any | None:
        """GET JSON through the live browser context (same proxy + cookies)."""
        assert self._page is not None
        try:
            resp = self._page.request.get(
                url,
                timeout=self._nav_timeout_ms,
                headers={"accept": "application/json, text/plain, */*"},
            )
            if resp.status != 200:
                logger.info(
                    "shreddit_json_http",
                    extra={"status": resp.status, "path": urlparse(url).path},
                )
                return None
            data = resp.json()
            if isinstance(data, dict) and data.get("error"):
                return None
            return data
        except Exception as exc:
            logger.info(
                "shreddit_json_failed",
                extra={"reason": type(exc).__name__, "path": urlparse(url).path},
            )
            return None

    def _dom_snapshot(self, request: CollectionRequest) -> None:
        assert self._page is not None
        selectors = (
            "shreddit-post",
            "article",
            "shreddit-feed",
            "shreddit-app",
            "faceplate-tracker",
        )
        counts = {sel: self._page.locator(sel).count() for sel in selectors}
        logger.info(
            "shreddit_dom_snapshot",
            extra={
                "path": urlparse(self._page.url or "").path,
                "title": (self._page.title() or "")[:80],
                "counts": counts,
                "collection_run_id": request.collection_run_id,
            },
        )

    def _read_listing_cards(self) -> list[dict[str, str | bool]]:
        """Read listing cards via locators (pierces closed shadow roots)."""
        assert self._page is not None
        loc = self._page.locator(_POST_SELECTOR)
        cards: list[dict[str, str | bool]] = []
        for i in range(loc.count()):
            el = loc.nth(i)
            cls = (el.get_attribute("class") or "").lower()
            cards.append(
                {
                    "id": el.get_attribute("id") or "",
                    "permalink": el.get_attribute("permalink") or "",
                    "promoted": bool(
                        el.get_attribute("promoted") is not None or "promoted" in cls
                    ),
                }
            )
        return cards

    def _json_listing(self, request: CollectionRequest) -> list[dict[str, Any]]:
        """Paginate Reddit listing JSON through the browser session."""
        limit = request.limit if request.limit is not None else 100
        yielded: list[dict[str, Any]] = []
        after: str | None = None
        while len(yielded) < limit:
            page_size = min(100, limit - len(yielded))
            url, params = _listing_endpoint(request, page_size=page_size, after=after)
            payload = self._fetch_json(f"{url}?{urlencode(params)}")
            if not isinstance(payload, dict):
                return yielded
            children = payload.get("data", {}).get("children") or []
            if not children:
                break
            for child in children:
                if child.get("kind") != "t3":
                    continue
                data = child.get("data") or {}
                if not data.get("id"):
                    continue
                yielded.append(data)
                if len(yielded) >= limit:
                    break
            after = payload.get("data", {}).get("after")
            if not after:
                break
        return yielded

    def _json_comments(
        self, submission_id: str, request: CollectionRequest
    ) -> list[dict[str, Any]] | None:
        params: dict[str, Any] = {"raw_json": 1, "sort": request.comment_sort}
        if request.max_comments_per_submission:
            params["limit"] = request.max_comments_per_submission
        elif request.max_comments_per_submission is None:
            params["limit"] = 500
        if request.max_depth is not None:
            params["depth"] = request.max_depth
        payload = self._fetch_json(
            f"https://www.reddit.com/comments/{submission_id}.json?{urlencode(params)}"
        )
        if not isinstance(payload, list) or len(payload) < 2:
            return None
        listing = payload[1].get("data", {}).get("children", [])
        out: list[dict[str, Any]] = []
        more = 0
        for raw_com, traversed_depth in _flatten_comment_tree(listing):
            if raw_com is _MORE_SENTINEL:
                more += 1
                continue
            raw_com.setdefault("depth", traversed_depth)
            out.append(raw_com)
        if more:
            self._json_more.add(submission_id)
            logger.info(
                "shreddit_json_more_truncated",
                extra={
                    "submission_id": submission_id,
                    "more_nodes": more,
                    "parsed": len(out),
                },
            )
        return out

    def _collect_listing_permalinks(self, request: CollectionRequest) -> list[str]:
        self._json_post_cache = {}
        self._json_more = set()
        url = listing_url(
            subreddit=request.subreddit,
            listing_type=request.listing_type,
            search_query=request.search_query,
            sort=request.sort,
            time_filter=request.time_filter,
        )
        self._goto(url, wait_selector=_POST_SELECTOR)

        json_posts = self._json_listing(request)
        if json_posts:
            self._json_post_cache = {p["id"]: p for p in json_posts}
            permalinks = [
                p["permalink"] for p in json_posts if p.get("permalink")
            ]
            logger.info(
                "shreddit_listing_json",
                extra={
                    "subreddit": request.subreddit,
                    "count": len(permalinks),
                    "collection_run_id": request.collection_run_id,
                },
            )
            if permalinks:
                return permalinks

        limit = request.limit if request.limit is not None else 100
        ordered: list[str] = []
        seen: set[str] = set()
        idle = 0

        for _scroll in range(self._max_scrolls):
            check_control()
            assert self._page is not None
            cards = self._read_listing_cards()
            added = 0
            for card in cards:
                if card.get("promoted"):
                    continue
                pid = str(card.get("id") or "")
                permalink = str(card.get("permalink") or "")
                if not pid.startswith("t3_") or not permalink:
                    continue
                if pid in seen:
                    continue
                seen.add(pid)
                ordered.append(permalink)
                added += 1
                if len(ordered) >= limit:
                    logger.info(
                        "shreddit_listing_collected",
                        extra={
                            "subreddit": request.subreddit,
                            "count": len(ordered),
                            "collection_run_id": request.collection_run_id,
                        },
                    )
                    return ordered[:limit]

            if added == 0:
                idle += 1
            else:
                idle = 0

            if idle >= 3:
                break

            try:
                self._page.evaluate("window.scrollBy(0, document.body.scrollHeight)")
            except Exception:
                break
            self._page.wait_for_timeout(self._scroll_wait_ms)
            self._click_feed_loader()

        logger.info(
            "shreddit_listing_collected",
            extra={
                "subreddit": request.subreddit,
                "count": len(ordered),
                "collection_run_id": request.collection_run_id,
            },
        )
        if not ordered:
            self._dom_snapshot(request)
            raise RuntimeError(
                f"No shreddit-post cards found for r/{request.subreddit}. "
                "The listing page was empty or blocked."
            )
        return ordered[:limit]

    def _click_feed_loader(self) -> None:
        assert self._page is not None
        for selector in (
            "faceplate-partial[src*='feeds']",
            "faceplate-partial[src*='feed']",
            "button:has-text('View more posts')",
            "button:has-text('See more')",
        ):
            try:
                loc = self._page.locator(selector)
                if loc.count() > 0:
                    loc.first.scroll_into_view_if_needed(timeout=1000)
                    loc.first.click(timeout=1500)
                    return
            except Exception:
                continue

    # ------------------------------------------------------------------
    # Post + comments harvest
    # ------------------------------------------------------------------

    def _harvest_post(
        self, permalink: str, request: CollectionRequest
    ) -> SubmissionRecord | None:
        reddit_id = _reddit_id_from_permalink(permalink)
        json_post = self._json_post_cache.get(reddit_id) if reddit_id else None
        json_comments: list[dict[str, Any]] | None = None
        if reddit_id and request.max_comments_per_submission != 0:
            json_comments = self._json_comments(reddit_id, request)

        want_all = request.max_comments_per_submission is None
        listed_comments = int((json_post or {}).get("num_comments") or 0)
        json_short = (
            json_comments is not None
            and want_all
            and listed_comments > len(json_comments) + 2
        )
        need_dom = json_post is None or (
            request.max_comments_per_submission != 0
            and (
                json_comments is None
                or (want_all and reddit_id in self._json_more)
                or json_short
            )
        )

        if need_dom:
            url = comments_url(permalink, sort=request.comment_sort)
            self._goto(url, wait_selector=_POST_SELECTOR)
            if request.max_comments_per_submission != 0:
                self._expand_more_comments(request)

        if json_post:
            raw: dict[str, Any] | None = json_post
        elif need_dom:
            raw = self._extract_post(request)
        else:
            raw = None
        if raw is None:
            return None

        comments_raw: list[dict[str, Any]] = list(json_comments or [])
        if need_dom and request.max_comments_per_submission != 0:
            comments_raw = _merge_comment_dicts(
                comments_raw, self._extract_comments(request)
            )
        self._comment_cache[raw["id"]] = comments_raw
        logger.info(
            "shreddit_comments_extracted",
            extra={
                "submission_id": raw["id"],
                "count": len(comments_raw),
                "via_json": json_comments is not None,
                "collection_run_id": request.collection_run_id,
            },
        )

        record = map_submission_dict(
            raw,
            collection_run_id=request.collection_run_id,
            sampling_strategy=request.sampling_strategy,
            matched_query_or_keyword=request.matched_query_or_keyword,
        )
        logger.info(
            "submission_seen",
            extra={
                "reddit_fullname": record.reddit_fullname,
                "subreddit": record.subreddit,
                "source": "shreddit",
                "collection_run_id": request.collection_run_id,
            },
        )
        return record

    def _extract_post(self, request: CollectionRequest) -> dict[str, Any] | None:
        assert self._page is not None
        payload: dict[str, Any] | None
        loc = self._page.locator(_POST_SELECTOR)
        try:
            payload = loc.first.evaluate(_EXTRACT_POST_JS) if loc.count() else None
        except Exception:
            payload = None

        if payload and payload.get("attrs"):
            raw = submission_raw_from_shreddit(
                attrs=payload.get("attrs") or {},
                selftext=payload.get("selftext") or "",
                flair_text=payload.get("flair_text") or None,
                nview=payload.get("nview"),
                flags=payload.get("flags") or [],
            )
            if not raw.get("subreddit"):
                raw["subreddit"] = request.subreddit
            if raw.get("id"):
                return raw

        html = self._page.content()
        posts, _comments = parse_shreddit_html(
            html, fallback_subreddit=request.subreddit
        )
        return posts[0] if posts else None

    def _expand_more_comments(self, request: CollectionRequest) -> None:
        assert self._page is not None
        # replace_more_limit is a PRAW knob. 0 still means "do not expand".
        if self._more_comments_clicks <= 0 or request.replace_more_limit == 0:
            return
        cap = self._more_comments_clicks
        # Unlimited comments: keep expanding until the tree is exhausted.
        if request.max_comments_per_submission is None:
            cap = max(cap, 400)

        clicks = 0
        idle = 0
        while clicks < cap:
            check_control()
            if request.max_comments_per_submission is not None:
                n = self._page.locator(_COMMENT_SELECTOR).count()
                if n >= request.max_comments_per_submission:
                    return

            loc = self._page.locator(_MORE_COMMENTS_SELECTOR)
            count = loc.count()
            if count == 0:
                alt = self._page.locator(
                    "button:has-text('more replies'), "
                    "button:has-text('More replies'), "
                    "button:has-text('Continue this thread'), "
                    "a:has-text('Continue this thread')"
                )
                if alt.count() == 0:
                    return
                try:
                    alt.first.scroll_into_view_if_needed(timeout=2000)
                    alt.first.click(timeout=3000)
                    clicks += 1
                    idle = 0
                    self._page.wait_for_timeout(self._expand_wait_ms)
                except Exception:
                    return
                continue

            clicked = 0
            batch = min(count, 6)
            for i in range(batch):
                el = loc.nth(i)
                src = el.get_attribute("src") or ""
                if request.max_depth is not None and "startingDepth=" in src:
                    try:
                        depth = int(src.split("startingDepth=", 1)[1].split("&", 1)[0])
                        if depth > request.max_depth:
                            continue
                    except ValueError:
                        pass
                try:
                    el.scroll_into_view_if_needed(timeout=2000)
                    el.click(timeout=3000)
                    clicks += 1
                    clicked += 1
                    if clicks >= cap:
                        break
                except Exception:
                    continue
            if clicked == 0:
                idle += 1
                if idle >= 2:
                    return
            else:
                idle = 0
            self._page.wait_for_timeout(self._expand_wait_ms)

    def _extract_comments(self, request: CollectionRequest) -> list[dict[str, Any]]:
        assert self._page is not None
        payload: list[dict[str, Any]]
        try:
            payload = self._page.locator(_COMMENT_SELECTOR).evaluate_all(
                _EXTRACT_COMMENTS_JS
            )
        except Exception:
            html = self._page.content()
            _, parsed_comments = parse_shreddit_html(
                html, fallback_subreddit=request.subreddit
            )
            return parsed_comments

        comments: list[dict[str, Any]] = []
        for item in payload or []:
            attrs = item.get("attrs") or {}
            try:
                raw = comment_raw_from_shreddit(
                    attrs=attrs,
                    body=item.get("body") or "",
                    flags=item.get("flags") or [],
                    fallback_subreddit=request.subreddit,
                )
            except (ValueError, KeyError):
                continue
            if not raw.get("id"):
                continue
            comments.append(raw)
        return comments

    # ------------------------------------------------------------------
    # Protocol
    # ------------------------------------------------------------------

    def iter_submissions(
        self,
        request: CollectionRequest,
    ) -> Iterable[SubmissionRecord]:
        permalinks = self._collect_listing_permalinks(request)
        for permalink in permalinks:
            check_control()
            try:
                record = self._harvest_post(permalink, request)
            except ScrapeStopRequested:
                raise
            except Exception as exc:
                logger.warning(
                    "shreddit_post_harvest_failed",
                    extra={
                        "reason": type(exc).__name__,
                        "error": str(exc)[:200],
                        "collection_run_id": request.collection_run_id,
                    },
                )
                continue
            if record is None:
                continue
            yield record

    def iter_comments(
        self,
        submission: SubmissionRecord,
        request: CollectionRequest,
    ) -> Iterable[CommentRecord]:
        cached = self._comment_cache.get(submission.reddit_id)
        if cached is None:
            # Cache miss (e.g. caller skipped harvest): visit the comments page.
            try:
                self._harvest_post(submission.permalink, request)
            except Exception as exc:
                logger.warning(
                    "shreddit_comment_harvest_failed",
                    extra={
                        "reason": type(exc).__name__,
                        "collection_run_id": request.collection_run_id,
                    },
                )
                return
            cached = self._comment_cache.get(submission.reddit_id, [])

        limit = request.max_comments_per_submission
        count = 0
        for raw in cached:
            if limit is not None and count >= limit:
                break
            depth = int(raw.get("depth", 0) or 0)
            if request.max_depth is not None and depth > request.max_depth:
                continue
            body = raw.get("body") or ""
            if not request.include_deleted and body in ("[deleted]", "[removed]"):
                continue
            record = map_comment_dict(raw, collection_run_id=request.collection_run_id)
            logger.debug(
                "comment_seen",
                extra={
                    "reddit_fullname": record.reddit_fullname,
                    "source": "shreddit",
                    "collection_run_id": request.collection_run_id,
                },
            )
            count += 1
            yield record

    def __del__(self) -> None:  # pragma: no cover - best-effort cleanup
        with contextlib.suppress(Exception):
            self.close()
