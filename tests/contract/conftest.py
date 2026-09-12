"""Fixtures for provider contract tests."""

from __future__ import annotations

from collections.abc import Iterator

import httpx
import pytest

from tests.fakes.llamacpp import create_app as create_llamacpp
from tests.fakes.ollama import create_app as create_ollama
from tests.fakes.server import FakeServer, run_fake


@pytest.fixture(scope="session")
def ollama_server() -> Iterator[FakeServer]:
    """A fake Ollama on a real localhost port, shared by the session."""
    with run_fake(create_ollama()) as server:
        yield server


@pytest.fixture(scope="session")
def llamacpp_server() -> Iterator[FakeServer]:
    """A fake llama-server on a real localhost port, shared by the session."""
    with run_fake(create_llamacpp()) as server:
        yield server


@pytest.fixture
async def http_client() -> httpx.AsyncClient:
    """The shared client, built exactly as the application builds it."""
    from velox_ui.providers.httpclient import build_http_client

    client = build_http_client()
    try:
        yield client
    finally:
        await client.aclose()
