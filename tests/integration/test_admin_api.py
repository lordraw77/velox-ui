"""Admin user management and the registration gate, over HTTP."""

from __future__ import annotations

from fastapi.testclient import TestClient


def _headers(registered: dict) -> dict[str, str]:
    return {"Authorization": registered["header"]}


def test_registration_is_closed_after_the_first_account(
    client: TestClient, registered: dict
) -> None:
    # `registered` (the fixture) already created the first, admin account. Settings
    # default `open_registration=False`, so a second self-service sign-up is refused.
    response = client.post(
        "/api/auth/register",
        json={
            "email": "guest@homelab.local",
            "password": "correct-horse-battery",
            "name": "Guest",
        },
    )
    assert response.status_code == 403


def test_admin_can_create_a_user_even_with_registration_closed(
    client: TestClient, registered: dict
) -> None:
    headers = _headers(registered)
    created = client.post(
        "/api/admin/users",
        headers=headers,
        json={
            "email": "member@homelab.local",
            "password": "correct-horse-battery",
            "name": "Member",
            "role": "user",
        },
    )
    assert created.status_code == 201, created.text
    assert created.json()["role"] == "user"
    assert created.json()["status"] == "active"

    # The new account can sign in even though self-service registration is closed.
    login = client.post(
        "/api/auth/login",
        json={"email": "member@homelab.local", "password": "correct-horse-battery"},
    )
    assert login.status_code == 200


def test_list_role_status_and_delete(client: TestClient, registered: dict) -> None:
    headers = _headers(registered)
    created = client.post(
        "/api/admin/users",
        headers=headers,
        json={
            "email": "member2@homelab.local",
            "password": "correct-horse-battery",
            "name": "M2",
        },
    )
    member_id = created.json()["id"]

    listed = client.get("/api/admin/users", headers=headers)
    assert listed.status_code == 200
    assert member_id in [u["id"] for u in listed.json()["items"]]

    promoted = client.patch(
        f"/api/admin/users/{member_id}/role", headers=headers, json={"role": "admin"}
    )
    assert promoted.status_code == 200
    assert promoted.json()["role"] == "admin"

    disabled = client.patch(
        f"/api/admin/users/{member_id}/status", headers=headers, json={"status": "disabled"}
    )
    assert disabled.status_code == 200
    assert disabled.json()["status"] == "disabled"

    login_disabled = client.post(
        "/api/auth/login",
        json={"email": "member2@homelab.local", "password": "correct-horse-battery"},
    )
    assert login_disabled.status_code == 401

    deleted = client.delete(f"/api/admin/users/{member_id}", headers=headers)
    assert deleted.status_code == 204


def test_cannot_demote_or_disable_or_delete_self(client: TestClient, registered: dict) -> None:
    headers = _headers(registered)
    self_id = registered["user_id"]

    demote = client.patch(
        f"/api/admin/users/{self_id}/role", headers=headers, json={"role": "user"}
    )
    assert demote.status_code == 403

    disable = client.patch(
        f"/api/admin/users/{self_id}/status", headers=headers, json={"status": "disabled"}
    )
    assert disable.status_code == 403

    delete = client.delete(f"/api/admin/users/{self_id}", headers=headers)
    assert delete.status_code == 403


def test_non_admin_is_forbidden(client: TestClient, registered: dict) -> None:
    headers = _headers(registered)
    member = client.post(
        "/api/admin/users",
        headers=headers,
        json={
            "email": "plain@homelab.local",
            "password": "correct-horse-battery",
            "name": "Plain",
        },
    )
    login = client.post(
        "/api/auth/login",
        json={"email": "plain@homelab.local", "password": "correct-horse-battery"},
    )
    plain_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    response = client.get("/api/admin/users", headers=plain_headers)
    assert response.status_code == 403
    assert member.status_code == 201
