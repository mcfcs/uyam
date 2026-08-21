"""Tests for scrape-status files used by the website captcha preview."""

from __future__ import annotations

import pytest

from uyam.scrape_status import (
    ScrapeStopRequested,
    check_control,
    clear_status,
    get_control,
    read_status,
    set_control,
    write_status,
)


def test_write_read_clear_status() -> None:
    clear_status()
    write_status("captcha", "Solve the captcha in Chrome", url="https://www.reddit.com/")
    data = read_status()
    assert data is not None
    assert data["state"] == "captcha"
    assert "Solve" in data["message"]
    clear_status()
    assert read_status() is None


def test_control_run_pause_stop() -> None:
    clear_status()
    set_control("run")
    assert get_control() == "run"
    check_control()  # no-op
    set_control("stop")
    with pytest.raises(ScrapeStopRequested):
        check_control()
    clear_status()
