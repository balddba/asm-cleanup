"""Constant-time password verification for single-password web auth."""

from __future__ import annotations

import secrets

from asm_cleanup.auth.settings import AuthSettings


class PasswordAuthenticator:
    """Verify a candidate password against configured AuthSettings.

    Attributes:
        _settings (AuthSettings): Auth configuration holding the expected password.
    """

    def __init__(self, settings: AuthSettings) -> None:
        """Store auth settings used for password comparison.

        Args:
            settings (AuthSettings): Validated auth configuration.
        """
        self._settings = settings

    def verify(self, password: str) -> bool:
        """Return True when password matches the configured password.

        Uses secrets.compare_digest for constant-time comparison.

        Args:
            password (str): Candidate password from the login request.

        Returns:
            bool: True if the password matches; otherwise False.
        """
        return secrets.compare_digest(password, self._settings.password)
