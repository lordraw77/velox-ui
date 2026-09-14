"""Providers configured from the interface, and per-model parameters, over HTTP."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator

import msgspec
import pytest
from fastapi.testclient import TestClient
from tests.fakes.ollama import REQUESTS
from tests.fakes.ollama import create_app as create_ollama
from tests.fakes.openai_compat import create_app as create_openai
from tests.fakes.server import FakeServer, run_fake

from velox_ui.app import create_app
from velox_ui.settings import AuthSettings, ProviderSettings, Settings

KEY = "sk-test-key-1234"


@pytest.fixture(scope="module")
def ollama() -> Iterator[FakeServer]:
    with run_fake(create_ollama()) as server:
        yield server


@pytest.fixture(scope="module")
def keyed() -> Iterator[FakeServer]:
    with run_fake(create_openai(api_key=KEY)) as server:
        yield server


@pytest.fixture
def client(settings: Settings, ollama: FakeServer) -> Iterator[TestClient]:
    configured = msgspec.structs.replace(
        settings,
        auth=AuthSettings(
            open_registration=True, access_token_ttl_s=60, refresh_token_ttl_s=3600
        ),
        providers=ProviderSettings(ollama_hosts=(ollama.base_url,), autodiscover=False),
    )
    with TestClient(create_app(configured)) as test_client:
        yield test_client


@pytest.fixture
def admin(client: TestClient, registered: dict) -> dict[str, str]:
    return {"Authorization": registered["header"]}


@pytest.fixture
def member(client: TestClient, registered: dict) -> dict[str, str]:
    response = client.post(
        "/api/auth/register",
        json={
            "email": "guest@homelab.local",
            "password": "correct-horse-battery",
            "name": "Guest",
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["role"] == "user"
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_a_provider_added_in_the_interface_works_and_its_key_is_encrypted(
    client: TestClient, admin: dict, keyed: FakeServer, settings: Settings
) -> None:
    response = client.post(
        "/api/providers",
        headers=admin,
        json={"preset": "vllm", "base_url": f"{keyed.base_url}/v1", "api_key": KEY},
    )
    assert response.status_code == 201, response.text
    created = response.json()
    assert created["provider_id"] == "vllm"
    assert created["origin"] == "ui" and created["editable"] is True
    assert created["credential_hint"] and KEY not in created["credential_hint"]

    # The key is used: the keyed fake lists models only for the right credential.
    groups = {
        group["provider_id"]: group
        for group in client.get("/api/models", headers=admin).json()["providers"]
    }
    assert groups["vllm"]["models"][0]["key"] == "qwen2.5-7b-instruct"
    assert "num_ctx" not in groups["vllm"]["supported_params"]
    assert "num_ctx" in groups["ollama-0"]["supported_params"]

    listing = client.get("/api/providers", headers=admin).text
    assert KEY not in listing, "a credential must never appear in an API response"

    database = settings.data_dir / "test.db"
    with sqlite3.connect(database) as connection:
        rows = connection.execute("SELECT ciphertext, hint FROM secret").fetchall()
    assert len(rows) == 1
    assert KEY.encode() not in rows[0][0], "stored in clear"
    assert KEY not in rows[0][1]


def test_a_stored_provider_survives_a_restart(
    settings: Settings, ollama: FakeServer, keyed: FakeServer
) -> None:
    configured = msgspec.structs.replace(
        settings,
        providers=ProviderSettings(ollama_hosts=(ollama.base_url,), autodiscover=False),
    )
    with TestClient(create_app(configured)) as first:
        token = first.post(
            "/api/auth/register",
            json={
                "email": "owner@homelab.local",
                "password": "correct-horse-battery",
                "name": "Owner",
            },
        ).json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        assert (
            first.post(
                "/api/providers",
                headers=headers,
                json={
                    "preset": "custom",
                    "id": "lab",
                    "base_url": f"{keyed.base_url}/v1",
                    "api_key": KEY,
                },
            ).status_code
            == 201
        )

    with TestClient(create_app(configured)) as second:
        token = second.post(
            "/api/auth/login",
            json={"email": "owner@homelab.local", "password": "correct-horse-battery"},
        ).json()["access_token"]
        groups = second.get("/api/models", headers={"Authorization": f"Bearer {token}"}).json()[
            "providers"
        ]
        lab = next(group for group in groups if group["provider_id"] == "lab")
        assert lab["models"], "the decrypted key must still authenticate after a restart"
        assert lab["is_local"] is True, "a loopback custom endpoint counts as local"


def test_configured_providers_are_read_only(client: TestClient, admin: dict) -> None:
    response = client.patch("/api/providers/ollama-0", headers=admin, json={"name": "x"})
    assert response.status_code == 403
    assert "velox.toml" in response.json()["error"]["message"]
    assert client.delete("/api/providers/ollama-0", headers=admin).status_code == 403


def test_changing_providers_requires_an_administrator(
    client: TestClient, member: dict, keyed: FakeServer
) -> None:
    body = {"preset": "custom", "base_url": f"{keyed.base_url}/v1"}
    assert client.post("/api/providers", headers=member, json=body).status_code == 403
    assert (
        client.post(
            "/api/providers/probe", headers=member, json={"base_url": keyed.base_url}
        ).status_code
        == 403
    )
    # Reading stays open: the picker and the health badges need it.
    listing = client.get("/api/providers", headers=member).json()["providers"]
    assert listing and all(entry["credential_hint"] is None for entry in listing)


def test_validation_is_readable(client: TestClient, admin: dict) -> None:
    cases = [
        ({"preset": "nope"}, "Unknown preset"),
        ({"preset": "custom"}, "http"),
        ({"preset": "custom", "base_url": "ftp://x"}, "http"),
        ({"preset": "custom", "base_url": "http://user:pw@host/v1"}, "API key field"),
        ({"preset": "tabbyapi"}, "requires an API key"),
        ({"preset": "custom", "base_url": "http://h/v1", "id": "Bad Id"}, "lowercase"),
        ({"preset": "custom", "base_url": "http://h/v1", "id": "ollama-0"}, "already exists"),
    ]
    for body, message in cases:
        response = client.post("/api/providers", headers=admin, json=body)
        assert response.status_code in (409, 422), (body, response.text)
        assert message in response.json()["error"]["message"], (body, response.text)


def test_probe_identifies_what_answers(
    client: TestClient, admin: dict, ollama: FakeServer, keyed: FakeServer
) -> None:
    found = client.post(
        "/api/providers/probe", headers=admin, json={"base_url": ollama.base_url}
    ).json()
    assert (found["kind"], found["preset"], found["models"]) == ("ollama", "ollama", 2)

    locked = client.post(
        "/api/providers/probe", headers=admin, json={"base_url": keyed.base_url}
    ).json()
    assert locked["kind"] == "openai_compat" and locked["base_url"].endswith("/v1")
    assert "API key" in locked["detail"]

    unlocked = client.post(
        "/api/providers/probe", headers=admin, json={"base_url": keyed.base_url, "api_key": KEY}
    ).json()
    # The fake OpenAI-compatible server lists three models as of phase 8 (a "tools"
    # entry was added so its capability probe can be exercised via a real /models
    # listing); this asserts "at least the two original ones", not the exact count,
    # so it does not re-break every time the fake's fixture roster grows.
    assert unlocked["models"] >= 2 and unlocked["detail"] is None


def test_editing_and_removing_a_stored_provider(
    client: TestClient, admin: dict, keyed: FakeServer
) -> None:
    client.post(
        "/api/providers",
        headers=admin,
        json={"preset": "custom", "id": "lab", "base_url": f"{keyed.base_url}/v1"},
    )
    groups = client.get("/api/models", headers=admin).json()["providers"]
    assert next(g for g in groups if g["provider_id"] == "lab")["models"] == [], "no key yet"

    patched = client.patch("/api/providers/lab", headers=admin, json={"api_key": KEY})
    assert patched.status_code == 200 and patched.json()["credential_hint"]
    groups = client.get("/api/models", headers=admin).json()["providers"]
    assert next(g for g in groups if g["provider_id"] == "lab")["models"], "new key in effect"

    assert client.delete("/api/providers/lab", headers=admin).status_code == 204
    ids = [
        entry["provider_id"]
        for entry in client.get("/api/providers", headers=admin).json()["providers"]
    ]
    assert "lab" not in ids


def test_saved_parameters_apply_to_every_turn(client: TestClient, admin: dict) -> None:
    ref = "ollama-0:llama3.2"
    saved = client.put(
        f"/api/model-params/{ref}",
        headers=admin,
        json={"num_ctx": 8192, "temperature": 0.2, "keep_alive": "10m"},
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["params"] == {"num_ctx": 8192, "temperature": 0.2, "keep_alive": "10m"}

    chat_id = client.post("/api/chats", headers=admin, json={"title": "t"}).json()["id"]
    REQUESTS.clear()
    with client.stream(
        "POST",
        f"/api/chats/{chat_id}/completions",
        headers=admin,
        json={"content": "hi", "model_ref": ref, "params": {"temperature": 0.9}},
    ) as response:
        assert response.status_code == 200
        list(response.iter_lines())

    body = next(body for path, body in REQUESTS if path == "/api/chat")
    assert body["options"]["num_ctx"] == 8192, "saved value applied"
    assert body["options"]["temperature"] == 0.9, "the per-turn value wins"
    assert body["keep_alive"] == "10m"

    assert client.delete(f"/api/model-params/{ref}", headers=admin).status_code == 204
    assert client.get(f"/api/model-params/{ref}", headers=admin).json()["params"] == {}


def test_parameters_a_backend_does_not_take_are_rejected(
    client: TestClient, admin: dict, keyed: FakeServer
) -> None:
    client.post(
        "/api/providers",
        headers=admin,
        json={"preset": "vllm", "base_url": f"{keyed.base_url}/v1", "api_key": KEY},
    )
    response = client.put(
        "/api/model-params/vllm:qwen2.5-7b-instruct", headers=admin, json={"num_gpu": 20}
    )
    assert response.status_code == 422
    assert "num_gpu" in response.json()["error"]["message"]

    out_of_range = client.put(
        "/api/model-params/ollama-0:llama3.2", headers=admin, json={"temperature": -1}
    )
    assert out_of_range.status_code == 422
