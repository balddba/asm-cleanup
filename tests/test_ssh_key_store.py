"""Tests for cryptfile SSH private-key storage."""

from __future__ import annotations

from pathlib import Path

import pytest

from asm_cleanup.auth.ssh_key_store import (
    CryptFileSshKeyStore,
    MemorySshKeyStore,
    load_pasted_ssh_key,
    ssh_key_store_from_env,
)
from asm_cleanup.db.target import Target


def test_memory_store_roundtrip() -> None:
    """Store, fetch, and delete a PEM in the process-local backend."""
    store = MemorySshKeyStore()
    store.set(99, "-----BEGIN OPENSSH PRIVATE KEY-----\nfake\n")
    assert store.has(99) is True
    assert store.get(99) is not None
    store.delete(99)
    assert store.has(99) is False


def test_ssh_key_store_from_env_memory(monkeypatch: pytest.MonkeyPatch) -> None:
    """Use the memory backend when ASM_CLEANUP_KEYRING_BACKEND=memory."""
    monkeypatch.setenv("ASM_CLEANUP_KEYRING_BACKEND", "memory")
    store = ssh_key_store_from_env()
    store.set(7, "pem-data")
    assert store.get(7) == "pem-data"
    store.delete(7)


def test_ssh_key_store_from_env_requires_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail fast when the cryptfile passphrase is missing."""
    monkeypatch.delenv("ASM_CLEANUP_KEYRING_BACKEND", raising=False)
    monkeypatch.delenv("ASM_CLEANUP_KEYRING_KEY", raising=False)
    with pytest.raises(ValueError, match="ASM_CLEANUP_KEYRING_KEY"):
        ssh_key_store_from_env()


def test_cryptfile_store_roundtrip(tmp_path: Path) -> None:
    """Encrypt and decrypt a pasted key through keyrings.cryptfile."""
    crypt = tmp_path / "ssh_keys.cryptfile.cfg"
    store = CryptFileSshKeyStore(
        keyring_key="test-keyring-key-for-unit-tests-32b+",
        file_path=crypt,
    )
    pem = (
        "-----BEGIN OPENSSH PRIVATE KEY-----\ncrypt\n-----END OPENSSH PRIVATE KEY-----"
    )
    store.set(3, pem)
    assert crypt.is_file()
    assert store.get(3) == pem
    store.delete(3)
    assert store.get(3) is None


def test_load_pasted_ssh_key_migrates_sqlite_plaintext() -> None:
    """Copy leftover SQLite PEM into the store and clear the column."""
    store = MemorySshKeyStore()
    target = Target(
        id=42,
        name="t",
        host="h",
        user="u",
        destination_disk_group="+DATA",
        ssh_key_content="-----BEGIN OPENSSH PRIVATE KEY-----\nlegacy\n",
    )
    pem = load_pasted_ssh_key(target, store)
    assert pem is not None
    assert "legacy" in pem
    assert target.ssh_key_content is None
    assert store.get(42) == pem
