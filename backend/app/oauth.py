"""OAuth 2.1 authorization server backing the MCP endpoint.

Implements the MCP SDK's OAuthAuthorizationServerProvider protocol. The SDK
supplies the HTTP endpoints (/authorize, /token, /register, /revoke and the
.well-known metadata) and PKCE verification; this module supplies storage and
identity:

- Clients and refresh tokens are persisted in the database.
- /authorize redirects to our own login/consent page (routers/oauth_login.py),
  which calls back into ``complete_authorization`` after the user signs in.
- Access tokens are short-lived JWTs signed with the app's SECRET_KEY and an
  ``aud`` claim of MCP_AUDIENCE, so they are never interchangeable with the
  web app's login JWTs (which carry no audience).
"""
import json
import logging
import secrets
import time

import jwt

from .config import (
    JWT_ALGORITHM,
    MCP_ACCESS_TOKEN_EXPIRE_SECONDS,
    MCP_AUDIENCE,
    MCP_REFRESH_TOKEN_EXPIRE_DAYS,
    PUBLIC_URL,
    SECRET_KEY,
)
from .database import SessionLocal
from .models import OAuthAuthCode, OAuthClient, OAuthRefreshToken, User

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    RefreshToken,
    TokenError,
    construct_redirect_uri,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

logger = logging.getLogger("berebank.oauth")

AUTH_CODE_TTL_SECONDS = 300  # 5 minutes, per OAuth 2.1 recommendations
PENDING_TTL_SECONDS = 600  # login page must be completed within 10 minutes


class PendingAuthorization:
    """An /authorize request waiting for the user to sign in and consent."""

    def __init__(self, client: OAuthClientInformationFull, params: AuthorizationParams):
        self.client = client
        self.params = params
        self.expires_at = time.time() + PENDING_TTL_SECONDS


# txn id -> PendingAuthorization. In-memory is fine: the app runs as a single
# process, and an abandoned login page simply expires.
_pending: dict[str, PendingAuthorization] = {}


def _prune_pending() -> None:
    now = time.time()
    for txn in [t for t, p in _pending.items() if p.expires_at < now]:
        _pending.pop(txn, None)


def get_pending(txn: str) -> PendingAuthorization | None:
    _prune_pending()
    pending = _pending.get(txn)
    if pending is None or pending.expires_at < time.time():
        return None
    return pending


def deny_authorization(txn: str) -> str:
    """User clicked Deny: consume the transaction, redirect back with an error."""
    pending = _pending.pop(txn, None)
    if pending is None:
        raise ValueError("Authorization request expired")
    return construct_redirect_uri(
        str(pending.params.redirect_uri), error="access_denied", state=pending.params.state
    )


def complete_authorization(txn: str, user: User) -> str:
    """User authenticated and consented: issue an auth code, build the redirect."""
    pending = _pending.pop(txn, None)
    if pending is None:
        raise ValueError("Authorization request expired")
    params = pending.params
    code = secrets.token_urlsafe(32)
    db = SessionLocal()
    try:
        db.add(OAuthAuthCode(
            code=code,
            user_id=user.id,
            client_id=pending.client.client_id,
            scopes=" ".join(params.scopes or []),
            code_challenge=params.code_challenge,
            redirect_uri=str(params.redirect_uri),
            redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly,
            resource=params.resource,
            expires_at=time.time() + AUTH_CODE_TTL_SECONDS,
        ))
        db.commit()
    finally:
        db.close()
    logger.info("Issued MCP auth code for user %s (client %s)", user.email, pending.client.client_id)
    return construct_redirect_uri(str(params.redirect_uri), code=code, state=params.state)


def _create_access_token(user_id: int, client_id: str, scopes: list[str]) -> tuple[str, int]:
    expires_at = int(time.time()) + MCP_ACCESS_TOKEN_EXPIRE_SECONDS
    token = jwt.encode(
        {
            "sub": str(user_id),
            "aud": MCP_AUDIENCE,
            "client_id": client_id,
            "scopes": scopes,
            "exp": expires_at,
            "iat": int(time.time()),
        },
        SECRET_KEY,
        algorithm=JWT_ALGORITHM,
    )
    return token, expires_at


