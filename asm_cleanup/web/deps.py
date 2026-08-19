"""FastAPI dependency providers for database sessions and JWT auth."""

from __future__ import annotations

from collections.abc import Generator
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from asm_cleanup.auth import AuthSettings, JwtTokenService
from asm_cleanup.auth.ssh_key_store import SshKeyStore, ssh_key_store_from_env
from asm_cleanup.db import DbManager

db_manager = DbManager()
_bearer_scheme = HTTPBearer(auto_error=False)
_ssh_key_store: SshKeyStore | None = None


def get_db() -> Generator[Session]:
    """Yield a SQLAlchemy session for the request lifetime.

    Yields:
        Session: Active database session.
    """
    with db_manager.session() as session:
        yield session


def get_auth_settings() -> AuthSettings:
    """Load auth settings from the process environment.

    Returns:
        AuthSettings: Validated auth configuration.

    Raises:
        ValueError: If required auth environment variables are missing.
    """
    return AuthSettings.from_env()


def get_ssh_key_store() -> SshKeyStore:
    """Return the process-wide encrypted SSH key store.

    Returns:
        SshKeyStore: Cryptfile (or memory) store loaded from the environment.

    Raises:
        ValueError: If ASM_CLEANUP_KEYRING_KEY is missing or weak.
    """
    global _ssh_key_store
    if _ssh_key_store is None:
        _ssh_key_store = ssh_key_store_from_env()
    return _ssh_key_store


def get_jwt_service(
    settings: Annotated[AuthSettings, Depends(get_auth_settings)],
) -> JwtTokenService:
    """Build a JwtTokenService from auth settings.

    Args:
        settings (AuthSettings): Injected auth configuration.

    Returns:
        JwtTokenService: Token issue/decode service.
    """
    return JwtTokenService(settings)


def require_auth(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None,
        Depends(_bearer_scheme),
    ],
    jwt_service: Annotated[JwtTokenService, Depends(get_jwt_service)],
) -> dict:
    """Require a valid Bearer JWT on protected API routes.

    Args:
        credentials (HTTPAuthorizationCredentials | None): Parsed Authorization header.
        jwt_service (JwtTokenService): Token validation service.

    Returns:
        dict: Decoded JWT claims.

    Raises:
        HTTPException: 401 when the header is missing or the token is invalid.
    """
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=401,
            detail="Missing or invalid Authorization header",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        return jwt_service.decode_token(credentials.credentials)
    except jwt.PyJWTError as exc:
        raise HTTPException(
            status_code=401,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
