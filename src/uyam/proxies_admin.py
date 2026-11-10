"""Manage proxies.txt from the app: inspect, replace/append, back up, test.

proxies.txt is a secret (gitignored). Nothing here returns a password to the
caller in clear: summaries and test results carry host:port or a masked URL
(`http://user:***@host:port`). Every write first copies the current file to
data/proxy-backups/ (gitignored) and then replaces proxies.txt atomically, so
a running scrape never reads a half-written file. The Shreddit source reads
proxies.txt when a collection starts, so edits apply to the next run.
"""

from __future__ import annotations

import ipaddress
import random
import shutil
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

import requests

from uyam.sources.proxy_pool import _parse_proxy_line

DEFAULT_TEST_URL = "https://www.reddit.com/robots.txt"


@dataclass
class ParsedList:
    lines: list[str] = field(default_factory=list)  # valid source lines, deduplicated
    urls: list[str] = field(default_factory=list)  # parsed URL per kept line
    invalid_lines: list[int] = field(default_factory=list)  # 1-based line numbers
    duplicates: int = 0

    @property
    def providers(self) -> Counter[str]:
        return Counter(provider(u) for u in self.urls)

    @property
    def schemes(self) -> Counter[str]:
        return Counter((urlparse(u).scheme or "http").lower() for u in self.urls)

    @property
    def socks_with_auth(self) -> int:
        """Chrome cannot authenticate to SOCKS proxies — these fail in Shreddit."""
        return sum(
            1
            for u in self.urls
            if urlparse(u).scheme.lower().startswith("socks") and urlparse(u).username
        )


def parse_text(text: str) -> ParsedList:
    out = ParsedList()
    seen: set[str] = set()
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        url = _parse_proxy_line(line)
        if url is None:
            out.invalid_lines.append(number)
            continue
        if url in seen:
            out.duplicates += 1
            continue
        seen.add(url)
        out.lines.append(line)
        out.urls.append(url)
    return out


def read_file(path: Path) -> ParsedList:
    if not path.exists():
        return ParsedList()
    return parse_text(path.read_text(encoding="utf-8", errors="replace"))


def provider(url: str) -> str:
    """Registrable-ish domain for grouping (`wolveproxy.com`), or `raw IP`."""
    host = (urlparse(url).hostname or "").lower()
    try:
        ipaddress.ip_address(host)
        return "raw IP"
    except ValueError:
        parts = host.split(".")
        return ".".join(parts[-2:]) if len(parts) >= 2 else host or "?"


def label(url: str) -> str:
    parsed = urlparse(url)
    return f"{parsed.hostname}:{parsed.port}"


def mask(url: str) -> str:
    parsed = urlparse(url)
    scheme = parsed.scheme or "http"
    user = f"{parsed.username}:***@" if parsed.username else ""
    return f"{scheme}://{user}{parsed.hostname}:{parsed.port}"


# ---------------------------------------------------------------------------
# Writes (always backed up, always atomic)
# ---------------------------------------------------------------------------


def backup(path: Path, backup_dir: Path) -> Path | None:
    if not path.exists():
        return None
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    dest = backup_dir / f"proxies-{stamp}.txt"
    shutil.copy2(path, dest)
    return dest


def _write_atomic(path: Path, lines: list[str], note: str) -> None:
    header = [
        "# Uyam proxy list (gitignored secret). Managed from the Streamlit Proxies tab.",
        f"# {note} — {datetime.now(UTC).isoformat(timespec='seconds')}",
    ]
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text("\n".join(header + lines) + "\n", encoding="utf-8")
    tmp.replace(path)


def save(path: Path, backup_dir: Path, new: ParsedList, *, mode: str) -> dict[str, object]:
    """mode 'replace' writes only `new`; 'append' adds unseen entries to the file."""
    if mode not in ("replace", "append"):
        raise ValueError(f"mode must be 'replace' or 'append', got {mode!r}")
    if not new.lines and mode == "replace":
        raise ValueError("refusing to replace proxies.txt with an empty list")
    current = read_file(path)
    backup_path = backup(path, backup_dir)
    if mode == "replace":
        lines = new.lines
    else:
        known = set(current.urls)
        lines = current.lines + [
            line for line, url in zip(new.lines, new.urls, strict=True) if url not in known
        ]
    _write_atomic(path, lines, f"{mode}: {len(lines)} proxies")
    return {"written": len(lines), "before": len(current.urls), "backup": backup_path}


def remove_providers(path: Path, backup_dir: Path, providers: set[str]) -> dict[str, object]:
    current = read_file(path)
    keep = [
        line
        for line, url in zip(current.lines, current.urls, strict=True)
        if provider(url) not in providers
    ]
    backup_path = backup(path, backup_dir)
    _write_atomic(path, keep, f"removed {', '.join(sorted(providers))}: {len(keep)} proxies")
    return {"written": len(keep), "removed": len(current.urls) - len(keep), "backup": backup_path}


def list_backups(backup_dir: Path) -> list[Path]:
    return sorted(backup_dir.glob("proxies-*.txt"), reverse=True)


def restore(path: Path, backup_dir: Path, backup_file: Path) -> Path | None:
    if backup_file.parent.resolve() != backup_dir.resolve():
        raise ValueError("can only restore from the proxy backup folder")
    safety = backup(path, backup_dir)
    tmp = path.with_suffix(path.suffix + ".tmp")
    shutil.copy2(backup_file, tmp)
    tmp.replace(path)
    return safety


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------


@dataclass
class ProbeResult:
    proxy: str  # host:port only
    ok: bool
    status: int | None
    ms: int | None
    error: str | None


def _probe(url: str, target: str, timeout: float) -> ProbeResult:
    started = time.monotonic()
    try:
        resp = requests.get(
            target,
            proxies={"http": url, "https": url},
            timeout=timeout,
            headers={"User-Agent": "Mozilla/5.0 (uyam proxy check)"},
        )
        ms = int((time.monotonic() - started) * 1000)
        return ProbeResult(label(url), resp.status_code < 400, resp.status_code, ms, None)
    except requests.RequestException as exc:
        # Exception text can embed the proxy URL (with credentials): report the type only,
        # plus the well-known proxy auth hint.
        detail = type(exc).__name__
        if "407" in str(exc):
            detail += " (407 auth rejected — wrong user/pass or expired plan)"
        return ProbeResult(label(url), False, None, None, detail)


def probe_proxies(
    urls: list[str],
    *,
    sample: int = 10,
    target: str = DEFAULT_TEST_URL,
    timeout: float = 12.0,
    workers: int = 8,
    seed: int | None = None,
) -> list[ProbeResult]:
    """Probe a random sample of proxies concurrently against `target`."""
    if not urls:
        return []
    rng = random.Random(seed)
    chosen = urls if len(urls) <= sample else rng.sample(urls, sample)
    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(chosen)))) as pool:
        return list(pool.map(lambda u: _probe(u, target, timeout), chosen))
