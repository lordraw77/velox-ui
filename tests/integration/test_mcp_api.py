"""``/api/mcp/servers`` CRUD, connect, tool listing, ``/api/tools`` and secret storage.

The stdio server is a real subprocess (``tests/fakes/mcp_stdio_server.py``), so
``connect`` genuinely spawns a process, speaks JSON-RPC and caches a real tool list —
nothing here is a stub of the connect operation itself.
"""

from __future__ import annotations

import sys

from fastapi.testclient import TestClient
from sqlalchemy import select

from velox_ui.db.models import Secret


def _headers(registered: dict) -> dict[str, str]:
    return {"Authorization": registered["header"]}


def _stdio_config() -> dict:
    return {"command": sys.executable, "args": ["-m", "tests.fakes.mcp_stdio_server"]}


def test_create_list_get_update_delete_server(client: TestClient, registered: dict) -> None:
    headers = _headers(registered)

    created = client.post(
        "/api/mcp/servers",
        headers=headers,
        json={"name": "weather", "transport": "stdio", "config": _stdio_config()},
    )
    assert created.status_code == 201, created.text
    body = created.json()
    server_id = body["id"]
    assert body["transport"] == "stdio"
    assert body["enabled"] is True
    assert body["approval"] == "always"
    assert body["tool_count"] == 0

    listed = client.get("/api/mcp/servers", headers=headers)
    assert listed.status_code == 200
    assert any(s["id"] == server_id for s in listed.json())

    fetched = client.get(f"/api/mcp/servers/{server_id}", headers=headers)
    assert fetched.status_code == 200
    assert fetched.json()["name"] == "weather"

    patched = client.patch(
        f"/api/mcp/servers/{server_id}",
        headers=headers,
        json={"name": "weather-2", "approval": "never"},
    )
    assert patched.status_code == 200
    assert patched.json()["name"] == "weather-2"
    assert patched.json()["approval"] == "never"

    deleted = client.delete(f"/api/mcp/servers/{server_id}", headers=headers)
    assert deleted.status_code == 204
    assert client.get(f"/api/mcp/servers/{server_id}", headers=headers).status_code == 404


def test_connect_caches_the_real_tool_list(client: TestClient, registered: dict) -> None:
    headers = _headers(registered)
    created = client.post(
        "/api/mcp/servers",
        headers=headers,
        json={"name": "weather", "transport": "stdio", "config": _stdio_config()},
    )
    server_id = created.json()["id"]

    connected = client.post(f"/api/mcp/servers/{server_id}/connect", headers=headers)
    assert connected.status_code == 200, connected.text
    tools = connected.json()
    assert [t["name"] for t in tools] == ["get_weather"]

    cached = client.get(f"/api/mcp/servers/{server_id}/tools", headers=headers)
    assert cached.status_code == 200
    assert [t["name"] for t in cached.json()] == ["get_weather"]

    listed = client.get("/api/mcp/servers", headers=headers)
    assert next(s for s in listed.json() if s["id"] == server_id)["tool_count"] == 1


def test_available_tools_endpoint_lists_connected_servers_tools(
    client: TestClient, registered: dict
) -> None:
    headers = _headers(registered)
    created = client.post(
        "/api/mcp/servers",
        headers=headers,
        json={"name": "weather", "transport": "stdio", "config": _stdio_config()},
    )
    server_id = created.json()["id"]
    client.post(f"/api/mcp/servers/{server_id}/connect", headers=headers)

    tools = client.get("/api/tools", headers=headers)
    assert tools.status_code == 200
    body = tools.json()
    assert len(body) == 1
    assert body[0]["name"] == "get_weather"
    assert body[0]["server_id"] == server_id


def test_disabled_server_is_excluded_from_available_tools(
    client: TestClient, registered: dict
) -> None:
    headers = _headers(registered)
    created = client.post(
        "/api/mcp/servers",
        headers=headers,
        json={"name": "weather", "transport": "stdio", "config": _stdio_config()},
    )
    server_id = created.json()["id"]
    client.post(f"/api/mcp/servers/{server_id}/connect", headers=headers)
    client.patch(f"/api/mcp/servers/{server_id}", headers=headers, json={"enabled": False})

    tools = client.get("/api/tools", headers=headers)
    assert tools.json() == []


def test_approve_unknown_call_id_returns_ok_false(client: TestClient, registered: dict) -> None:
    headers = _headers(registered)
    response = client.post(
        "/api/tools/approve",
        headers=headers,
        json={"call_id": "does-not-exist", "approved": True},
    )
    assert response.status_code == 200
    assert response.json() == {"ok": False}


def test_auth_token_is_encrypted_at_rest_not_stored_in_config(
    client: TestClient, registered: dict, settings
) -> None:
    headers = _headers(registered)
    created = client.post(
        "/api/mcp/servers",
        headers=headers,
        json={
            "name": "remote",
            "transport": "http_sse",
            "config": {"url": "http://example.invalid/mcp"},
            "auth_token": "super-secret-bearer-token",
        },
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["auth_hint"] == "set"
    # The config the API returns carries only what was given: no token, no field
    # that could hold one in the clear.
    assert body["config"] == {"url": "http://example.invalid/mcp"}
    assert "super-secret-bearer-token" not in str(body)

    from velox_ui.db.engine import Database

    async def _read_secret() -> tuple[bytes, bytes, str] | None:
        database = Database(settings.db.url)
        try:
            async with database.session() as session:
                row = (
                    await session.execute(select(Secret).where(Secret.ref == body["id"]))
                ).scalar_one_or_none()
                if row is None:
                    return None
                return row.nonce, row.ciphertext, row.ref
        finally:
            await database.dispose()

    import asyncio

    found = asyncio.run(_read_secret())
    assert found is not None
    nonce, ciphertext, ref = found
    assert b"super-secret-bearer-token" not in ciphertext

    from velox_ui.security.crypto import SecretBox

    box = SecretBox(settings.secret_key)
    plaintext = box.decrypt(nonce, ciphertext, ref=ref)
    assert plaintext == "super-secret-bearer-token"
