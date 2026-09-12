"""Security headers."""

from __future__ import annotations

from fastapi.testclient import TestClient

from velox_ui.api.security_headers import CONTENT_SECURITY_POLICY


def _policy(client: TestClient) -> dict[str, str]:
    header = client.get("/health").headers["content-security-policy"]
    directives: dict[str, str] = {}
    for part in header.split(";"):
        name, _, value = part.strip().partition(" ")
        if name:
            directives[name] = value
    return directives


def test_headers_are_present_on_every_response(client: TestClient) -> None:
    headers = client.get("/health").headers
    assert headers["content-security-policy"] == CONTENT_SECURITY_POLICY
    assert headers["x-content-type-options"] == "nosniff"
    assert headers["referrer-policy"] == "same-origin"
    assert headers["x-frame-options"] == "DENY"


def test_scripts_cannot_be_inlined_or_evaluated(client: TestClient) -> None:
    """The second line of defence behind escaping raw HTML.

    Markdown rendering already strips raw HTML before parsing, but if that ever let
    something through, a policy without `unsafe-inline` means the injected markup
    still cannot execute.
    """
    script_src = _policy(client)["script-src"]
    assert "'unsafe-inline'" not in script_src
    assert "'unsafe-eval'" not in script_src
    assert script_src == "'self'"


def test_inline_styles_are_allowed_only_as_attributes(client: TestClient) -> None:
    # The virtual list sets an inline transform that changes as the reader scrolls,
    # which no nonce can cover. Allowing style *attributes* does not allow scripts.
    policy = _policy(client)
    assert policy["style-src"] == "'self'"
    assert policy["style-src-attr"] == "'unsafe-inline'"


def test_the_page_cannot_be_framed_or_rebased(client: TestClient) -> None:
    policy = _policy(client)
    assert policy["frame-ancestors"] == "'none'"
    assert policy["base-uri"] == "'none'"
    assert policy["object-src"] == "'none'"


def test_the_worker_is_permitted(client: TestClient) -> None:
    # Markdown rendering runs in a worker; a policy that forbade it would leave every
    # message unformatted with no error the user could see.
    assert "'self'" in _policy(client)["worker-src"]


def test_hsts_is_not_asserted_over_plain_http(client: TestClient) -> None:
    # A self-hosted instance is often reached over HTTP on a home network. Asserting
    # HSTS there can lock someone out of their own server.
    assert "strict-transport-security" not in client.get("/health").headers


def test_hsts_is_asserted_over_https(client: TestClient) -> None:
    response = client.get("https://testserver/health")
    assert "max-age=" in response.headers["strict-transport-security"]
