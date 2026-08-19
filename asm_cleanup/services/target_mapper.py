"""Map Target ORM rows to library ConnectionConfig."""

from __future__ import annotations

from asm_cleanup.config.connection_config import ConnectionConfig
from asm_cleanup.config.connection_mode import ConnectionMode
from asm_cleanup.db.target import Target


class TargetMapper:
    """Map Target ORM rows to library ConnectionConfig."""

    @staticmethod
    def to_connection_config(
        target: Target,
        *,
        grid_home: str,
        oracle_sid: str,
    ) -> ConnectionConfig:
        """Build a ConnectionConfig from a Target and discovered Grid settings.

        Args:
            target (Target): ORM target connection profile.
            grid_home (str): Discovered or overridden Grid home.
            oracle_sid (str): Discovered or overridden ASM SID.

        Returns:
            ConnectionConfig: SSH connection settings for SshGridAdapter.
        """
        connect_kwargs: dict[str, str] = {}
        if target.ssh_key_path:
            connect_kwargs["key_filename"] = target.ssh_key_path
        return ConnectionConfig(
            mode=ConnectionMode.ssh,
            host=target.host,
            user=target.user,
            grid_home=grid_home,
            oracle_sid=oracle_sid,
            oracle_home=grid_home,
            connect_kwargs=connect_kwargs,
        )
