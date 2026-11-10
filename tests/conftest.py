"""Shared pytest fixtures for the Uyám test suite."""

from __future__ import annotations

from pathlib import Path

import pytest

TEST_HMAC_KEY = "test-hmac-key-shared-conftest"
FIXTURE_PATH = Path(__file__).parent.parent / "fixtures" / "sample_posts.json"


@pytest.fixture
def fixture_path() -> Path:
    return FIXTURE_PATH


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    d = tmp_path / "data"
    d.mkdir()
    return d


@pytest.fixture(autouse=True)
def isolated_scrape_status(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the scrape status/control files at a temp dir for every test.

    They live in data/.scrape-status/ by default — shared with real scrapes.
    A live Stop left control.json at "stop" and failed 8 unrelated tests
    (2026-11-11), and test runs wrote fake proxy failures into the real
    collect.log. Tests must neither read nor write the operator's state.
    """
    import uyam.cli
    import uyam.parallel
    import uyam.scrape_status as ss

    status = tmp_path / "scrape-status"
    monkeypatch.setenv("UYAM_SCRAPE_STATUS_DIR", str(status))  # for worker processes
    for name, value in {
        "STATUS_DIR": status,
        "STATUS_FILE": status / "status.json",
        "SCREENSHOT_FILE": status / "captcha.png",
        "CONTROL_FILE": status / "control.json",
        "RUN_FILE": status / "run.json",
        "LOG_FILE": status / "collect.log",
        "WORKERS_DIR": status / "workers",
    }.items():
        monkeypatch.setattr(ss, name, value)
    monkeypatch.setattr(uyam.cli, "LOG_FILE", status / "collect.log")
    monkeypatch.setattr(uyam.parallel, "LOG_FILE", status / "collect.log")
    return status
