"""Tests for scrape-status files used by the website captcha preview."""

from __future__ import annotations

from uyam.scrape_status import clear_status, read_status, write_status


def test_write_read_clear_status() -> None:
    clear_status()
    write_status("captcha", "Solve the captcha in Chrome", url="https://www.reddit.com/")
    data = read_status()
    assert data is not None
    assert data["state"] == "captcha"
    assert "Solve" in data["message"]
    clear_status()
    assert read_status() is None
