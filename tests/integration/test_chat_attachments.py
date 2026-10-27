"""Image attachments, end to end: upload, send to a vision model, replay on later turns.

The fake Ollama records every request body, so these assert what actually reached the
backend -- the images in the wire format Ollama expects -- rather than what the service
believes it sent.
"""

from __future__ import annotations

import base64
import struct
import zlib
from collections.abc import Iterator

import msgspec
import pytest
from fastapi.testclient import TestClient
from tests.fakes import ollama as fake_ollama
from tests.fakes.ollama import create_app as create_ollama
from tests.fakes.server import FakeServer, run_fake

from velox_ui.app import create_app
from velox_ui.settings import ProviderSettings, Settings


def _png(width: int = 2, height: int = 2) -> bytes:
    """A real, minimal PNG, so the test does not depend on a fixture file."""

    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    raw = b"".join(b"\x00" + b"\xff\x00\x00" * width for _ in range(height))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


@pytest.fixture(scope="module")
def ollama() -> Iterator[FakeServer]:
    with run_fake(create_ollama()) as server:
        yield server


@pytest.fixture
def client(settings: Settings, ollama: FakeServer) -> Iterator[TestClient]:
    configured = msgspec.structs.replace(
        settings,
        providers=ProviderSettings(ollama_hosts=(ollama.base_url,), autodiscover=False),
    )
    fake_ollama.REQUESTS.clear()
    with TestClient(create_app(configured)) as test_client:
        yield test_client


def _upload(client: TestClient, headers: dict, data: bytes, name: str = "pic.png"):
    return client.post("/api/attachments", headers=headers, files={"upload": (name, data)})


def _send(client: TestClient, headers: dict, chat_id: str, model: str, **body):
    payload = {"content": "what is this?", "model_ref": f"ollama-0:{model}", **body}
    with client.stream(
        "POST", f"/api/chats/{chat_id}/completions", headers=headers, json=payload
    ) as response:
        status = response.status_code
        response.read()
        return status, response


def _chat_requests() -> list[dict]:
    return [body for path, body in fake_ollama.REQUESTS if path == "/api/chat"]


def test_upload_accepts_an_image_and_serves_it_back(
    client: TestClient, registered: dict
) -> None:
    headers = {"Authorization": registered["header"]}
    data = _png()
    response = _upload(client, headers, data)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["content_type"] == "image/png"
    assert body["size_bytes"] == len(data)

    served = client.get(f"/api/files/{body['id']}/content", headers=headers)
    assert served.status_code == 200
    assert served.content == data


def test_upload_is_deduplicated_per_user(client: TestClient, registered: dict) -> None:
    headers = {"Authorization": registered["header"]}
    first = _upload(client, headers, _png()).json()
    second = _upload(client, headers, _png()).json()
    assert first["id"] == second["id"]


@pytest.mark.parametrize(
    ("data", "name"),
    [
        (b"", "empty.png"),
        (b"<svg xmlns='http://www.w3.org/2000/svg'><script>1</script></svg>", "x.svg"),
        (b"%PDF-1.4 not an image", "doc.png"),
        (b"RIFF\x00\x00\x00\x00WAVEfmt ", "sound.webp"),
    ],
)
def test_upload_rejects_anything_that_is_not_an_image(
    client: TestClient, registered: dict, data: bytes, name: str
) -> None:
    headers = {"Authorization": registered["header"]}
    assert _upload(client, headers, data, name).status_code == 422


def test_image_reaches_a_vision_model_as_base64(client: TestClient, registered: dict) -> None:
    headers = {"Authorization": registered["header"]}
    data = _png()
    attachment = _upload(client, headers, data).json()
    chat_id = client.post("/api/chats", headers=headers, json={"title": "t"}).json()["id"]

    status, _ = _send(client, headers, chat_id, "llava", attachment_ids=[attachment["id"]])
    assert status == 200

    user = _chat_requests()[-1]["messages"][-1]
    assert user["role"] == "user"
    assert user["content"] == "what is this?"
    assert user["images"] == [base64.b64encode(data).decode()]


def test_attachment_is_recorded_on_the_message_and_replayed_later(
    client: TestClient, registered: dict
) -> None:
    headers = {"Authorization": registered["header"]}
    data = _png()
    attachment = _upload(client, headers, data).json()
    chat_id = client.post("/api/chats", headers=headers, json={"title": "t"}).json()["id"]

    _send(client, headers, chat_id, "llava", attachment_ids=[attachment["id"]])
    _send(client, headers, chat_id, "llava", content="and the colour?")

    history = client.get(f"/api/chats/{chat_id}", headers=headers).json()
    users = [m for m in history["messages"] if m["role"] == "user"]
    assert users[0]["meta"]["attachments"][0]["id"] == attachment["id"]
    assert users[1]["meta"] is None

    # The second request carries the picture again: a model has no memory of its own.
    earlier = _chat_requests()[-1]["messages"][0]
    assert earlier["images"] == [base64.b64encode(data).decode()]


def test_a_model_without_vision_refuses_images(client: TestClient, registered: dict) -> None:
    headers = {"Authorization": registered["header"]}
    attachment = _upload(client, headers, _png()).json()
    chat_id = client.post("/api/chats", headers=headers, json={"title": "t"}).json()["id"]

    status, response = _send(
        client, headers, chat_id, "llama3.2", attachment_ids=[attachment["id"]]
    )
    assert status == 422
    assert response.json()["error"]["code"] == "unsupported_capability"
    assert _chat_requests() == []


def test_an_unknown_attachment_is_refused(client: TestClient, registered: dict) -> None:
    headers = {"Authorization": registered["header"]}
    chat_id = client.post("/api/chats", headers=headers, json={"title": "t"}).json()["id"]
    status, _ = _send(client, headers, chat_id, "llava", attachment_ids=["0" * 26])
    assert status == 422


def test_another_users_attachment_is_refused(client: TestClient, registered: dict) -> None:
    headers = {"Authorization": registered["header"]}
    attachment = _upload(client, headers, _png()).json()
    client.post(
        "/api/admin/users",
        headers=headers,
        json={
            "email": "member@homelab.local",
            "password": "correct-horse-battery",
            "name": "Member",
            "role": "user",
        },
    ).raise_for_status()
    login = client.post(
        "/api/auth/login",
        json={"email": "member@homelab.local", "password": "correct-horse-battery"},
    )
    other = {"Authorization": f"Bearer {login.json()['access_token']}"}
    chat_id = client.post("/api/chats", headers=other, json={"title": "t"}).json()["id"]
    status, _ = _send(client, other, chat_id, "llava", attachment_ids=[attachment["id"]])
    assert status == 422
