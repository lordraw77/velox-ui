"""``/api/plugins`` discovery, configuration and validation (ADR-0014)."""

from __future__ import annotations

from fastapi.testclient import TestClient


def _headers(registered: dict) -> dict[str, str]:
    return {"Authorization": registered["header"]}


def test_list_plugins_starts_disabled(client: TestClient, registered: dict) -> None:
    listed = client.get("/api/plugins", headers=_headers(registered))
    assert listed.status_code == 200
    by_kind = {p["kind"]: p for p in listed.json()}
    assert set(by_kind) == {"images", "voice", "tools"}
    assert all(not p["enabled"] for p in by_kind.values())
    assert all(not p["configured"] for p in by_kind.values())


def test_update_plugin_enables_and_stores_config(client: TestClient, registered: dict) -> None:
    headers = _headers(registered)
    updated = client.put(
        "/api/plugins/images",
        headers=headers,
        json={
            "enabled": True,
            "base_url": "http://localhost:9000",
            "model": "sdxl",
            "api_key": "sk-secret-value",
        },
    )
    assert updated.status_code == 200, updated.text
    body = updated.json()
    assert body["enabled"] is True
    assert body["configured"] is True
    assert body["base_url"] == "http://localhost:9000"
    assert body["auth_hint"] is not None
    assert "sk-secret-value" not in updated.text

    listed = client.get("/api/plugins", headers=headers)
    images = next(p for p in listed.json() if p["kind"] == "images")
    assert images["enabled"] is True
    assert images["base_url"] == "http://localhost:9000"


def test_update_plugin_unknown_kind_is_404(client: TestClient, registered: dict) -> None:
    response = client.put(
        "/api/plugins/nonsense", headers=_headers(registered), json={"enabled": True}
    )
    assert response.status_code == 404


def test_validate_reports_not_ok_when_disabled(client: TestClient, registered: dict) -> None:
    response = client.post("/api/plugins/voice/validate", headers=_headers(registered))
    assert response.status_code == 200
    assert response.json()["ok"] is False


def test_list_is_open_but_configuration_requires_admin(
    client: TestClient, registered: dict
) -> None:
    created = client.post(
        "/api/admin/users",
        headers=_headers(registered),
        json={
            "email": "second@homelab.local",
            "password": "correct-horse-battery",
            "name": "Regular user",
            "role": "user",
        },
    )
    assert created.status_code == 201, created.text

    login = client.post(
        "/api/auth/login",
        json={"email": "second@homelab.local", "password": "correct-horse-battery"},
    )
    assert login.status_code == 200, login.text
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    listed = client.get("/api/plugins", headers=headers)
    assert listed.status_code == 200

    response = client.put("/api/plugins/images", headers=headers, json={"enabled": True})
    assert response.status_code == 403
