"""``POST /api/mcp/servers/import``: Claude Code config import over HTTP."""

from __future__ import annotations

from fastapi.testclient import TestClient


def _headers(registered: dict) -> dict[str, str]:
    return {"Authorization": registered["header"]}


def test_import_creates_stdio_and_http_servers(client: TestClient, registered: dict) -> None:
    # The import route only parses and stores config; it never launches the
    # process, so "npx" here need not exist on the test runner.
    raw = """
    {
      "mcpServers": {
        "discogs": {
          "type": null,
          "command": "npx",
          "args": ["-y", "discogs-mcp-server"],
          "env": {"DISCOGS_PERSONAL_ACCESS_TOKEN": "secret"},
          "cwd": "/srv/app",
          "url": null,
          "headers": {}
        },
        "remote": {"url": "https://example.invalid/mcp", "headers": {"X-Api-Key": "k"}}
      }
    }
    """

    response = client.post(
        "/api/mcp/servers/import", headers=_headers(registered), json={"raw": raw}
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert {s["name"] for s in body["servers"]} == {"discogs", "remote"}
    assert body["dropped_cwd"] == ["discogs"]

    discogs = next(s for s in body["servers"] if s["name"] == "discogs")
    assert discogs["transport"] == "stdio"
    assert discogs["config"]["env"]["DISCOGS_PERSONAL_ACCESS_TOKEN"] == "secret"
    assert "cwd" not in discogs["config"]

    remote = next(s for s in body["servers"] if s["name"] == "remote")
    assert remote["transport"] == "http_sse"
    assert remote["config"] == {
        "url": "https://example.invalid/mcp",
        "headers": {"X-Api-Key": "k"},
    }

    listed = client.get("/api/mcp/servers", headers=_headers(registered))
    assert {s["name"] for s in listed.json()} >= {"discogs", "remote"}


def test_import_rejects_unrecognised_shape(client: TestClient, registered: dict) -> None:
    response = client.post(
        "/api/mcp/servers/import", headers=_headers(registered), json={"raw": "{}"}
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_request"


def test_import_rejects_invalid_json(client: TestClient, registered: dict) -> None:
    response = client.post(
        "/api/mcp/servers/import", headers=_headers(registered), json={"raw": "not json"}
    )
    assert response.status_code == 422
