"""Encrypted cryptfile storage for pasted SSH private keys."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Protocol

from loguru import logger

from asm_cleanup.auth.settings import (
    JWT_SECRET_MIN_BYTES,
    reject_placeholder_secret,
    require_min_bytes,
)
from asm_cleanup.db.target import Target

KEYRING_KEY_ENV = "ASM_CLEANUP_KEYRING_KEY"
KEYRING_FILE_ENV = "ASM_CLEANUP_KEYRING_FILE"
KEYRING_BACKEND_ENV = "ASM_CLEANUP_KEYRING_BACKEND"
DEFAULT_KEYRING_FILENAME = "ssh_keys.cryptfile.cfg"
_SERVICE_NAME = "asm-cleanup"
_MEMORY_SECRETS: dict[str, str] = {}


class SshKeyStore(Protocol):
    """Store and retrieve pasted SSH private keys for a target id."""

    def get(self, target_id: int) -> str | None:
        """Return stored PEM for a target, or None.

        Args:
            target_id (int): Target primary key.

        Returns:
            str | None: Private key PEM, or None when absent.
        """

    def set(self, target_id: int, pem: str) -> None:
        """Persist PEM for a target.

        Args:
            target_id (int): Target primary key.
            pem (str): Private key content.
        """

    def delete(self, target_id: int) -> None:
        """Remove stored PEM for a target if present.

        Args:
            target_id (int): Target primary key.
        """

    def has(self, target_id: int) -> bool:
        """Return True when a PEM is stored for the target.

        Args:
            target_id (int): Target primary key.

        Returns:
            bool: True when a key is stored.
        """


class MemorySshKeyStore:
    """Process-local store used by unit tests (not for production)."""

    def get(self, target_id: int) -> str | None:
        """Return an in-memory PEM for a target.

        Args:
            target_id (int): Target primary key.

        Returns:
            str | None: Stored private key, or None.
        """
        return _MEMORY_SECRETS.get(_username(target_id))

    def set(self, target_id: int, pem: str) -> None:
        """Store a PEM in the process-local map.

        Args:
            target_id (int): Target primary key.
            pem (str): Private key content.
        """
        _MEMORY_SECRETS[_username(target_id)] = pem

    def delete(self, target_id: int) -> None:
        """Drop an in-memory PEM.

        Args:
            target_id (int): Target primary key.
        """
        _MEMORY_SECRETS.pop(_username(target_id), None)

    def has(self, target_id: int) -> bool:
        """Return True when the process-local map has a PEM.

        Args:
            target_id (int): Target primary key.

        Returns:
            bool: True when a key is stored.
        """
        return _username(target_id) in _MEMORY_SECRETS


class CryptFileSshKeyStore:
    """Persist SSH private keys in a keyrings.cryptfile cryptfile.

    The cryptfile passphrase is ASM_CLEANUP_KEYRING_KEY. The file path is
    ASM_CLEANUP_KEYRING_FILE, or a file next to the SQLite database.
    """

    def __init__(self, *, keyring_key: str, file_path: Path) -> None:
        """Initialize a cryptfile-backed key store.

        Args:
            keyring_key (str): Passphrase that encrypts the cryptfile.
            file_path (Path): Cryptfile location on disk.
        """
        from keyrings.cryptfile.cryptfile import CryptFileKeyring

        self._file_path = file_path
        self._file_path.parent.mkdir(parents=True, exist_ok=True)
        keyring = CryptFileKeyring()
        keyring.file_path = str(self._file_path)
        keyring.keyring_key = keyring_key
        self._keyring = keyring

    def get(self, target_id: int) -> str | None:
        """Return a decrypted PEM from the cryptfile.

        Args:
            target_id (int): Target primary key.

        Returns:
            str | None: Stored private key, or None.
        """
        return self._keyring.get_password(_SERVICE_NAME, _username(target_id))

    def set(self, target_id: int, pem: str) -> None:
        """Encrypt and write a PEM into the cryptfile.

        Args:
            target_id (int): Target primary key.
            pem (str): Private key content.
        """
        self._keyring.set_password(_SERVICE_NAME, _username(target_id), pem)
        try:
            os.chmod(self._file_path, 0o600)
        except OSError:
            logger.debug("could not chmod cryptfile {}", self._file_path)

    def delete(self, target_id: int) -> None:
        """Remove a PEM from the cryptfile when present.

        Args:
            target_id (int): Target primary key.
        """
        try:
            self._keyring.delete_password(_SERVICE_NAME, _username(target_id))
        except Exception:  # noqa: BLE001 (missing entries vary by backend)
            logger.debug("no cryptfile entry for target_id={}", target_id)

    def has(self, target_id: int) -> bool:
        """Return True when the cryptfile has a PEM for the target.

        Args:
            target_id (int): Target primary key.

        Returns:
            bool: True when a key is stored.
        """
        return bool(self.get(target_id))


def _username(target_id: int) -> str:
    """Build the cryptfile username for a target.

    Args:
        target_id (int): Target primary key.

    Returns:
        str: Stable keyring username.
    """
    return f"target-{int(target_id)}"


def default_keyring_path() -> Path:
    """Resolve the default cryptfile path beside the SQLite database.

    Returns:
        Path: Cryptfile path.
    """
    explicit = os.environ.get(KEYRING_FILE_ENV, "").strip()
    if explicit:
        return Path(explicit)
    db_url = os.environ.get("DATABASE_URL", "sqlite:///asm_cleanup.db")
    prefix = "sqlite:///"
    if db_url.startswith(prefix):
        db_path = Path(db_url[len(prefix) :])
        if db_path.parent.as_posix() not in {"", "."}:
            return db_path.parent / DEFAULT_KEYRING_FILENAME
    return Path(DEFAULT_KEYRING_FILENAME)


def ssh_key_store_from_env() -> SshKeyStore:
    """Build the SSH key store from environment variables.

    Returns:
        SshKeyStore: Memory backend when ASM_CLEANUP_KEYRING_BACKEND=memory, else cryptfile.

    Raises:
        ValueError: If ASM_CLEANUP_KEYRING_KEY is missing, weak, or a placeholder.
    """
    backend = os.environ.get(KEYRING_BACKEND_ENV, "").strip().casefold()
    if backend == "memory":
        return MemorySshKeyStore()

    key = os.environ.get(KEYRING_KEY_ENV, "").strip()
    if not key:
        raise ValueError(
            f"Missing required auth environment variable: {KEYRING_KEY_ENV}"
        )
    reject_placeholder_secret(key, KEYRING_KEY_ENV)
    require_min_bytes(key, KEYRING_KEY_ENV, JWT_SECRET_MIN_BYTES)
    return CryptFileSshKeyStore(keyring_key=key, file_path=default_keyring_path())


def load_pasted_ssh_key(target: Target, store: SshKeyStore) -> str | None:
    """Load a pasted key from the cryptfile, migrating leftover SQLite plaintext.

    Args:
        target (Target): Target row that may still have ssh_key_content.
        store (SshKeyStore): Encrypted key store.

    Returns:
        str | None: Private key PEM, or None when none is stored.
    """
    if target.id is None:
        return None
    stored = store.get(int(target.id))
    if stored:
        return stored
    legacy = (target.ssh_key_content or "").strip()
    if not legacy:
        return None
    store.set(int(target.id), legacy)
    target.ssh_key_content = None
    logger.info("migrated plaintext SSH key for target_id={} into cryptfile", target.id)
    return legacy


def target_has_pasted_ssh_key(target: Target, store: SshKeyStore) -> bool:
    """Return True when a pasted key exists in the store or leftover SQLite column.

    Args:
        target (Target): Target row.
        store (SshKeyStore): Encrypted key store.

    Returns:
        bool: True when pasted key material is available.
    """
    if target.id is not None and store.has(int(target.id)):
        return True
    return bool((target.ssh_key_content or "").strip())
