"""Request dependencies: application state and authentication.

Authentication accepts two credential forms on the same header. A JWT is verified
without touching the database; an API key (recognisable by its ``velox_sk_`` prefix)
costs one indexed lookup. Both resolve to the same :class:`Principal`, so no route ever
has to care which was used.

When ``auth.enabled`` is false the whole layer collapses to a single built-in local
account. That is the single-user desktop case, and it exists so that a private instance
is not forced through a login screen it gains nothing from.
"""

from __future__ import annotations

from typing import Annotated, Final

from fastapi import Depends, Request

from velox_ui.db.repositories.credentials import CredentialRepository
from velox_ui.errors import AuthenticationError, ForbiddenError
from velox_ui.security.apikeys import hash_api_key, looks_like_api_key
from velox_ui.security.tokens import decode_access_token
from velox_ui.state import AppState

__all__ = ["AdminPrincipal", "CurrentPrincipal", "Principal", "State", "get_state"]

_BEARER: Final = "bearer"
LOCAL_USER_EMAIL: Final = "local@velox.local"


class Principal:
    """The authenticated caller.

    Attributes:
        user_id: The account id.
        role: ``admin`` or ``user``.
        via: ``jwt``, ``api_key`` or ``anonymous`` (authentication disabled).
        api_key_id: Which key was used, when applicable.
    """

    __slots__ = ("api_key_id", "role", "user_id", "via")

    def __init__(
        self, user_id: str, role: str, via: str, api_key_id: str | None = None
    ) -> None:
        """Store the principal fields."""
        self.user_id = user_id
        self.role = role
        self.via = via
        self.api_key_id = api_key_id

    @property
    def is_admin(self) -> bool:
        """Whether the caller has administrator rights."""
        return self.role == "admin"


def get_state(request: Request) -> AppState:
    """Return the application state attached at startup."""
    state: AppState = request.app.state.velox
    return state


State = Annotated[AppState, Depends(get_state)]


def _bearer_credential(request: Request) -> str | None:
    """Extract a bearer credential from the Authorization header or the auth cookie."""
    header = request.headers.get("authorization")
    if header:
        scheme, _, value = header.partition(" ")
        if scheme.lower() == _BEARER and value:
            return value.strip()
    return request.cookies.get("velox_access") or None


async def authenticate(request: Request, state: State) -> Principal:
    """Resolve the caller's identity.

    Args:
        request: The incoming request.
        state: Application state.

    Returns:
        The authenticated :class:`Principal`.

    Raises:
        AuthenticationError: If no usable credential was presented.
    """
    if not state.settings.auth.enabled:
        return Principal(state.local_user_id or "", "admin", via="anonymous")

    credential = _bearer_credential(request)
    if not credential:
        raise AuthenticationError("Authentication is required.")

    if looks_like_api_key(credential):
        return await _authenticate_api_key(credential, state)

    claims = decode_access_token(state.settings.secret_key, credential)
    return Principal(claims.user_id, claims.role, via="jwt")


async def _authenticate_api_key(credential: str, state: AppState) -> Principal:
    """Resolve an API key to a principal."""
    async with state.db.session() as session:
        record = await CredentialRepository(session).api_key_by_hash(hash_api_key(credential))
        if record is None:
            raise AuthenticationError("The API key is not valid.")
        from velox_ui.db.repositories.users import UserRepository

        user = await UserRepository(session).by_id(record.user_id)
        if user is None or user.status != "active":
            raise AuthenticationError("The account for this API key is not active.")
        role, user_id, key_id = user.role, user.id, record.id

    # Recorded outside the read session so authentication never waits on the writer.
    state.schedule(_touch_api_key(state, key_id))
    return Principal(user_id, role, via="api_key", api_key_id=key_id)


async def _touch_api_key(state: AppState, key_id: str) -> None:
    """Update an API key's last-used timestamp in the background."""
    async with state.db.write() as session:
        await CredentialRepository(session).touch_api_key(key_id)


CurrentPrincipal = Annotated[Principal, Depends(authenticate)]


async def require_admin(principal: CurrentPrincipal) -> Principal:
    """Require administrator rights.

    Raises:
        ForbiddenError: If the caller is not an administrator.
    """
    if not principal.is_admin:
        raise ForbiddenError("This action requires administrator rights.")
    return principal


AdminPrincipal = Annotated[Principal, Depends(require_admin)]
