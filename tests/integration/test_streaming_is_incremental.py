"""Tokens must reach the client as they are produced.

This is the one promise that cannot be tested through Starlette's ``TestClient``: it
runs the application to completion before returning a response, so *every* stream looks
buffered under it, whatever the application actually did. The assertion only means
something over a real socket, so this module runs the real application on a real port
against a fake backend that emits one token per second.

That slow backend is not an artificial stress case. A CPU-only host generating at one
or two tokens per second is a supported configuration (ADR-0008), and it is precisely
the case where any accumulation in the pipeline becomes visible as a UI that does
nothing for ten seconds and then dumps a paragraph.
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

TOKEN_INTERVAL_S = 1.0
TOKEN_COUNT = 9


@pytest.fixture
def live(settings: Settings) -> Iterator[tuple[FakeServer, FakeServer]]:
    """The real application and a fake Ollama, both on real localhost ports."""
    with run_fake(create_ollama()) as backend:
        configured = msgspec.structs.replace(
            settings,
            providers=ProviderSettings(ollama_hosts=(backend.base_url,), autodiscover=False),
        )
        with run_fake(create_app(configured), lifespan="on") as app:
            yield app, backend


def _authenticate(base_url: str) -> dict[str, str]:
    response = httpx.post(
        f"{base_url}/api/auth/register",
        json={
            "email": "live@homelab.local",
            "password": "correct-horse-battery",
            "name": "Live",
        },
        timeout=10,
    )
    response.raise_for_status()
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_first_token_arrives_while_the_backend_is_still_generating(live) -> None:
    app, _ = live
    headers = _authenticate(app.base_url)
    chat_id = httpx.post(
        f"{app.base_url}/api/chats", headers=headers, json={"title": "Slow"}, timeout=10
    ).json()["id"]

    started = time.perf_counter()
    first_delta_at: float | None = None
    last_delta_at: float | None = None
    deltas = 0

    with httpx.stream(
        "POST",
        f"{app.base_url}/api/chats/{chat_id}/completions",
        headers=headers,
        json={"content": "hi", "model_ref": "ollama-0:slow"},
        timeout=60,
    ) as response:
        assert response.status_code == 200
        for line in response.iter_lines():
            if line.startswith("data: ") and '"t"' in line:
                deltas += 1
                last_delta_at = time.perf_counter() - started
                if first_delta_at is None:
                    first_delta_at = last_delta_at

    assert deltas == TOKEN_COUNT
    assert first_delta_at is not None and last_delta_at is not None

    # The first token lands about one backend interval in, not after the whole
    # generation. A buffered pipeline would put both at roughly the same moment.
    assert first_delta_at < TOKEN_INTERVAL_S * 2.5
    assert last_delta_at - first_delta_at > TOKEN_INTERVAL_S * 4, (
        "deltas arrived in a burst: something in the pipeline accumulated them"
    )


def test_the_reply_is_persisted_after_a_live_stream(live) -> None:
    app, _ = live
    headers = _authenticate(app.base_url)
    chat_id = httpx.post(
        f"{app.base_url}/api/chats", headers=headers, json={"title": "Live"}, timeout=10
    ).json()["id"]

    with httpx.stream(
        "POST",
        f"{app.base_url}/api/chats/{chat_id}/completions",
        headers=headers,
        json={"content": "hi", "model_ref": "ollama-0:llama3.2"},
        timeout=60,
    ) as response:
        list(response.iter_lines())

    body = httpx.get(f"{app.base_url}/api/chats/{chat_id}", headers=headers, timeout=10).json()
    assert body["messages"][-1]["content"] == "Hello, world! This is velox-ui."
    assert body["messages"][-1]["status"] == "complete"
