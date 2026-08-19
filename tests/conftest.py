"""Shared test environment for web auth and SSH key cryptfile."""

from __future__ import annotations

import os

os.environ.setdefault("ASM_CLEANUP_PASSWORD", "test-password")
os.environ.setdefault(
    "ASM_CLEANUP_JWT_SECRET",
    "test-jwt-secret-for-unit-tests-32b+",
)
os.environ.setdefault(
    "ASM_CLEANUP_KEYRING_KEY",
    "test-keyring-key-for-unit-tests-32b+",
)
os.environ.setdefault("ASM_CLEANUP_KEYRING_BACKEND", "memory")
os.environ.setdefault("ASM_CLEANUP_JWT_TTL_SECONDS", "86400")
