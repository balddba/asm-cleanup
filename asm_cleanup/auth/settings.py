"""Environment-backed authentication settings for the web API."""

from __future__ import annotations

import os

from pydantic import BaseModel, Field, ValidationError, field_validator

JWT_SECRET_MIN_BYTES = 32
PASSWORD_MIN_LENGTH = 8

_PLACEHOLDER_VALUES = frozenset(
    {
        "change-me",
        "changeme",
        "password",
        "admin",
        "secret",
    }
)


def is_placeholder_secret(value: str) -> bool:
    """Return True when value is a documented example or trivial placeholder.

    Args:
        value (str): Candidate password or signing secret.

    Returns:
        bool: True when the value must be rejected at startup.
    """
    folded = value.strip().casefold()
    if folded.startswith("change-me"):
        return True
    return folded in _PLACEHOLDER_VALUES


def reject_placeholder_secret(value: str, env_name: str) -> str:
    """Reject documented placeholders copied from .env.example.

    Args:
        value (str): Candidate secret.
        env_name (str): Environment variable name for the error message.

    Returns:
        str: The original value when it is not a placeholder.

    Raises:
        ValueError: If value is a placeholder.
    """
    if is_placeholder_secret(value):
        raise ValueError(
            f"{env_name} is a placeholder; run ./scripts/setup_env.sh to generate secrets"
        )
    return value


def require_min_bytes(value: str, env_name: str, minimum: int) -> str:
    """Require a UTF-8 byte length for HMAC-style secrets.

    Args:
        value (str): Candidate secret.
        env_name (str): Environment variable name for the error message.
        minimum (int): Minimum UTF-8 byte length.

    Returns:
        str: The original value when it meets the minimum.

    Raises:
        ValueError: If the encoded value is shorter than minimum.
    """
    if len(value.encode("utf-8")) < minimum:
        raise ValueError(f"{env_name} must be at least {minimum} bytes")
    return value


class AuthSettings(BaseModel):
    """Single-password JWT auth settings loaded from the process environment.

    Attributes:
        password (str): Shared login password from ASM_CLEANUP_PASSWORD.
        jwt_secret (str): HS256 signing secret from ASM_CLEANUP_JWT_SECRET.
        jwt_ttl_seconds (int): Access token lifetime in seconds.
    """

    model_config = {"extra": "forbid"}

    password: str = Field(..., min_length=PASSWORD_MIN_LENGTH)
    jwt_secret: str = Field(..., min_length=1)
    jwt_ttl_seconds: int = Field(default=86400, gt=0)

    @field_validator("password")
    @classmethod
    def password_not_placeholder(cls, value: str) -> str:
        """Reject placeholder login passwords.

        Args:
            value (str): Password from the environment.

        Returns:
            str: Validated password.

        Raises:
            ValueError: If the password is a placeholder.
        """
        return reject_placeholder_secret(value, "ASM_CLEANUP_PASSWORD")

    @field_validator("jwt_secret")
    @classmethod
    def jwt_secret_strong(cls, value: str) -> str:
        """Reject placeholder or short JWT signing secrets.

        Args:
            value (str): JWT secret from the environment.

        Returns:
            str: Validated secret.

        Raises:
            ValueError: If the secret is a placeholder or shorter than 32 bytes.
        """
        reject_placeholder_secret(value, "ASM_CLEANUP_JWT_SECRET")
        return require_min_bytes(value, "ASM_CLEANUP_JWT_SECRET", JWT_SECRET_MIN_BYTES)

    @classmethod
    def from_env(cls) -> AuthSettings:
        """Load auth settings from environment variables.

        Returns:
            AuthSettings: Validated settings instance.

        Raises:
            ValueError: If password or jwt_secret is missing, weak, or a placeholder.
        """
        password = os.environ.get("ASM_CLEANUP_PASSWORD", "").strip()
        jwt_secret = os.environ.get("ASM_CLEANUP_JWT_SECRET", "").strip()
        ttl_raw = os.environ.get("ASM_CLEANUP_JWT_TTL_SECONDS", "86400").strip()

        missing: list[str] = []
        if not password:
            missing.append("ASM_CLEANUP_PASSWORD")
        if not jwt_secret:
            missing.append("ASM_CLEANUP_JWT_SECRET")
        if missing:
            raise ValueError(
                "Missing required auth environment variable(s): " + ", ".join(missing)
            )

        try:
            jwt_ttl_seconds = int(ttl_raw)
        except ValueError as exc:
            raise ValueError(
                f"ASM_CLEANUP_JWT_TTL_SECONDS must be an integer (got {ttl_raw!r})"
            ) from exc

        try:
            return cls(
                password=password,
                jwt_secret=jwt_secret,
                jwt_ttl_seconds=jwt_ttl_seconds,
            )
        except ValidationError as exc:
            raise ValueError(str(exc)) from exc
