"""Tests for ProxyPool: parsing, fallback, and credential safety."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from uyam.sources.proxy_pool import (
    ProxyPool,
    _parse_proxy_line,
    load_proxies,
    to_playwright_proxy,
)


class TestParseProxyLine:
    def test_url_with_credentials(self) -> None:
        result = _parse_proxy_line("http://user:pass@proxy.example.com:3128")
        assert result == "http://user:pass@proxy.example.com:3128"

    def test_url_without_credentials(self) -> None:
        result = _parse_proxy_line("http://proxy.example.com:8080")
        assert result == "http://proxy.example.com:8080"

    def test_colon_separated_with_credentials(self) -> None:
        result = _parse_proxy_line("proxy.example.com:10800:myuser:mypassword")
        assert result == "http://myuser:mypassword@proxy.example.com:10800"

    def test_blank_line_returns_none(self) -> None:
        assert _parse_proxy_line("") is None
        assert _parse_proxy_line("   ") is None

    def test_comment_line_returns_none(self) -> None:
        assert _parse_proxy_line("# This is a comment") is None

    def test_malformed_line_returns_none(self) -> None:
        result = _parse_proxy_line("not-a-proxy-at-all!!!!")
        assert result is None

    def test_password_with_special_chars(self) -> None:
        result = _parse_proxy_line("proxy.example.com:10800:user:pass:with:colons")
        assert result is not None
        assert "user" in result

    def test_wolveproxy_format(self) -> None:
        line = "p2-g1.wolveproxy.com:11345:77cf9933b0502473:74d7f67aed20426140a7"
        result = _parse_proxy_line(line)
        assert result is not None
        assert "wolveproxy.com" in result
        assert "11345" in result


class TestLoadProxies:
    def test_loads_mixed_formats(self, tmp_path: Path) -> None:
        proxy_file = tmp_path / "proxies.txt"
        proxy_file.write_text(
            "# comment line\n"
            "\n"
            "http://proxy1.example.com:8080\n"
            "http://user:pass@proxy2.example.com:3128\n"
            "proxy3.example.com:10800:testuser:testpass\n",
            encoding="utf-8",
        )
        proxies = load_proxies(proxy_file)
        assert len(proxies) == 3

    def test_missing_file_returns_empty(self, tmp_path: Path) -> None:
        proxies = load_proxies(tmp_path / "nonexistent.txt")
        assert proxies == []

    def test_blank_and_comment_lines_ignored(self, tmp_path: Path) -> None:
        proxy_file = tmp_path / "proxies.txt"
        proxy_file.write_text(
            "# header comment\n\n# another comment\n\n",
            encoding="utf-8",
        )
        proxies = load_proxies(proxy_file)
        assert proxies == []


class TestProxyPool:
    def test_empty_pool_returns_none(self) -> None:
        pool = ProxyPool([])
        assert pool.current() is None

    def test_single_proxy_returned(self) -> None:
        pool = ProxyPool(["http://proxy1.example.com:8080"])
        assert pool.current() == "http://proxy1.example.com:8080"

    def test_fallback_on_unhealthy(self) -> None:
        pool = ProxyPool([
            "http://proxy1.example.com:8080",
            "http://proxy2.example.com:8080",
        ])
        # First proxy is healthy
        first = pool.current()
        assert first == "http://proxy1.example.com:8080"

        # Mark it unhealthy
        pool.mark_current_unhealthy()

        # Should get second proxy
        second = pool.current()
        assert second == "http://proxy2.example.com:8080"

    def test_all_unhealthy_returns_none(self) -> None:
        pool = ProxyPool([
            "http://proxy1.example.com:8080",
            "http://proxy2.example.com:8080",
        ])
        pool.mark_current_unhealthy()
        pool.mark_current_unhealthy()
        assert pool.current() is None

    def test_from_file_missing(self, tmp_path: Path) -> None:
        pool = ProxyPool.from_file(tmp_path / "nonexistent.txt")
        assert pool.is_empty()
        assert pool.current() is None

    def test_from_file_none(self) -> None:
        pool = ProxyPool.from_file(None)
        assert pool.is_empty()

    def test_build_session_proxies_with_proxy(self) -> None:
        pool = ProxyPool(["http://user:pass@proxy.example.com:3128"])
        result = pool.build_session_proxies()
        assert result is not None
        assert result["http"] == "http://user:pass@proxy.example.com:3128"
        assert result["https"] == "http://user:pass@proxy.example.com:3128"

    def test_build_session_proxies_empty_pool(self) -> None:
        pool = ProxyPool([])
        assert pool.build_session_proxies() is None

    def test_healthy_count(self) -> None:
        pool = ProxyPool(["http://p1:8080", "http://p2:8080", "http://p3:8080"])
        assert pool.healthy_count == 3

    def test_rotated_starts_at_offset(self) -> None:
        pool = ProxyPool(
            ["http://p1:8080", "http://p2:8080", "http://p3:8080"]
        )
        rotated = pool.rotated(1)
        assert rotated.current() == "http://p2:8080"
        assert pool.current() == "http://p1:8080"
        pool.mark_current_unhealthy()
        assert pool.healthy_count == 2


class TestToPlaywrightProxy:
    def test_splits_credentials(self) -> None:
        result = to_playwright_proxy("http://user:pass@proxy.example.com:3128")
        assert result == {
            "server": "http://proxy.example.com:3128",
            "username": "user",
            "password": "pass",
        }

    def test_without_credentials(self) -> None:
        result = to_playwright_proxy("http://proxy.example.com:8080")
        assert result == {"server": "http://proxy.example.com:8080"}

    def test_pool_current_playwright(self) -> None:
        pool = ProxyPool(["http://u:p@proxy.example.com:1080"])
        assert pool.current_playwright() == {
            "server": "http://proxy.example.com:1080",
            "username": "u",
            "password": "p",
        }

    def test_empty_pool_current_playwright(self) -> None:
        assert ProxyPool([]).current_playwright() is None


class TestCredentialsNeverInLogs:
    """Credentials (usernames/passwords) must never appear in log output."""

    def test_proxy_label_omits_credentials(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        proxy_file = tmp_path / "proxies.txt"
        proxy_file.write_text(
            "http://secretuser:secretpassword@proxy.example.com:3128\n",
            encoding="utf-8",
        )
        with caplog.at_level(logging.DEBUG):
            pool = ProxyPool.from_file(proxy_file)
            pool.current()
            pool.mark_current_unhealthy()

        full_log = "\n".join(r.getMessage() for r in caplog.records)
        assert "secretuser" not in full_log
        assert "secretpassword" not in full_log

    def test_colon_sep_proxy_label_omits_credentials(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        pool = ProxyPool(["http://hidden_user:hidden_pass@proxy.example.com:8080"])
        with caplog.at_level(logging.DEBUG):
            pool.current()
            pool.mark_current_unhealthy()

        full_log = "\n".join(r.getMessage() for r in caplog.records)
        assert "hidden_user" not in full_log
        assert "hidden_pass" not in full_log


class TestProxyFailureFallback:
    """Mock connection-level failure: first proxy fails, second succeeds."""

    def test_connection_error_triggers_fallback(self) -> None:
        pool = ProxyPool([
            "http://bad-proxy.example.com:9999",
            "http://good-proxy.example.com:8080",
        ])

        def simulate_request(pool: ProxyPool) -> str:
            proxy = pool.current()
            if proxy and "bad-proxy" in proxy:
                # Simulate connection error
                pool.mark_current_unhealthy()
                proxy = pool.current()
            return proxy or "direct"

        result = simulate_request(pool)
        assert result == "http://good-proxy.example.com:8080"
        assert pool.healthy_count == 1
