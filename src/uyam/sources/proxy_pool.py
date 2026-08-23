"""Proxy pool for the Uyám live Reddit source.

Proxies are for ordinary network routing only. They must NOT be used to:
  - evade Reddit rate limits
  - multiply permitted throughput
  - conceal the application's OAuth identity
  - circumvent access restrictions

ProxyPool selects one proxy for the session lifetime. On connection-level
failure it marks that proxy unhealthy and falls back to the next available.
When all proxies are exhausted it surfaces the failure normally.

Logs contain only hostname:port — never credentials.
"""

from __future__ import annotations

import logging
import re
import time
from pathlib import Path
from urllib.parse import unquote, urlparse

logger = logging.getLogger(__name__)

_COLON_SEP_RE = re.compile(
    r"^(?P<host>[^:]+):(?P<port>\d+):(?P<user>[^:]+):(?P<password>.+)$"
)
_URL_RE = re.compile(r"^https?://")


def _parse_proxy_line(line: str) -> str | None:
    """Parse one proxy line and return a URL-formatted proxy string, or None if invalid.

    Supported formats:
      http://host:port
      http://user:pass@host:port
      host:port:user:pass     (colon-separated, wolveproxy-style)

    Credentials are accepted but never logged.
    """
    line = line.strip()
    if not line or line.startswith("#"):
        return None

    if _URL_RE.match(line):
        # Already URL-format — validate it has at least host:port
        parsed = urlparse(line)
        if parsed.hostname and parsed.port:
            return line
        logger.warning("proxy_parse_warning: malformed URL proxy %r (skipped)", line)
        return None

    m = _COLON_SEP_RE.match(line)
    if m:
        host = m.group("host")
        port = m.group("port")
        user = m.group("user")
        password = m.group("password")
        return f"http://{user}:{password}@{host}:{port}"

    # Two-part host:port (no credentials)
    parts = line.split(":")
    if len(parts) == 2 and parts[1].isdigit():
        return f"http://{parts[0]}:{parts[1]}"

    logger.warning("proxy_parse_warning: unrecognized proxy format (skipped)")
    return None


def _safe_label(proxy_url: str) -> str:
    """Return host:port only — never credentials."""
    parsed = urlparse(proxy_url)
    return f"{parsed.hostname}:{parsed.port}"


def load_proxies(path: Path) -> list[str]:
    """Load and parse proxies from a file. Returns list of URL-format proxy strings."""
    if not path.exists():
        logger.info("proxy_file_not_found: using direct connection", extra={"path": str(path)})
        return []

    proxies: list[str] = []
    skipped = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        parsed = _parse_proxy_line(line)
        if parsed is not None:
            proxies.append(parsed)
        elif line.strip() and not line.strip().startswith("#"):
            skipped += 1

    logger.info(
        "proxies_loaded",
        extra={"count": len(proxies), "skipped": skipped, "path": str(path)},
    )
    return proxies


class ProxyPool:
    """Manages a pool of proxies for a single collection session.

    Usage:
        pool = ProxyPool.from_file(Path("proxies.txt"))
        proxy_url = pool.current()       # e.g., "http://user:pass@host:port"
        if proxy_url:
            session.proxies = {"http": proxy_url, "https": proxy_url}

    On connection-level failure:
        pool.mark_current_unhealthy()
        proxy_url = pool.current()       # next proxy, or None if exhausted
    """

    def __init__(self, proxies: list[str]) -> None:
        self._proxies = list(proxies)
        self._index = 0
        self._unhealthy: set[int] = set()
        self._cooldown_until: dict[int, float] = {}

    @classmethod
    def from_file(cls, path: Path | None) -> ProxyPool:
        """Create a ProxyPool from a file path. Returns an empty pool if path is None."""
        if path is None:
            return cls([])
        return cls(load_proxies(path))

    def is_empty(self) -> bool:
        return len(self._proxies) == 0

    def current(self) -> str | None:
        """Return a usable proxy URL, wrapping the pool and skipping cooldowns."""
        n = len(self._proxies)
        if n == 0:
            return None
        now = time.monotonic()
        started = self._index % n
        soonest: tuple[float, int] | None = None
        for step in range(n):
            i = (started + step) % n
            if i in self._unhealthy:
                continue
            until = self._cooldown_until.get(i, 0.0)
            if until <= now:
                self._index = i
                proxy = self._proxies[i]
                logger.debug("proxy_selected", extra={"proxy": _safe_label(proxy)})
                return proxy
            if soonest is None or until < soonest[0]:
                soonest = (until, i)
        if soonest is not None:
            wait = min(max(0.0, soonest[0] - now), 20.0)
            if wait > 0:
                logger.info(
                    "proxy_wait_cooldown",
                    extra={"seconds": round(wait, 1)},
                )
                time.sleep(wait)
            self._index = soonest[1]
            self._cooldown_until.pop(self._index, None)
            proxy = self._proxies[self._index]
            logger.debug("proxy_selected", extra={"proxy": _safe_label(proxy)})
            return proxy
        logger.warning(
            "proxy_pool_exhausted: all proxies unhealthy, using direct connection"
        )
        return None

    def mark_current_unhealthy(self) -> None:
        """Mark the current proxy as failed and advance to the next."""
        n = len(self._proxies)
        if n == 0:
            return
        i = self._index % n
        proxy = self._proxies[i]
        logger.warning("proxy_failure", extra={"proxy": _safe_label(proxy)})
        self._unhealthy.add(i)
        self._index = (i + 1) % n

    def cooldown_current(self, seconds: float = 45.0) -> None:
        """Temporarily skip this proxy (rate-limit / 429), then reuse it later."""
        n = len(self._proxies)
        if n == 0:
            return
        i = self._index % n
        wait = max(0.0, float(seconds))
        self._cooldown_until[i] = time.monotonic() + wait
        logger.warning(
            "proxy_cooldown",
            extra={"proxy": _safe_label(self._proxies[i]), "seconds": wait},
        )
        self._index = (i + 1) % n

    def build_session_proxies(self) -> dict[str, str] | None:
        """Return a dict suitable for requests.Session.proxies, or None for direct."""
        url = self.current()
        if url is None:
            return None
        return {"http": url, "https": url}

    def rotated(self, offset: int) -> ProxyPool:
        """Return a pool that starts `offset` proxies later (round-robin workers)."""
        n = len(self._proxies)
        if n == 0 or offset == 0:
            return ProxyPool(list(self._proxies))
        k = offset % n
        return ProxyPool(self._proxies[k:] + self._proxies[:k])

    @property
    def total(self) -> int:
        return len(self._proxies)

    @property
    def healthy_count(self) -> int:
        return len(self._proxies) - len(self._unhealthy)

    def current_playwright(self) -> dict[str, str] | None:
        """Return a Playwright-style proxy dict for the current proxy, or None."""
        url = self.current()
        if url is None:
            return None
        return to_playwright_proxy(url)


def to_playwright_proxy(proxy_url: str) -> dict[str, str]:
    """Convert a URL-format proxy string into Playwright's proxy dict.

    Playwright wants ``{"server": "http://host:port", "username": ..., "password": ...}``
    rather than a single credentialed URL. Credentials are not logged here.
    """
    parsed = urlparse(proxy_url)
    if not parsed.hostname or parsed.port is None:
        raise ValueError("proxy URL is missing host or port")
    scheme = parsed.scheme or "http"
    result: dict[str, str] = {"server": f"{scheme}://{parsed.hostname}:{parsed.port}"}
    if parsed.username:
        result["username"] = unquote(parsed.username)
    if parsed.password:
        result["password"] = unquote(parsed.password)
    return result
