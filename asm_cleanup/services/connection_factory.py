"""Factory for local and SSH asmcmd ports with shared key handling."""

from __future__ import annotations

import os
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager

from fabric import Connection
from loguru import logger

from asm_cleanup.config.connection_config import ConnectionConfig
from asm_cleanup.config.connection_mode import ConnectionMode
from asm_cleanup.transport.asm_cmd_port import AsmCmdPort
from asm_cleanup.transport.local import LocalShellAdapter
from asm_cleanup.transport.ssh import SshGridAdapter


class ConnectionFactory:
    """Create AsmCmdPort adapters for local or SSH execution.

    Owns Fabric connection lifecycle and SSH private-key temp files.
    """

    @staticmethod
    def build_connect_kwargs(
        *,
        connect_kwargs: dict[str, str] | None = None,
        ssh_key_path: str | None = None,
        ssh_key_content: str | None = None,
        allow_missing_key: bool = False,
    ) -> tuple[dict[str, str], str | None]:
        """Resolve Fabric connect_kwargs and optional temp key path to delete.

        Args:
            connect_kwargs (dict[str, str] | None): Base Fabric kwargs.
            ssh_key_path (str | None): Optional path to a private key file.
            ssh_key_content (str | None): Optional pasted private key content.
            allow_missing_key (bool): When True, omit key_filename if none found.

        Returns:
            tuple[dict[str, str], str | None]: Kwargs and temp key path (or None).

        Raises:
            FileNotFoundError: If a key is required and cannot be resolved.
        """
        kwargs: dict[str, str] = dict(connect_kwargs or {})
        temp_key_path: str | None = None

        if ssh_key_content and ssh_key_content.strip():
            with tempfile.NamedTemporaryFile(
                mode="w", delete=False, suffix="_key"
            ) as temp_key:
                temp_key.write(ssh_key_content)
            os.chmod(temp_key.name, 0o600)
            temp_key_path = temp_key.name
            kwargs["key_filename"] = temp_key_path
            return kwargs, temp_key_path

        if ssh_key_path and ssh_key_path.strip():
            kwargs["key_filename"] = SshGridAdapter.resolve_ssh_key_path(ssh_key_path)
            return kwargs, None

        if kwargs.get("key_filename"):
            return SshGridAdapter.merge_ssh_connect_kwargs(kwargs), None

        try:
            return SshGridAdapter.merge_ssh_connect_kwargs(kwargs), None
        except FileNotFoundError:
            if allow_missing_key:
                return kwargs, None
            raise

    @contextmanager
    def open_fabric(
        self,
        host: str,
        user: str,
        *,
        connect_kwargs: dict[str, str] | None = None,
        ssh_key_path: str | None = None,
        ssh_key_content: str | None = None,
        allow_missing_key: bool = False,
    ) -> Iterator[Connection]:
        """Open a Fabric SSH connection with shared key resolution.

        Args:
            host (str): SSH hostname or IP.
            user (str): SSH username.
            connect_kwargs (dict[str, str] | None): Base Fabric kwargs.
            ssh_key_path (str | None): Optional private key path.
            ssh_key_content (str | None): Optional pasted private key content.
            allow_missing_key (bool): When True, connect without key_filename.

        Yields:
            Connection: Active Fabric connection.

        Raises:
            FileNotFoundError: If a key is required and cannot be resolved.
        """
        kwargs, temp_key_path = self.build_connect_kwargs(
            connect_kwargs=connect_kwargs,
            ssh_key_path=ssh_key_path,
            ssh_key_content=ssh_key_content,
            allow_missing_key=allow_missing_key,
        )
        logger.info("connecting to target host={} as user={}", host, user)
        try:
            with Connection(host=host, user=user, connect_kwargs=kwargs) as conn:
                yield conn
        finally:
            if temp_key_path is not None:
                try:
                    os.unlink(temp_key_path)
                except OSError:
                    pass

    @contextmanager
    def open_port(
        self,
        connection: ConnectionConfig,
        *,
        fail_loud: bool = True,
        ssh_key_content: str | None = None,
        ssh_key_path: str | None = None,
        allow_missing_key: bool = False,
    ) -> Iterator[AsmCmdPort]:
        """Open a typed asmcmd port for the given connection settings.

        Args:
            connection (ConnectionConfig): Local or SSH execution settings.
            fail_loud (bool): Raise on non-zero asmcmd exit when True.
            ssh_key_content (str | None): Optional pasted key overriding connect_kwargs.
            ssh_key_path (str | None): Optional key path overriding connect_kwargs.
            allow_missing_key (bool): When True, SSH may proceed without a key file.

        Yields:
            AsmCmdPort: LocalShellAdapter or SshGridAdapter.

        Raises:
            FileNotFoundError: If SSH key resolution fails when required.
        """
        if connection.mode is ConnectionMode.local:
            yield LocalShellAdapter()
            return

        assert connection.host is not None
        assert connection.user is not None
        with self.open_fabric(
            connection.host,
            connection.user,
            connect_kwargs=connection.connect_kwargs,
            ssh_key_path=ssh_key_path,
            ssh_key_content=ssh_key_content,
            allow_missing_key=allow_missing_key,
        ) as conn:
            yield SshGridAdapter(connection, conn, fail_loud=fail_loud)
