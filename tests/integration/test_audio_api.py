"""``POST /api/audio/transcribe`` and ``POST /api/audio/speech`` against a fake backend."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from tests.fakes.media import create_app as create_media
from tests.fakes.server import FakeServer, run_fake


def _headers(registered: dict) -> dict[str, str]:
    return {"Authorization": registered["header"]}


@pytest.fixture(scope="module")
def media_server() -> Iterator[FakeServer]:
    """A fake STT/TTS backend."""
    with run_fake(create_media()) as server:
        yield server


def test_transcribe_is_disabled_by_default(client: TestClient, registered: dict) -> None:
    response = client.post(
        "/api/audio/transcribe",
        headers=_headers(registered),
        files={"audio": ("clip.webm", b"hello", "audio/webm")},
    )
    assert response.status_code == 501


def test_speech_is_disabled_by_default(client: TestClient, registered: dict) -> None:
    response = client.post(
        "/api/audio/speech", headers=_headers(registered), json={"text": "hello"}
    )
    assert response.status_code == 501


def test_transcribe_and_speak_once_enabled(
    client: TestClient, registered: dict, media_server: FakeServer
) -> None:
    headers = _headers(registered)
    enabled = client.put(
        "/api/plugins/voice",
        headers=headers,
        json={"enabled": True, "base_url": media_server.base_url},
    )
    assert enabled.status_code == 200, enabled.text

    transcribed = client.post(
        "/api/audio/transcribe",
        headers=headers,
        files={"audio": ("clip.webm", b"hello", "audio/webm")},
    )
    assert transcribed.status_code == 200, transcribed.text
    assert transcribed.json()["text"] == "transcribed: hello"

    spoken = client.post("/api/audio/speech", headers=headers, json={"text": "hello there"})
    assert spoken.status_code == 200
    assert spoken.content == b"fake-mp3-bytes"
    assert spoken.headers["content-type"].startswith("audio/")