def _issue_tokens(db, user_id: int, client_id: str, scopes: list[str]) -> OAuthToken:
    access_token, _ = _create_access_token(user_id, client_id, scopes)
    refresh_token = secrets.token_urlsafe(48)
    db.add(OAuthRefreshToken(
        token=refresh_token,
        user_id=user_id,
        client_id=client_id,
        scopes=" ".join(scopes),
        expires_at=time.time() + MCP_REFRESH_TOKEN_EXPIRE_DAYS * 86400,
    ))
    db.commit()
    return OAuthToken(
        access_token=access_token,
        token_type="Bearer",
        expires_in=MCP_ACCESS_TOKEN_EXPIRE_SECONDS,
        scope=" ".join(scopes) if scopes else None,
        refresh_token=refresh_token,
    )


class BereBankOAuthProvider:
    """OAuthAuthorizationServerProvider implementation over the app database."""

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        db = SessionLocal()
        try:
            row = db.get(OAuthClient, client_id)
        finally:
            db.close()
        if row is None:
            return None
        return OAuthClientInformationFull.model_validate(json.loads(row.client_info))

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        db = SessionLocal()
        try:
            db.add(OAuthClient(
                client_id=client_info.client_id,
                client_info=client_info.model_dump_json(),
            ))
            db.commit()
        finally:
            db.close()
        logger.info("Registered MCP OAuth client %s (%s)", client_info.client_id, client_info.client_name)

    async def authorize(self, client: OAuthClientInformationFull, params: AuthorizationParams) -> str:
        _prune_pending()
        txn = secrets.token_urlsafe(32)
        _pending[txn] = PendingAuthorization(client, params)
        return f"{PUBLIC_URL}/oauth/login?txn={txn}"

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> AuthorizationCode | None:
        db = SessionLocal()
        try:
            row = db.get(OAuthAuthCode, authorization_code)
            if row is None or row.client_id != client.client_id or row.expires_at < time.time():
                return None
            return AuthorizationCode(
                code=row.code,
                scopes=row.scopes.split() if row.scopes else [],
                expires_at=row.expires_at,
                client_id=row.client_id,
                code_challenge=row.code_challenge,
                redirect_uri=row.redirect_uri,
                redirect_uri_provided_explicitly=row.redirect_uri_provided_explicitly,
                resource=row.resource,
                subject=str(row.user_id),
            )
        finally:
            db.close()

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> OAuthToken:
        db = SessionLocal()
        try:
            row = db.get(OAuthAuthCode, authorization_code.code)
            if row is None:
                raise TokenError("invalid_grant", "Authorization code is invalid or already used")
            user_id = row.user_id
            db.delete(row)  # single use
            return _issue_tokens(db, user_id, client.client_id, authorization_code.scopes)
        finally:
            db.close()

    async def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> RefreshToken | None:
        db = SessionLocal()
        try:
            row = db.get(OAuthRefreshToken, refresh_token)
            if (
                row is None
                or row.revoked
                or row.client_id != client.client_id
                or row.expires_at < time.time()
            ):
                return None
            return RefreshToken(
                token=row.token,
                client_id=row.client_id,
                scopes=row.scopes.split() if row.scopes else [],
                expires_at=int(row.expires_at),
                subject=str(row.user_id),
            )
        finally:
            db.close()

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: RefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        db = SessionLocal()
        try:
            row = db.get(OAuthRefreshToken, refresh_token.token)
            if row is None or row.revoked:
                raise TokenError("invalid_grant", "Refresh token is invalid or revoked")
            user = db.get(User, row.user_id)
            if user is None or not user.is_active:
                raise TokenError("invalid_grant", "User account is deactivated")
            row.revoked = True  # rotate: old refresh token becomes unusable
            new_scopes = scopes or refresh_token.scopes
            return _issue_tokens(db, row.user_id, client.client_id, new_scopes)
        finally:
            db.close()

    async def load_access_token(self, token: str) -> AccessToken | None:
        try:
            payload = jwt.decode(
                token, SECRET_KEY, algorithms=[JWT_ALGORITHM], audience=MCP_AUDIENCE
            )
        except jwt.PyJWTError:
            return None
        db = SessionLocal()
        try:
            user = db.get(User, int(payload["sub"]))
        finally:
            db.close()
        if user is None or not user.is_active:
            return None
        return AccessToken(
            token=token,
            client_id=payload.get("client_id", ""),
            scopes=payload.get("scopes", []),
            expires_at=payload.get("exp"),
            subject=payload["sub"],
        )

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        db = SessionLocal()
        try:
            row = db.get(OAuthRefreshToken, token.token)
            if row is not None:
                row.revoked = True
                db.commit()
        finally:
            db.close()


oauth_provider = BereBankOAuthProvider()
