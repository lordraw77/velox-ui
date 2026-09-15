"""Plugin discovery, configuration and validation (ADR-0014).

Cold path throughout: enabling a plugin or checking it works is an administrator
action, never part of a completion turn.
"""

from __future__ import annotations

from typing import get_args

from fastapi import APIRouter
from pydantic import BaseModel

from velox_ui.api.deps import AdminPrincipal, CurrentPrincipal, State
from velox_ui.db.repositories.plugin_config import PluginConfigRepository
from velox_ui.errors import NotFoundError
from velox_ui.plugins.spec import PluginKind
from velox_ui.security.crypto import mask_secret

router = APIRouter(prefix="/api/plugins", tags=["plugins"])

_KINDS: tuple[PluginKind, ...] = get_args(PluginKind.__value__)


class PluginInfoResponse(BaseModel):
    """One plugin kind's discovery and configuration state."""

    name: str
    kind: str
    enabled: bool
    configured: bool
    description: str
    base_url: str | None = None
    model: str | None = None
    auth_hint: str | None = None


class UpdatePluginRequest(BaseModel):
    """Payload for enabling/configuring a plugin.

    ``api_key``: ``None`` leaves the stored credential untouched, an empty string
    removes it, anything else replaces it (the same convention
    ``UpdateMcpServerRequest.auth_token`` uses).
    """

    enabled: bool
    base_url: str | None = None
    model: str | None = None
    tts_voice: str | None = None
    api_key: str | None = None


def _valid_kind(name: str) -> PluginKind:
    if name not in _KINDS:
        raise NotFoundError(f"No such plugin kind: {name!r}.")
    return name


@router.get("", response_model=list[PluginInfoResponse], summary="List plugins")
async def list_plugins(principal: CurrentPrincipal, state: State) -> list[PluginInfoResponse]:
    """Return every plugin kind's discovery and configuration state.

    Open to any signed-in user, the same as provider listing: the client needs to
    know whether images/voice are enabled to show or hide their controls, even
    though only an administrator can change the configuration.
    """
    del principal
    results: list[PluginInfoResponse] = []
    async with state.db.session() as session:
        repository = PluginConfigRepository(session)
        for kind in _KINDS:
            stored = await repository.get(kind) or {}
            info = state.plugins.info(
                kind,
                enabled=bool(stored.get("enabled")),
                configured=bool(stored.get("base_url")),
            )
            auth_hint = None
            auth_ref = stored.get("auth_ref")
            if auth_ref:
                secret = await repository.get_secret(auth_ref)
                auth_hint = secret.hint if secret is not None else None
            results.append(
                PluginInfoResponse(
                    name=info.name,
                    kind=info.kind,
                    enabled=info.enabled,
                    configured=info.configured,
                    description=info.description,
                    base_url=stored.get("base_url"),
                    model=stored.get("model"),
                    auth_hint=auth_hint,
                )
            )
    return results


@router.put("/{name}", response_model=PluginInfoResponse, summary="Configure a plugin")
async def update_plugin(
    name: str, payload: UpdatePluginRequest, principal: AdminPrincipal, state: State
) -> PluginInfoResponse:
    """Enable, disable or reconfigure a plugin.

    Raises:
        NotFoundError: If ``name`` is not a known plugin kind.
    """
    del principal
    kind = _valid_kind(name)
    async with state.db.write() as session:
        repository = PluginConfigRepository(session)
        stored = await repository.get(kind) or {}
        auth_ref = stored.get("auth_ref")
        auth_hint: str | None = None
        if auth_ref:
            existing_secret = await repository.get_secret(auth_ref)
            auth_hint = existing_secret.hint if existing_secret is not None else None

        if payload.api_key is not None:
            if payload.api_key == "":
                if auth_ref:
                    await repository.delete_secret(auth_ref)
                auth_ref = None
                auth_hint = None
            else:
                ref = auth_ref or f"plugin:{kind}"
                nonce, ciphertext = state.secrets.encrypt(payload.api_key, ref=ref)
                auth_hint = mask_secret(payload.api_key)
                await repository.put_secret(
                    ref, nonce=nonce, ciphertext=ciphertext, hint=auth_hint
                )
                auth_ref = ref

        value: dict[str, object] = {
            "enabled": payload.enabled,
            "base_url": payload.base_url,
            "model": payload.model,
            "extra": {"tts_voice": payload.tts_voice} if payload.tts_voice else {},
            "auth_ref": auth_ref,
        }
        await repository.put(kind, value)

    state.plugins.invalidate(kind)
    info = state.plugins.info(kind, enabled=payload.enabled, configured=bool(payload.base_url))
    return PluginInfoResponse(
        name=info.name,
        kind=info.kind,
        enabled=info.enabled,
        configured=info.configured,
        description=info.description,
        base_url=payload.base_url,
        model=payload.model,
        auth_hint=auth_hint,
    )


@router.post("/{name}/validate", summary="Check a plugin's configured backend")
async def validate_plugin(
    name: str, principal: AdminPrincipal, state: State
) -> dict[str, object]:
    """Run the plugin's ``validate()`` hook (ADR-0014).

    Raises:
        NotFoundError: If ``name`` is not a known plugin kind.
    """
    del principal
    kind = _valid_kind(name)
    plugin = await state.plugins.get(kind)
    if plugin is None:
        return {"ok": False, "detail": "Not enabled or not configured."}
    result = await plugin.validate()
    return {"ok": result.ok, "detail": result.detail}
