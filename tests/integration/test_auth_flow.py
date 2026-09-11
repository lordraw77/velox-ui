"""Registration, login, refresh rotation and API keys."""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_first_account_becomes_administrator(client: TestClient) -> None:
    response = client.post(
        "/api/auth/register",
        json={
            "email": "first@homelab.local",
            "password": "correct-horse-battery",
            "name": "First",
        },
    )
    assert response.status_code == 200
    assert response.json()["role"] == "admin"


def test_registration_is_closed_after_the_first_account(
    client: TestClient, registered: dict
) -> None:
    response = client.post(
        "/api/auth/register",
        json={
            "email": "second@homelab.local",
            "password": "correct-horse-battery",
            "name": "Second",
        },
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"


def test_internal_domains_are_accepted(client: TestClient) -> None:
    # A strict email validator would reject .local, which is where self-hosted
    # instances actually live.
    response = client.post(
        "/api/auth/register",
        json={"email": "me@nas.local", "password": "correct-horse-battery", "name": "Me"},
    )
    assert response.status_code == 200


def test_login_and_profile(client: TestClient, registered: dict) -> None:
    response = client.post(
        "/api/auth/login",
        json={"email": "owner@homelab.local", "password": "correct-horse-battery"},
    )
    assert response.status_code == 200
    token = response.json()["access_token"]
    profile = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert profile.json()["email"] == "owner@homelab.local"


def test_wrong_password_and_unknown_account_are_indistinguishable(
    client: TestClient, registered: dict
) -> None:
    wrong = client.post(
        "/api/auth/login", json={"email": "owner@homelab.local", "password": "nope"}
    )
    unknown = client.post(
        "/api/auth/login", json={"email": "ghost@homelab.local", "password": "nope"}
    )
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json()["error"] == unknown.json()["error"] | {
        "request_id": wrong.json()["error"]["request_id"]
    }


def test_refresh_rotates_the_token(client: TestClient, registered: dict) -> None:
    first = client.cookies.get("velox_refresh")
    response = client.post("/api/auth/refresh")
    assert response.status_code == 200
    assert client.cookies.get("velox_refresh") != first, "refresh tokens must rotate"


def test_reusing_a_rotated_token_kills_the_session(
    client: TestClient, registered: dict
) -> None:
    stolen = client.cookies.get("velox_refresh")
    assert client.post("/api/auth/refresh").status_code == 200

    client.cookies.set("velox_refresh", stolen)
    replayed = client.post("/api/auth/refresh")
    assert replayed.status_code == 401
    assert "reused" in replayed.json()["error"]["message"]

    # The whole family is revoked, so the attacker's fresh token is dead too.
    assert client.post("/api/auth/refresh").status_code == 401


def test_logout_revokes_the_session(client: TestClient, registered: dict) -> None:
    assert client.post("/api/auth/logout").status_code == 200
    assert client.post("/api/auth/refresh").status_code == 401


def test_logout_without_a_session_still_succeeds(client: TestClient) -> None:
    assert client.post("/api/auth/logout").status_code == 200


def test_unauthenticated_access_is_refused(client: TestClient) -> None:
    response = client.get("/api/auth/me")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


def test_api_key_authenticates_and_is_shown_once(client: TestClient, registered: dict) -> None:
    headers = {"Authorization": registered["header"]}
    created = client.post("/api/me/api-keys", headers=headers, json={"name": "cli"})
    assert created.status_code == 201
    plaintext = created.json()["key"]

    listed = client.get("/api/me/api-keys", headers=headers).json()
    assert "key" not in listed[0], "the plaintext must never be returned again"
    assert plaintext not in listed[0]["masked"]

    profile = client.get("/api/auth/me", headers={"Authorization": f"Bearer {plaintext}"})
    assert profile.json()["email"] == "owner@homelab.local"


def test_deleted_api_key_stops_working(client: TestClient, registered: dict) -> None:
    headers = {"Authorization": registered["header"]}
    created = client.post("/api/me/api-keys", headers=headers, json={"name": "cli"}).json()
    assert (
        client.delete(f"/api/me/api-keys/{created['id']}", headers=headers).status_code == 204
    )
    refused = client.get("/api/auth/me", headers={"Authorization": f"Bearer {created['key']}"})
    assert refused.status_code == 401


def test_deleting_someone_elses_key_is_a_404(client: TestClient, registered: dict) -> None:
    headers = {"Authorization": registered["header"]}
    assert client.delete("/api/me/api-keys/01NOPE", headers=headers).status_code == 404
