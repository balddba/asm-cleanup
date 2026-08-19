"""Pydantic schemas for web authentication requests and responses."""

from __future__ import annotations

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    """Login body containing the shared application password.

    Attributes:
        password (str): Shared password to exchange for a bearer token.
    """

    model_config = {"extra": "forbid"}

    password: str = Field(..., min_length=1)


class TokenResponse(BaseModel):
    """Successful login response with a bearer access token.

    Attributes:
        access_token (str): Signed JWT for subsequent API calls.
        token_type (str): Token type; always bearer.
    """

    model_config = {"extra": "forbid"}

    access_token: str
    token_type: str = "bearer"
