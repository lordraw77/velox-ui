"""Administrator user management.

Every route requires :func:`~velox_ui.api.deps.require_admin` via
:data:`~velox_ui.api.deps.AdminPrincipal`. Registration itself is gated separately, in
``auth.py``: ``open_registration=false`` refuses self-service sign-up once the first
(admin) account exists, and these routes are how an administrator creates further
accounts on such an instance.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query
from pydantic import BaseModel

from velox_ui.api.deps import AdminPrincipal, State
from velox_ui.api.pagination import decode_cursor, encode_cursor
from velox_ui.api.schemas.fields import DisplayName, Email, Password
from velox_ui.db.models import AppUser
from velox_ui.db.repositories.users import UserRepository
from velox_ui.errors import ConflictError, ForbiddenError, NotFoundError, ValidationError
from velox_ui.security.password import hash_password

router = APIRouter(prefix="/api/admin/users", tags=["admin"])

_ROLES = ("admin", "user")
_STATUSES = ("active", "pending", "disabled")


class CreateUserRequest(BaseModel):
    """Payload for an administrator creating an account directly."""

    email: Email
    password: Password
    name: DisplayName
    role: str = "user"


class SetRoleRequest(BaseModel):
    """Payload for changing an account's role."""

    role: str


class SetStatusRequest(BaseModel):
    """Payload for changing an account's status."""

    status: str


class UserResponse(BaseModel):
    """An account, as the admin console renders it."""

    id: str
    email: str
    name: str
    role: str
    status: str
    created_at: int
    last_seen_at: int | None


class UserPage(BaseModel):
    """A keyset page of accounts."""

    items: list[UserResponse]
    next_cursor: str | None


def _to_response(user: AppUser) -> UserResponse:
    """Build a response from an ORM ``AppUser`` row."""
    return UserResponse(
        id=user.id,
        email=user.email,
        name=user.name,
        role=user.role,
        status=user.status,
        created_at=user.created_at,
        last_seen_at=user.last_seen_at,
    )


@router.get("", response_model=UserPage, summary="List accounts")
async def list_users(
    principal: AdminPrincipal,
    state: State,
    cursor: Annotated[str | None, Query()] = None,
    limit: Annotated[int | None, Query()] = None,
) -> UserPage:
    """Return one keyset page of accounts, newest first."""
    decoded = decode_cursor(cursor, arity=2) if cursor else None
    typed_cursor = (int(decoded[0]), str(decoded[1])) if decoded else None
    async with state.db.session() as session:
        users, next_key = await UserRepository(session).list_page(
            limit=max(1, min(limit or 50, 200)), cursor=typed_cursor
        )
        return UserPage(
            items=[_to_response(user) for user in users],
            next_cursor=encode_cursor(next_key) if next_key else None,
        )


@router.post("", response_model=UserResponse, status_code=201, summary="Create an account")
async def create_user(
    payload: CreateUserRequest, principal: AdminPrincipal, state: State
) -> UserResponse:
    """Create an account directly, bypassing self-service registration.

    This is how an administrator adds users once ``open_registration`` is disabled.

    Raises:
        ValidationError: If ``role`` is not a recognised value.
        ConflictError: If an account with this email already exists.
    """
    if payload.role not in _ROLES:
        raise ValidationError(f"role must be one of {_ROLES}.")
    async with state.db.write() as session:
        users = UserRepository(session)
        if await users.by_email(payload.email) is not None:
            raise ConflictError("An account with this email address already exists.")
        user = await users.create(
            email=payload.email,
            name=payload.name,
            password_hash=hash_password(payload.password),
            role=payload.role,
        )
        await session.flush()
        return _to_response(user)


@router.patch(
    "/{user_id}/role", response_model=UserResponse, summary="Change an account's role"
)
async def set_role(
    user_id: str, payload: SetRoleRequest, principal: AdminPrincipal, state: State
) -> UserResponse:
    """Promote or demote an account.

    Raises:
        ValidationError: If ``role`` is not a recognised value.
        ForbiddenError: If the caller would demote their own last administrator account.
        NotFoundError: If the account does not exist.
    """
    if payload.role not in _ROLES:
        raise ValidationError(f"role must be one of {_ROLES}.")
    async with state.db.write() as session:
        users = UserRepository(session)
        if user_id == principal.user_id and payload.role != "admin":
            raise ForbiddenError("You cannot remove your own administrator role.")
        if not await users.set_role(user_id, role=payload.role):
            raise NotFoundError("No such account.")
        user = await users.by_id(user_id)
        assert user is not None  # noqa: S101 - just updated in this transaction
        return _to_response(user)


@router.patch(
    "/{user_id}/status", response_model=UserResponse, summary="Enable or disable an account"
)
async def set_status(
    user_id: str, payload: SetStatusRequest, principal: AdminPrincipal, state: State
) -> UserResponse:
    """Change an account's status.

    Raises:
        ValidationError: If ``status`` is not a recognised value.
        ForbiddenError: If the caller would disable their own account.
        NotFoundError: If the account does not exist.
    """
    if payload.status not in _STATUSES:
        raise ValidationError(f"status must be one of {_STATUSES}.")
    if user_id == principal.user_id and payload.status != "active":
        raise ForbiddenError("You cannot disable your own account.")
    async with state.db.write() as session:
        users = UserRepository(session)
        if not await users.set_status(user_id, status=payload.status):
            raise NotFoundError("No such account.")
        user = await users.by_id(user_id)
        assert user is not None  # noqa: S101 - just updated in this transaction
        return _to_response(user)


@router.delete("/{user_id}", status_code=204, summary="Delete an account")
async def delete_user(user_id: str, principal: AdminPrincipal, state: State) -> None:
    """Delete an account and everything it owns.

    Raises:
        ForbiddenError: If the caller would delete their own account.
        NotFoundError: If the account does not exist.
    """
    if user_id == principal.user_id:
        raise ForbiddenError("You cannot delete your own account.")
    async with state.db.write() as session:
        removed = await UserRepository(session).delete(user_id)
    if not removed:
        raise NotFoundError("No such account.")
