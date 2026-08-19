"""HS256 JWT issue and decode helpers for web API auth."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import jwt

from asm_cleanup.auth.settings import AuthSettings


class JwtTokenService:
    """Issue and validate bearer access tokens for asm-cleanup.

    Attributes:
        _settings (AuthSettings): Auth configuration with jwt_secret and TTL.
    """

    def __init__(self, settings: AuthSettings) -> None:
        """Store auth settings used for JWT signing and validation.

        Args:
            settings (AuthSettings): Validated auth configuration.
        """
        self._settings = settings

    def issue_token(self) -> str:
        """Create a signed HS256 access token.

        Returns:
            str: Encoded JWT string.
        """
        now = datetime.now(UTC)
        payload = {
            "sub": "asm-cleanup",
            "iat": now,
            "exp": now + timedelta(seconds=self._settings.jwt_ttl_seconds),
        }
        return jwt.encode(
            payload,
            self._settings.jwt_secret,
            algorithm="HS256",
        )

    def decode_token(self, token: str) -> dict[str, Any]:
        """Decode and validate an HS256 access token.

        Args:
            token (str): Encoded JWT from the Authorization header.

        Returns:
            dict[str, Any]: Decoded token claims.

        Raises:
            jwt.PyJWTError: If the token is invalid, malformed, or expired.
        """
        return jwt.decode(
            token,
            self._settings.jwt_secret,
            algorithms=["HS256"],
        )
