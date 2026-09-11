"""API key management for the signed-in account.

A key is returned in full exactly once, in the response that creates it. After that
only its prefix is available, because only its hash is stored (ADR-0013).
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, Field

from velox_ui.api.deps import CurrentPrincipal, State
from velox_ui.db.repositories.credentials import CredentialRepository
from velox_ui.errors import NotFoundError
from velox_ui.security.apikeys import generate_api_key, mask_key

router = APIRouter(prefix="/api/me/api-keys", tags=["api-keys"])


class CreateKeyRequest(BaseModel):
    """Payload for minting a key."""

    name: str = Field(min_length=1, max_length=200)
    expires_at: int | None = Field(default=None, description="Epoch milliseconds.")


class KeySummary(BaseModel):
    """A stored key, as the UI sees it."""

    id: str
    name: str
    masked: str
    last_used_at: int | None
    expires_at: int | None
    created_at: int


class CreatedKey(KeySummary):
    """A newly minted key, including the plaintext shown only this once."""

    key: str


@router.get("", response_model=list[KeySummary], summary="List API keys")
async def list_keys(principal: CurrentPrincipal, state: State) -> list[KeySummary]:
    """Return the caller's keys, newest first."""
    async with state.db.session() as session:
        records = await CredentialRepository(session).list_api_keys(principal.user_id)
        return [
            KeySummary(
                id=record.id,
                name=record.name,
                masked=mask_key(record.prefix),
                last_used_at=record.last_used_at,
                expires_at=record.expires_at,
                created_at=record.created_at,
            )
            for record in records
        ]


@router.post("", response_model=CreatedKey, status_code=201, summary="Create an API key")
async def create_key(
    payload: CreateKeyRequest, principal: CurrentPrincipal, state: State
) -> CreatedKey:
    """Mint a key and return it once.

    The plaintext is not recoverable afterwards; the UI must tell the user to copy it
    now, and does.
    """
    generated = generate_api_key()
    async with state.db.write() as session:
        record = await CredentialRepository(session).create_api_key(
            user_id=principal.user_id,
            name=payload.name,
            generated=generated,
            expires_at=payload.expires_at,
        )
        await session.flush()
        summary = CreatedKey(
            id=record.id,
            name=record.name,
            masked=mask_key(record.prefix),
            last_used_at=None,
            expires_at=record.expires_at,
            created_at=record.created_at,
            key=generated.plaintext,
        )
    return summary


@router.delete("/{key_id}", status_code=204, summary="Delete an API key")
async def delete_key(key_id: str, principal: CurrentPrincipal, state: State) -> None:
    """Revoke a key immediately.

    Raises:
        NotFoundError: If the key does not exist or belongs to someone else.
    """
    async with state.db.write() as session:
        removed = await CredentialRepository(session).delete_api_key(principal.user_id, key_id)
    if not removed:
        raise NotFoundError("No such API key.")
