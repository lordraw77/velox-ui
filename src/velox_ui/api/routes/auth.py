"""Registration, login, refresh and logout.

These are cold-path routes, so they use ordinary Pydantic models: the validation and
the generated OpenAPI documentation are worth their cost here, and none of it runs
during a completion (ADR-0001).

Two behaviours are deliberate. Login answers identically for an unknown address and a
wrong password, and performs the same Argon2 work in both cases, so response content
and response time both stay useless for enumerating accounts. And the refresh token is
delivered as an ``HttpOnly`` cookie rather than in the response body, so a cross-site
scripting bug cannot read the long-lived credential.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Cookie, Request, Response
from pydantic import BaseModel, Field

from velox_ui.api.deps import CurrentPrincipal, State
from velox_ui.api.schemas.fields import DisplayName, Email, Password
from velox_ui.db.repositories.credentials import CredentialRepository
from velox_ui.db.repositories.users import UserRepository
from velox_ui.errors import AuthenticationError, ConflictError, ForbiddenError
from velox_ui.security.password import hash_password, needs_rehash, verify_password
from velox_ui.security.tokens import issue_access_token
from velox_ui.state import AppState

router = APIRouter(prefix="/api/auth", tags=["auth"])

REFRESH_COOKIE = "velox_refresh"


class RegisterRequest(BaseModel):
    """Payload for creating an account."""

    email: Email
    password: Password
    name: DisplayName


class LoginRequest(BaseModel):
    """Payload for exchanging credentials for tokens."""

    email: Email
    password: str = Field(min_length=1, max_length=1024)


class TokenResponse(BaseModel):
    """A freshly issued access token.

    The refresh token is not present: it travels in an ``HttpOnly`` cookie.
    """

    access_token: str
    token_type: str = "bearer"  # noqa: S105 - an auth scheme name, not a credential
    expires_at: int
    user_id: str
    role: str


class ProfileResponse(BaseModel):
    """The authenticated account."""

    id: str
    email: str
    name: str
    role: str
    status: str
    created_at: int


def _set_refresh_cookie(response: Response, token: str, *, ttl_s: int, secure: bool) -> None:
    """Attach the refresh cookie.

    ``secure`` is not forced on: a self-hosted instance is very often reached over
    plain HTTP on a home network, and a cookie the browser refuses to send would break
    login entirely. It is enabled automatically when the request arrives over HTTPS.
    """
    response.set_cookie(
        REFRESH_COOKIE,
        token,
        max_age=ttl_s,
        httponly=True,
        samesite="lax",
        secure=secure,
        path="/api/auth",
    )


async def _issue_session(
    state: AppState,
    response: Response,
    *,
    user_id: str,
    role: str,
    user_agent: str | None,
    secure: bool,
) -> TokenResponse:
    """Create a refresh family and mint the first access token for it."""
    settings = state.settings.auth
    async with state.db.write() as session:
        token, family = await CredentialRepository(session).issue_refresh_token(
            user_id=user_id, ttl_s=settings.refresh_token_ttl_s, user_agent=user_agent
        )
    _set_refresh_cookie(response, token, ttl_s=settings.refresh_token_ttl_s, secure=secure)
    access, expires_at = issue_access_token(
        state.settings.secret_key,
        user_id=user_id,
        role=role,
        session_id=family,
        ttl_s=settings.access_token_ttl_s,
    )
    return TokenResponse(access_token=access, expires_at=expires_at, user_id=user_id, role=role)


@router.post("/register", response_model=TokenResponse, summary="Create an account")
async def register(
    payload: RegisterRequest, request: Request, response: Response, state: State
) -> TokenResponse:
    """Create an account and sign in.

    The very first account is always an administrator, so a fresh instance is usable
    without an out-of-band bootstrap step. Every later account is a regular user, and
    is refused unless open registration is enabled.
    """
    async with state.db.write() as session:
        users = UserRepository(session)
        first_account = await users.count() == 0
        if not first_account and not state.settings.auth.open_registration:
            raise ForbiddenError("Registration is closed on this instance.")
        if await users.by_email(payload.email) is not None:
            raise ConflictError("An account with this email address already exists.")
        user = await users.create(
            email=payload.email,
            name=payload.name,
            password_hash=hash_password(payload.password),
            role="admin" if first_account else "user",
        )
        user_id, role = user.id, user.role

    return await _issue_session(
        state,
        response,
        user_id=user_id,
        role=role,
        user_agent=request.headers.get("user-agent"),
        secure=request.url.scheme == "https",
    )


@router.post("/login", response_model=TokenResponse, summary="Sign in")
async def login(
    payload: LoginRequest, request: Request, response: Response, state: State
) -> TokenResponse:
    """Exchange an email and password for tokens.

    Raises:
        AuthenticationError: For an unknown address, a wrong password or a disabled
            account. The three are indistinguishable from outside on purpose.
    """
    async with state.db.session() as session:
        user = await UserRepository(session).by_email(payload.email)
        stored_hash = user.password_hash if user else None
        matched = verify_password(stored_hash, payload.password)
        if not matched or user is None or user.status != "active":
            raise AuthenticationError("The email address or password is incorrect.")
        user_id, role = user.id, user.role
        stale_hash = stored_hash is not None and needs_rehash(stored_hash)

    if stale_hash:
        # The stored hash predates the current cost parameters. Upgrading it here is
        # the only moment the plaintext is available.
        async with state.db.write() as session:
            await UserRepository(session).set_password(user_id, hash_password(payload.password))

    tokens = await _issue_session(
        state,
        response,
        user_id=user_id,
        role=role,
        user_agent=request.headers.get("user-agent"),
        secure=request.url.scheme == "https",
    )
    state.schedule(_touch_last_seen(state, user_id))
    return tokens


async def _touch_last_seen(state: AppState, user_id: str) -> None:
    """Record the login time without holding up the response."""
    async with state.db.write() as session:
        await UserRepository(session).touch_last_seen(user_id)


@router.post("/refresh", response_model=TokenResponse, summary="Rotate the session")
async def refresh(
    request: Request,
    response: Response,
    state: State,
    velox_refresh: Annotated[str | None, Cookie()] = None,
) -> TokenResponse:
    """Exchange a refresh token for a new access token and a new refresh token.

    Raises:
        AuthenticationError: If the token is missing, expired, or was already used.
            Reuse revokes the whole family, which signs out both the attacker and the
            legitimate user: with a stolen token there is no way to tell them apart,
            and keeping the session alive would favour the attacker.
    """
    if not velox_refresh:
        raise AuthenticationError("No refresh token was presented.")

    settings = state.settings.auth
    async with state.db.write() as session:
        outcome = await CredentialRepository(session).rotate_refresh_token(
            velox_refresh,
            ttl_s=settings.refresh_token_ttl_s,
            user_agent=request.headers.get("user-agent"),
        )
        user = await UserRepository(session).by_id(outcome.user_id) if outcome.user_id else None
        role = user.role if user else "user"
        active = user is not None and user.status == "active"

    if outcome.reused:
        response.delete_cookie(REFRESH_COOKIE, path="/api/auth")
        raise AuthenticationError(
            "This session was signed out because a refresh token was reused. Sign in again."
        )
    if not outcome.ok or not active or outcome.user_id is None:
        response.delete_cookie(REFRESH_COOKIE, path="/api/auth")
        raise AuthenticationError("The session has expired. Sign in again.")

    assert outcome.token is not None  # noqa: S101 - guaranteed by outcome.ok
    _set_refresh_cookie(
        response,
        outcome.token,
        ttl_s=settings.refresh_token_ttl_s,
        secure=request.url.scheme == "https",
    )
    access, expires_at = issue_access_token(
        state.settings.secret_key,
        user_id=outcome.user_id,
        role=role,
        session_id=outcome.family_id or "",
        ttl_s=settings.access_token_ttl_s,
    )
    return TokenResponse(
        access_token=access, expires_at=expires_at, user_id=outcome.user_id, role=role
    )


@router.post("/logout", summary="Sign out")
async def logout(
    response: Response,
    state: State,
    velox_refresh: Annotated[str | None, Cookie()] = None,
) -> dict[str, bool]:
    """Revoke the current refresh token and clear the cookie.

    Succeeds even without a valid token: signing out must never fail.
    """
    if velox_refresh:
        async with state.db.write() as session:
            await CredentialRepository(session).revoke_token(velox_refresh)
    response.delete_cookie(REFRESH_COOKIE, path="/api/auth")
    return {"ok": True}


@router.get("/me", response_model=ProfileResponse, summary="The authenticated account")
async def me(principal: CurrentPrincipal, state: State) -> ProfileResponse:
    """Return the signed-in account.

    Raises:
        AuthenticationError: If the account has been removed since the token was issued.
    """
    async with state.db.session() as session:
        user = await UserRepository(session).by_id(principal.user_id)
        if user is None:
            raise AuthenticationError("This account no longer exists.")
        return ProfileResponse(
            id=user.id,
            email=user.email,
            name=user.name,
            role=user.role,
            status=user.status,
            created_at=user.created_at,
        )
