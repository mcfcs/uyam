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


def test_time_limit_raises() -> None:
    from uyam.scrape_status import write_run_meta

    clear_status()
    set_control("run")
    write_run_meta(pid=1, max_seconds=0.01)
    import time

    time.sleep(0.05)
    with pytest.raises(ScrapeStopRequested) as excinfo:
        check_control()
    assert excinfo.value.reason == "time_limit_reached"
    clear_status()


def test_no_deadline_is_noop() -> None:
    from uyam.scrape_status import write_run_meta

    clear_status()
    set_control("run")
    write_run_meta(pid=1, max_seconds=None)
    check_control()
    clear_status()


def test_worker_pid_registry() -> None:
    import os

    from uyam.scrape_status import (
        list_worker_pids,
        register_worker_pid,
        reset_worker_pids,
        scrape_is_running,
        unregister_worker_pid,
    )

    clear_status()
    reset_worker_pids()
    register_worker_pid(os.getpid())
    assert os.getpid() in list_worker_pids()
    assert scrape_is_running() is True
    unregister_worker_pid(os.getpid())
    assert os.getpid() not in list_worker_pids()
    clear_status()


def test_process_alive_self() -> None:
    import os

    from uyam.scrape_status import process_alive

    assert process_alive(os.getpid()) is True
    assert process_alive(0) is False
    assert process_alive(-1) is False
    assert process_alive(999_999_999) is False
