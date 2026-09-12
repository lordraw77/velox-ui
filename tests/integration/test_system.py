"""System endpoints."""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_health_is_constant_and_cheap(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_health_carries_a_request_id(client: TestClient) -> None:
    assert client.get("/health").headers.get("x-request-id")


def test_inbound_request_id_is_honoured(client: TestClient) -> None:
    response = client.get("/health", headers={"X-Request-ID": "from-the-proxy"})
    assert response.headers["x-request-id"] == "from-the-proxy"


def test_ready_reports_the_schema(client: TestClient) -> None:
    body = client.get("/ready").json()
    assert body["status"] == "ready"
    assert body["schema"] == "current"


def test_version(client: TestClient) -> None:
    body = client.get("/api/version").json()
    assert body["name"] == "velox-ui"
    assert body["database"] == "sqlite"


def test_client_config_announces_first_run(client: TestClient) -> None:
    assert client.get("/api/config").json()["auth"]["setup_required"] is True


def test_client_config_stops_announcing_first_run(client: TestClient, registered: dict) -> None:
    assert client.get("/api/config").json()["auth"]["setup_required"] is False


def test_metrics_exposes_the_histograms(client: TestClient) -> None:
    client.get("/health")
    body = client.get("/metrics").text
    assert "velox_http_requests_total" in body
    assert "velox_completion_ttft_seconds" in body


def test_metrics_labels_use_the_route_template(client: TestClient) -> None:
    """Metric labels must be bounded, whatever path a caller invents.

    Labelling by raw path would let anyone create unlimited metric series by
    requesting random URLs, which is a denial-of-service against the monitoring
    rather than against the server.
    """
    client.get("/api/nope-does-not-exist")
    client.get("/another-invented-path")
    body = client.get("/metrics").text

    assert "/api/nope-does-not-exist" not in body
    assert "/another-invented-path" not in body
    # Unmatched paths land on a template: the API 404 handler or the single-page
    # fallback, depending on whether the interface has been built.
    assert 'route="unmatched"' in body or 'route="/{path' in body
