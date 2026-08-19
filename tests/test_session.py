"""Tests for AsmSession wiring and connection selection."""

from unittest.mock import MagicMock, patch

from asm_cleanup.config.connection_config import ConnectionConfig
from asm_cleanup.config.connection_mode import ConnectionMode
from asm_cleanup.config.move_policy import MovePolicy
from asm_cleanup.pipeline.asm_session import AsmSession
from asm_cleanup.transport.local import LocalShellAdapter
from asm_cleanup.transport.ssh import SshGridAdapter


def _local_connection() -> ConnectionConfig:
    """Build a minimal local connection config.

    Returns:
        ConnectionConfig: Local connection for session tests.
    """
    return ConnectionConfig(mode=ConnectionMode.local)


def _ssh_connection() -> ConnectionConfig:
    """Build a minimal SSH connection config.

    Returns:
        ConnectionConfig: SSH connection for session tests.
    """
    return ConnectionConfig(
        mode=ConnectionMode.ssh,
        host="grid.example.com",
        user="oracle",
        grid_home="/u01/app/grid",
    )


def test_open_local_uses_local_shell_adapter() -> None:
    """Open a local session with LocalShellAdapter."""
    with AsmSession.open(
        _local_connection(),
        move_policy=MovePolicy(destination_disk_group="+DATA"),
    ) as session:
        assert isinstance(session.port, LocalShellAdapter)
        assert session.connection is not None
        assert session.connection.mode is ConnectionMode.local


@patch("asm_cleanup.services.connection_factory.Connection")
def test_open_ssh_uses_ssh_grid_adapter(mock_connection: MagicMock) -> None:
    """Open an SSH session with SshGridAdapter wrapping Fabric Connection."""
    mock_connection.return_value.__enter__.return_value = MagicMock()
    with AsmSession.open(
        _ssh_connection(),
        move_policy=MovePolicy(destination_disk_group="+DATA"),
    ) as session:
        assert isinstance(session.port, SshGridAdapter)
        assert session.connection is not None
        assert session.connection.mode is ConnectionMode.ssh


def test_connection_factory_build_connect_kwargs_path() -> None:
    """Resolve an explicit SSH key path into Fabric connect_kwargs."""
    from asm_cleanup.services.connection_factory import ConnectionFactory

    kwargs, temp = ConnectionFactory.build_connect_kwargs(
        ssh_key_path="/tmp/id_test",
        allow_missing_key=True,
    )
    assert temp is None
    assert kwargs["key_filename"] == "/tmp/id_test"


def test_connection_factory_allow_missing_key() -> None:
    """Allow missing default keys when allow_missing_key is True."""
    from asm_cleanup.services.connection_factory import ConnectionFactory

    kwargs, temp = ConnectionFactory.build_connect_kwargs(allow_missing_key=True)
    assert temp is None
    assert "key_filename" not in kwargs or kwargs.get("key_filename")
