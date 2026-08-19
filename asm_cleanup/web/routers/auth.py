"""Public authentication routes for password login."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from asm_cleanup.auth import AuthSettings, JwtTokenService, PasswordAuthenticator
from asm_cleanup.schemas.auth import LoginRequest, TokenResponse
from asm_cleanup.web.deps import get_auth_settings, get_jwt_service

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login", response_model=TokenResponse)
def login(
    payload: LoginRequest,
    settings: Annotated[AuthSettings, Depends(get_auth_settings)],
    jwt_service: Annotated[JwtTokenService, Depends(get_jwt_service)],
) -> TokenResponse:
    """Exchange the shared password for a bearer access token.

    Args:
        payload (LoginRequest): Login body with password.
        settings (AuthSettings): Injected auth configuration.
        jwt_service (JwtTokenService): Token issuer.

    Returns:
        TokenResponse: Bearer access token on success.

    Raises:
        HTTPException: 401 when the password does not match.
    """
    authenticator = PasswordAuthenticator(settings)
    if not authenticator.verify(payload.password):
        raise HTTPException(status_code=401, detail="Invalid password")
    return TokenResponse(access_token=jwt_service.issue_token())
