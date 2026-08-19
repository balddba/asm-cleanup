"""Serialize ORM rows into public API response models."""

from __future__ import annotations

from asm_cleanup.auth.ssh_key_store import SshKeyStore, target_has_pasted_ssh_key
from asm_cleanup.db import Target
from asm_cleanup.schemas.target_base import TargetResponse


def target_to_response(target: Target, store: SshKeyStore) -> TargetResponse:
    """Serialize a Target ORM row without exposing private key material.

    Args:
        target (Target): Stored target connection profile.
        store (SshKeyStore): Encrypted store for pasted SSH keys.

    Returns:
        TargetResponse: Public target fields plus has_ssh_key.
    """
    return TargetResponse(
        id=target.id,
        name=target.name,
        host=target.host,
        user=target.user,
        ssh_key_path=target.ssh_key_path,
        has_ssh_key=target_has_pasted_ssh_key(target, store),
        grid_home=target.grid_home,
        oracle_sid=target.oracle_sid,
        destination_disk_group=target.destination_disk_group,
        move_online=bool(target.move_online),
        created_at=target.created_at.isoformat(),
    )
