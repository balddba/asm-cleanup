"""Unit tests for local and SSH asmcmd transport adapters."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from asm_cleanup.config.connection_config import ConnectionConfig
from asm_cleanup.transport.asm_cmd_error import AsmCmdError
from asm_cleanup.transport.local import LocalShellAdapter
from asm_cleanup.transport.ssh import SshGridAdapter


def _ssh_config(**kwargs: object) -> ConnectionConfig:
    """Build a minimal SSH ConnectionConfig.

    Args:
        **kwargs (object): Field overrides.

    Returns:
        ConnectionConfig: Validated SSH connection settings.
    """
    defaults: dict[str, object] = {
        "mode": "ssh",
        "host": "asm-host",
        "user": "grid",
        "grid_home": "/u01/app/grid",
        "oracle_sid": "+ASM",
    }
    defaults.update(kwargs)
    return ConnectionConfig.model_validate(defaults)


def test_local_run_argv_success() -> None:
    """Capture stdout/stderr from a successful local argv run."""
    adapter = LocalShellAdapter(fail_loud=True)
    completed = MagicMock()
    completed.stdout = "DATA/\n"
    completed.stderr = ""
    completed.returncode = 0
    with patch("asm_cleanup.transport.local.subprocess.run", return_value=completed):
        result = adapter.run_argv(["asmcmd", "ls", "+"])
    assert result.ok
    assert result.stdout == "DATA/\n"
    assert result.argv == ["asmcmd", "ls", "+"]


def test_local_run_argv_fail_loud_raises() -> None:
    """Raise AsmCmdError when fail_loud is True and exit is non-zero."""
    adapter = LocalShellAdapter(fail_loud=True)
    completed = MagicMock()
    completed.stdout = ""
    completed.stderr = "not found"
    completed.returncode = 127
    with (
        patch("asm_cleanup.transport.local.subprocess.run", return_value=completed),
        pytest.raises(AsmCmdError),
    ):
        adapter.run_argv(["asmcmd", "ls", "+"])


def test_local_run_argv_soft_failure() -> None:
    """Return a failed CommandResult when fail_loud is False."""
    adapter = LocalShellAdapter(fail_loud=False)
    completed = MagicMock()
    completed.stdout = None
    completed.stderr = None
    completed.returncode = 1
    with patch("asm_cleanup.transport.local.subprocess.run", return_value=completed):
        result = adapter.run_argv(["asmcmd", "ls", "+MISSING"])
    assert not result.ok
    assert result.stdout == ""
    assert result.stderr == ""


def test_local_run_shell_fail_loud_raises() -> None:
    """Raise AsmCmdError from run_shell when fail_loud is True."""
    adapter = LocalShellAdapter(fail_loud=True)
    completed = MagicMock()
    completed.stdout = ""
    completed.stderr = "fail"
    completed.returncode = 2
    with (
        patch("asm_cleanup.transport.local.subprocess.run", return_value=completed),
        pytest.raises(AsmCmdError),
    ):
        adapter.run_shell("false")


def test_local_run_shell_and_list_helpers() -> None:
    """run_shell records argv and list helpers delegate to run_argv."""
    adapter = LocalShellAdapter(fail_loud=False)
    completed = MagicMock()
    completed.stdout = "ok"
    completed.stderr = ""
    completed.returncode = 0
    with patch(
        "asm_cleanup.transport.local.subprocess.run", return_value=completed
    ) as mock_run:
        shell = adapter.run_shell("echo hi", argv=["custom", "argv"])
        assert shell.argv == ["custom", "argv"]
        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        assert args[:3] == ["bash", "--noprofile", "--norc"]

    with patch.object(adapter, "run_argv") as mock_argv:
        mock_argv.return_value = MagicMock()
        adapter.list_long("+DATA")
        adapter.list_disk_groups()
        mock_argv.assert_any_call(["asmcmd", "ls", "-l", "+DATA"])
        mock_argv.assert_any_call(["asmcmd", "ls", "+"])


def test_resolve_ssh_key_path_expands_home() -> None:
    """Expand ~/ and bare ~ in SSH key paths."""
    home = str(Path.home())
    assert SshGridAdapter.resolve_ssh_key_path("~/keys/id_rsa") == str(
        Path.home() / "keys/id_rsa"
    )
    assert SshGridAdapter.resolve_ssh_key_path("~") == home
    assert SshGridAdapter.resolve_ssh_key_path("~/") == home
    assert SshGridAdapter.resolve_ssh_key_path("/abs/key") == "/abs/key"


def test_merge_ssh_connect_kwargs_rewrites_existing_key() -> None:
    """Rewrite an explicit key_filename through resolve_ssh_key_path."""
    merged = SshGridAdapter.merge_ssh_connect_kwargs({"key_filename": "~/id_ed25519"})
    assert merged["key_filename"] == str(Path.home() / "id_ed25519")


def test_merge_ssh_connect_kwargs_discovers_default_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Discover a private key next to id_ed25519.pub under ~/.ssh."""
    ssh_dir = tmp_path / ".ssh"
    ssh_dir.mkdir()
    (ssh_dir / "id_ed25519.pub").write_text("ssh-ed25519 AAAA\n", encoding="utf-8")
    private = ssh_dir / "id_ed25519"
    private.write_text("PRIVATE", encoding="utf-8")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    merged = SshGridAdapter.merge_ssh_connect_kwargs({})
    assert merged["key_filename"] == str(private)


