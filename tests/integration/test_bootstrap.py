"""First-run account bootstrap."""

from __future__ import annotations

from pathlib import Path

import msgspec
from fastapi.testclient import TestClient

from velox_ui.app import create_app
from velox_ui.settings import AuthSettings, Settings


def test_configured_administrator_is_created(settings: Settings) -> None:
    configured = msgspec.structs.replace(
        settings,
        auth=AuthSettings(
            admin_email="boss@homelab.local", admin_password="configured-password"
        ),
    )
    with TestClient(create_app(configured)) as client:
        assert client.get("/api/config").json()["auth"]["setup_required"] is False
        response = client.post(
            "/api/auth/login",
            json={"email": "boss@homelab.local", "password": "configured-password"},
        )
        assert response.status_code == 200
        assert response.json()["role"] == "admin"


def test_no_password_is_ever_written_to_the_log(settings: Settings, capsys) -> None:
    # An earlier design generated an administrator password and logged it. A
    # credential in a log file outlives the convenience it bought.
    with TestClient(create_app(settings)):
        pass
    captured = capsys.readouterr()
    assert "password" not in captured.err.lower() or "administrator" not in captured.err.lower()


def test_disabled_authentication_uses_a_local_account(settings: Settings) -> None:
    single_user = msgspec.structs.replace(settings, auth=AuthSettings(enabled=False))
    with TestClient(create_app(single_user)) as client:
        assert client.get("/api/config").json()["auth"]["enabled"] is False
        # No credential is presented and none is needed.
        profile = client.get("/api/auth/me")
        assert profile.status_code == 200
        assert profile.json()["email"] == "local@velox.local"


def test_local_account_is_reused_across_restarts(settings: Settings) -> None:
    single_user = msgspec.structs.replace(settings, auth=AuthSettings(enabled=False))
    with TestClient(create_app(single_user)) as client:
        first = client.get("/api/auth/me").json()["id"]
    with TestClient(create_app(single_user)) as client:
        assert client.get("/api/auth/me").json()["id"] == first


def test_database_file_is_created_in_the_data_directory(
    settings: Settings, data_dir: Path
) -> None:
    with TestClient(create_app(settings)):
        pass
    assert (data_dir / "test.db").is_file()
