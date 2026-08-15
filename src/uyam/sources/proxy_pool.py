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
from pathlib import Path
from urllib.parse import urlparse

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

    @classmethod
    def from_file(cls, path: Path | None) -> ProxyPool:
        """Create a ProxyPool from a file path. Returns an empty pool if path is None."""
        if path is None:
            return cls([])
        return cls(load_proxies(path))

    def is_empty(self) -> bool:
        return len(self._proxies) == 0

    def current(self) -> str | None:
        """Return the current proxy URL, or None for a direct connection."""
        while self._index < len(self._proxies):
            if self._index not in self._unhealthy:
                proxy = self._proxies[self._index]
                logger.debug(
                    "proxy_selected",
                    extra={"proxy": _safe_label(proxy)},
                )
                return proxy
            self._index += 1
        if self._proxies:
            logger.warning(
                "proxy_pool_exhausted: all proxies unhealthy, using direct connection"
            )
        return None

    def mark_current_unhealthy(self) -> None:
        """Mark the current proxy as failed and advance to the next."""
        if self._index < len(self._proxies):
            proxy = self._proxies[self._index]
            logger.warning(
                "proxy_failure",
                extra={"proxy": _safe_label(proxy)},
            )
            self._unhealthy.add(self._index)
            self._index += 1

    def build_session_proxies(self) -> dict[str, str] | None:
        """Return a dict suitable for requests.Session.proxies, or None for direct."""
        url = self.current()
        if url is None:
            return None
        return {"http": url, "https": url}

    @property
    def total(self) -> int:
        return len(self._proxies)

    @property
    def healthy_count(self) -> int:
        return len(self._proxies) - len(self._unhealthy)
