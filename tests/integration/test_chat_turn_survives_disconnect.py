"""A turn belongs to the conversation, not to the connection reading it.

The reader is disposable here: these tests close the response mid-answer — what the
interface does when another chat is opened, or a page reloaded — and assert that the
model kept going, that reattaching replays the whole turn, and that stopping is now
something a caller asks for rather than a side effect of a dropped socket.

Over real sockets, both the application and the backend: Starlette's ``TestClient``
runs the application to completion before returning a response, so "closed it halfway"
would not be true there (``tests/fakes/server.py``). The backend is the ollama fake's
``slow`` model, one token per second, so halfway is a real moment rather than a race.
"""

from __future__ import annotations

import time
from collections.abc import Iterator

import httpx
import msgspec
import pytest
from tests.fakes.ollama import create_app as create_ollama
from tests.fakes.server import FakeServer, run_fake

from velox_ui.app import create_app
from velox_ui.settings import ProviderSettings, Settings

_FULL_REPLY = "Hello, world! This is velox-ui."
_MODEL_REF = "ollama-0:slow"


@pytest.fixture
def live(settings: Settings) -> Iterator[FakeServer]:
    """The real application and a fake Ollama, both on real localhost ports."""
    with run_fake(create_ollama()) as backend:
        configured = msgspec.structs.replace(
            settings,
            providers=ProviderSettings(ollama_hosts=(backend.base_url,), autodiscover=False),
        )
        with run_fake(create_app(configured), lifespan="on") as app:
            yield app


@pytest.fixture
def headers(live: FakeServer) -> dict[str, str]:
    response = httpx.post(
        f"{live.base_url}/api/auth/register",
        json={"email": "turns@homelab.local", "password": "correct-horse-battery", "name": "T"},
        timeout=10,
    )
    response.raise_for_status()
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _new_chat(base_url: str, headers: dict[str, str]) -> str:
    created = httpx.post(
        f"{base_url}/api/chats", headers=headers, json={"title": "Slow"}, timeout=10
    )
    assert created.status_code == 201, created.text
    chat_id: str = created.json()["id"]
    return chat_id


def _start_and_leave(base_url: str, headers: dict[str, str], chat_id: str) -> None:
    """Start a turn, read until the model is really producing, then walk away."""
    with httpx.stream(
        "POST",
        f"{base_url}/api/chats/{chat_id}/completions",
        headers=headers,
        json={"content": "hi", "model_ref": _MODEL_REF},
        timeout=30,
    ) as response:
        assert response.status_code == 200
        for line in response.iter_lines():
            if line.startswith("event: delta"):
                return  # closes the response mid-answer, as the interface does


def _parse_sse(lines: Iterator[str]) -> list[tuple[str, str]]:
    events: list[tuple[str, str]] = []
    name: str | None = None
    for line in lines:
        if line.startswith("event: "):
            name = line[len("event: ") :]
        elif line.startswith("data: ") and name:
            events.append((name, line[len("data: ") :]))
    return events


def _assistant(base_url: str, headers: dict[str, str], chat_id: str) -> dict:
    chat = httpx.get(f"{base_url}/api/chats/{chat_id}", headers=headers, timeout=10)
    assert chat.status_code == 200, chat.text
    replies = [m for m in chat.json()["messages"] if m["role"] == "assistant"]
    return replies[-1] if replies else {}


def _await_reply(
    base_url: str, headers: dict[str, str], chat_id: str, *, timeout_s: float
) -> dict:
    deadline = time.monotonic() + timeout_s
    message: dict = {}
    while time.monotonic() < deadline:
        message = _assistant(base_url, headers, chat_id)
        if message.get("status") not in (None, "streaming"):
            return message
        time.sleep(0.2)
    return message


def test_a_reader_leaving_does_not_stop_the_turn(
    live: FakeServer, headers: dict[str, str]
) -> None:
    chat_id = _new_chat(live.base_url, headers)

    started = time.monotonic()
    _start_and_leave(live.base_url, headers, chat_id)
    left_after = time.monotonic() - started
    message = _await_reply(live.base_url, headers, chat_id, timeout_s=30.0)

    # Left after the first token or two of a nine-second answer, which arrived whole.
    assert left_after < 5.0
    assert message["content"] == _FULL_REPLY
    assert message["status"] == "complete"


def test_coming_back_replays_the_turn_from_its_start(
    live: FakeServer, headers: dict[str, str]
) -> None:
    chat_id = _new_chat(live.base_url, headers)
    _start_and_leave(live.base_url, headers, chat_id)

    with httpx.stream(
        "GET", f"{live.base_url}/api/chats/{chat_id}/stream", headers=headers, timeout=30
    ) as response:
        assert response.status_code == 200
        events = _parse_sse(response.iter_lines())

    names = [name for name, _ in events]
    # Replayed from the beginning: the frame carrying the message ids is there, so a
    # client that missed the first half can still rebuild the whole turn.
    assert names[0] == "start"
    assert names[-1] == "done"
    text = "".join(msgspec.json.decode(data)["t"] for name, data in events if name == "delta")
    assert text == _FULL_REPLY


def test_stopping_is_asked_for_explicitly(live: FakeServer, headers: dict[str, str]) -> None:
    chat_id = _new_chat(live.base_url, headers)
    _start_and_leave(live.base_url, headers, chat_id)

    stopped = httpx.post(
        f"{live.base_url}/api/chats/{chat_id}/stop", headers=headers, timeout=10
    )
    assert stopped.status_code == 200, stopped.text
    assert stopped.json() == {"stopped": True}

    message = _await_reply(live.base_url, headers, chat_id, timeout_s=10.0)
    assert message["status"] == "stopped"
    # What the model had produced is kept (ADR-0005), and it is not the whole answer.
    assert message["content"] != _FULL_REPLY

    # Nothing left to stop.
    again = httpx.post(f"{live.base_url}/api/chats/{chat_id}/stop", headers=headers, timeout=10)
    assert again.json() == {"stopped": False}


def test_a_second_turn_in_the_same_conversation_is_refused(
    live: FakeServer, headers: dict[str, str]
) -> None:
    chat_id = _new_chat(live.base_url, headers)
    _start_and_leave(live.base_url, headers, chat_id)

    second = httpx.post(
        f"{live.base_url}/api/chats/{chat_id}/completions",
        headers=headers,
        json={"content": "again", "model_ref": _MODEL_REF},
        timeout=30,
    )

    assert second.status_code == 409, second.text
    assert "already has a turn running" in second.text
    httpx.post(f"{live.base_url}/api/chats/{chat_id}/stop", headers=headers, timeout=10)


def test_reading_a_conversation_with_no_turn_is_a_404(
    live: FakeServer, headers: dict[str, str]
) -> None:
    chat_id = _new_chat(live.base_url, headers)
    missing = "01ARZ3NDEKTSV4RRFFQ69G5FAV"

    def status(method: str, path: str) -> int:
        return httpx.request(
            method, f"{live.base_url}{path}", headers=headers, timeout=10
        ).status_code

    assert status("GET", f"/api/chats/{chat_id}/stream") == 404
    assert status("GET", f"/api/chats/{missing}/stream") == 404
    assert status("POST", f"/api/chats/{missing}/stop") == 404
