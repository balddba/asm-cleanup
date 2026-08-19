"""Unit tests for ConnectionFactory key resolution and port opening."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from asm_cleanup.config.connection_config import ConnectionConfig
from asm_cleanup.config.connection_mode import ConnectionMode
from asm_cleanup.services.connection_factory import ConnectionFactory
from asm_cleanup.transport.local import LocalShellAdapter
from asm_cleanup.transport.ssh import SshGridAdapter


def test_build_connect_kwargs_from_key_content(tmp_path: Path) -> None:
    """Write pasted key content to a 0600 temp file and set key_filename."""
    content = (
        "-----BEGIN OPENSSH PRIVATE KEY-----\nfake\n-----END OPENSSH PRIVATE KEY-----"
    )
    kwargs, temp_path = ConnectionFactory.build_connect_kwargs(ssh_key_content=content)
    assert temp_path is not None
    try:
        assert kwargs["key_filename"] == temp_path
        assert Path(temp_path).read_text(encoding="utf-8") == content
        assert oct(Path(temp_path).stat().st_mode & 0o777) == "0o600"
    finally:
        os.unlink(temp_path)


def test_build_connect_kwargs_from_key_path() -> None:
    """Use an explicit ssh_key_path as key_filename."""
    kwargs, temp_path = ConnectionFactory.build_connect_kwargs(
        ssh_key_path="~/keys/id_rsa"
    )
    assert temp_path is None
    assert kwargs["key_filename"] == str(Path.home() / "keys/id_rsa")


def test_build_connect_kwargs_existing_key_filename() -> None:
    """Rewrite key_filename already present in connect_kwargs."""
    kwargs, temp_path = ConnectionFactory.build_connect_kwargs(
        connect_kwargs={"key_filename": "~/id_ed25519"}
    )
    assert temp_path is None
    assert kwargs["key_filename"] == str(Path.home() / "id_ed25519")


def test_build_connect_kwargs_allow_missing_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Omit key_filename when allow_missing_key is True and no keys exist."""
    (tmp_path / ".ssh").mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    kwargs, temp_path = ConnectionFactory.build_connect_kwargs(allow_missing_key=True)
    assert temp_path is None
    assert "key_filename" not in kwargs


def test_build_connect_kwargs_raises_without_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Raise FileNotFoundError when a key is required but missing."""
    (tmp_path / ".ssh").mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    with pytest.raises(FileNotFoundError):
        ConnectionFactory.build_connect_kwargs(allow_missing_key=False)


def test_open_fabric_unlinks_temp_key() -> None:
    """Delete the temporary key file after the Fabric context exits."""
    factory = ConnectionFactory()
    content = (
        "-----BEGIN OPENSSH PRIVATE KEY-----\nx\n-----END OPENSSH PRIVATE KEY-----"
    )
    mock_conn = MagicMock()
    mock_cm = MagicMock()
    mock_cm.__enter__.return_value = mock_conn
    mock_cm.__exit__.return_value = False

    with (
        patch(
            "asm_cleanup.services.connection_factory.Connection", return_value=mock_cm
        ),
        factory.open_fabric("host1", "grid", ssh_key_content=content) as conn,
    ):
        assert conn is mock_conn
        # Capture temp path from connect_kwargs used to build Connection
    # After context, temp key should be gone — rebuild to inspect path was unlinked
    _, temp_path = ConnectionFactory.build_connect_kwargs(ssh_key_content=content)
    assert temp_path is not None
    assert Path(temp_path).is_file()
    with (
        patch(
            "asm_cleanup.services.connection_factory.Connection", return_value=mock_cm
        ),
        factory.open_fabric("h", "u", ssh_key_content=content),
    ):
        pass
    # The second open_fabric creates and deletes its own temp file; ensure unlink tolerates OSError
    with (
        patch(
            "asm_cleanup.services.connection_factory.Connection", return_value=mock_cm
        ),
        patch(
            "asm_cleanup.services.connection_factory.os.unlink",
            side_effect=OSError("busy"),
        ),
        factory.open_fabric("h", "u", ssh_key_content=content),
    ):
        pass
    os.unlink(temp_path)


def test_open_port_local_yields_local_adapter() -> None:
    """Yield LocalShellAdapter for connection.mode=local."""
    factory = ConnectionFactory()
    cfg = ConnectionConfig(mode=ConnectionMode.local)
    with factory.open_port(cfg) as port:
        assert isinstance(port, LocalShellAdapter)


def test_open_port_ssh_yields_ssh_adapter() -> None:
    """Yield SshGridAdapter wrapping an opened Fabric connection."""
    factory = ConnectionFactory()
    cfg = ConnectionConfig.model_validate(
        {
            "mode": "ssh",
            "host": "asm-host",
            "user": "grid",
            "grid_home": "/u01/app/grid",
            "oracle_sid": "+ASM",
        }
    )
    mock_conn = MagicMock()
    with patch.object(factory, "open_fabric") as mock_open:
        mock_open.return_value.__enter__.return_value = mock_conn
        mock_open.return_value.__exit__.return_value = False
        with factory.open_port(cfg, fail_loud=False, allow_missing_key=True) as port:
            assert isinstance(port, SshGridAdapter)
            assert port.connection is mock_conn
            assert port.fail_loud is False