def test_merge_ssh_connect_kwargs_missing_private_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Raise when a public key exists without its private counterpart."""
    ssh_dir = tmp_path / ".ssh"
    ssh_dir.mkdir()
    (ssh_dir / "id_rsa.pub").write_text("ssh-rsa AAAA\n", encoding="utf-8")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    with pytest.raises(FileNotFoundError, match="private key"):
        SshGridAdapter.merge_ssh_connect_kwargs({})


def test_merge_ssh_connect_kwargs_no_keys_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Raise when ~/.ssh has no expected key pairs."""
    (tmp_path / ".ssh").mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    with pytest.raises(FileNotFoundError, match="No SSH key found"):
        SshGridAdapter.merge_ssh_connect_kwargs({})


def test_wrap_remote_grid_command_modes() -> None:
    """Cover asm_env_init, oraenv, simple-env, and passthrough wrappers."""
    init_cfg = _ssh_config(asm_env_init="export FOO=1")
    assert "export FOO=1\nls" in SshGridAdapter.wrap_remote_grid_command(init_cfg, "ls")

    ora_cfg = _ssh_config(use_oraenv=True, oracle_sid="+ASM1")
    wrapped = SshGridAdapter.wrap_remote_grid_command(ora_cfg, "asmcmd ls +")
    assert "ORAENV_ASK=NO" in wrapped
    assert "ORACLE_SID=" in wrapped and "+ASM1" in wrapped

    simple = _ssh_config(
        oracle_sid="+ASM",
        oracle_home="/u01/grid",
        oracle_base="/u01/app",
        use_oraenv=False,
        asm_env_init=None,
    )
    body = SshGridAdapter.wrap_remote_grid_command(simple, "asmcmd ls +")
    assert "ORACLE_HOME=" in body and "/u01/grid" in body
    assert "ORACLE_BASE" in body
    assert "LD_LIBRARY_PATH" in body

    bare = _ssh_config(oracle_sid=None)
    assert SshGridAdapter.wrap_remote_grid_command(bare, "echo hi") == "echo hi"


def test_ssh_asmcmd_bin_and_run_shell() -> None:
    """asmcmd_bin requires grid_home; run_shell maps Fabric results."""
    conn = MagicMock()
    fabric_res = MagicMock()
    fabric_res.ok = True
    fabric_res.exited = 0
    fabric_res.stdout = "DATA/\n"
    fabric_res.stderr = ""
    conn.run.return_value = fabric_res

    adapter = SshGridAdapter(_ssh_config(), conn, fail_loud=True)
    assert adapter.asmcmd_bin() == "/u01/app/grid/bin/asmcmd"

    no_home = _ssh_config(grid_home="/u01/app/grid")
    broken = ConnectionConfig.model_construct(
        mode=no_home.mode,
        host=no_home.host,
        user=no_home.user,
        grid_home=None,
        oracle_sid="+ASM",
    )
    with pytest.raises(RuntimeError, match="grid_home"):
        SshGridAdapter(broken, conn).asmcmd_bin()

    result = adapter.run_shell("asmcmd ls +", use_grid_env=True)
    assert result.ok
    assert "DATA/" in result.stdout

    conn.run.return_value = None
    soft = SshGridAdapter(_ssh_config(), conn, fail_loud=False)
    empty = soft.run_shell("true", use_grid_env=False)
    assert not empty.ok
    assert "no result" in empty.stderr


def test_ssh_run_argv_rewrites_asmcmd_and_list_helpers() -> None:
    """Replace leading asmcmd with grid_home binary path."""
    conn = MagicMock()
    fabric_res = MagicMock()
    fabric_res.ok = True
    fabric_res.exited = 0
    fabric_res.stdout = ""
    fabric_res.stderr = ""
    conn.run.return_value = fabric_res
    adapter = SshGridAdapter(_ssh_config(), conn, fail_loud=False)

    with patch.object(adapter, "run_shell", wraps=adapter.run_shell) as mock_shell:
        adapter.run_argv(["asmcmd", "ls", "-l", "+DATA"])
        script = mock_shell.call_args[0][0]
        assert "/u01/app/grid/bin/asmcmd" in script
        assert "ls" in script

    with patch.object(adapter, "run_argv") as mock_argv:
        mock_argv.return_value = MagicMock()
        adapter.list_long("+DATA/db")
        adapter.list_disk_groups()
        mock_argv.assert_any_call(["asmcmd", "ls", "-l", "+DATA/db"])
        mock_argv.assert_any_call(["asmcmd", "ls", "+"])
