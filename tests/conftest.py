"""Shared fixtures.

Every test gets its own data directory, database and secret key, so nothing leaks
between tests and none of them can touch a developer's real installation. No test
requires a network, an API key or a real inference backend, which is a hard rule for
this suite (see docs/design/00-overview.md).
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from velox_ui.app import create_app
from velox_ui.settings import AuthSettings, DatabaseSettings, ProviderSettings, Settings

TEST_SECRET = "test-secret-key-0123456789abcdef"


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    """An isolated data directory."""
    path = tmp_path / "data"
    path.mkdir()
    return path


@pytest.fixture
def settings(data_dir: Path) -> Settings:
    """Settings pointing at an isolated SQLite database."""
    return Settings(
        data_dir=data_dir,
        secret_key=TEST_SECRET,
        db=DatabaseSettings(url=f"sqlite+aiosqlite:///{(data_dir / 'test.db').as_posix()}"),
        auth=AuthSettings(access_token_ttl_s=60, refresh_token_ttl_s=3600),
        # Autodiscovery is off in tests: a developer with Ollama running on the
        # standard port would otherwise silently get a real backend registered
        # inside their test instance.
        providers=ProviderSettings(autodiscover=False),
        log_level="ERROR",
    )


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    """A test client with the application's lifespan run."""
    with TestClient(create_app(settings)) as test_client:
        yield test_client


@pytest.fixture
def registered(client: TestClient) -> dict[str, str]:
    """Register the first account and return usable auth headers.

    The first account on a fresh instance is always the administrator, which is the
    first-run claim flow described in :mod:`velox_ui.lifespan`.
    """
    response = client.post(
        "/api/auth/register",
        json={
            "email": "owner@homelab.local",
            "password": "correct-horse-battery",
            "name": "Owner",
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    return {
        "user_id": body["user_id"],
        "role": body["role"],
        "token": body["access_token"],
        "header": f"Bearer {body['access_token']}",
    }


@pytest.fixture(autouse=True)
def _clean_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove any VELOX_* variable from the developer's shell.

    Without this, running the suite on a machine that has a real instance configured
    would silently point tests at that instance's database.
    """
    for name in list(os.environ):
        if name.startswith("VELOX_"):
            monkeypatch.delenv(name, raising=False)
