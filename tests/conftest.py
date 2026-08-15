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
