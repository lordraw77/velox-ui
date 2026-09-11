"""Every error the API returns has the same shape."""

from __future__ import annotations

from fastapi.testclient import TestClient

REQUIRED_KEYS = {"code", "message", "retryable", "request_id"}


def test_not_found_envelope(client: TestClient) -> None:
    body = client.get("/api/definitely-not-a-route").json()
    assert set(body["error"]) >= REQUIRED_KEYS
    assert body["error"]["code"] == "not_found"


def test_validation_envelope_names_the_fields(client: TestClient) -> None:
    response = client.post(
        "/api/auth/register", json={"email": "bad", "password": "x", "name": ""}
    )
    body = response.json()["error"]
    assert response.status_code == 422
    assert body["code"] == "invalid_request"
    assert {item["field"] for item in body["fields"]} == {"email", "password", "name"}


def test_validation_errors_do_not_echo_the_submitted_password(client: TestClient) -> None:
    # Pydantic's raw error list carries the offending input. Echoing it would put the
    # user's password into the response body and into any client-side error log.
    response = client.post(
        "/api/auth/register",
        json={"email": "bad", "password": "hunter2-secret", "name": "X"},
    )
    assert "hunter2-secret" not in response.text


def test_unauthorized_envelope(client: TestClient) -> None:
    body = client.get("/api/auth/me").json()["error"]
    assert body["code"] == "unauthorized"
    assert body["retryable"] is False
