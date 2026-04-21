"""Run ``asmcmd`` locally or over SSH with the same calling convention."""

from __future__ import annotations

import shlex
import subprocess
from collections.abc import Callable

from fabric import Connection

from .target_config import TargetConfig


def wrap_remote_grid_command(target_config: TargetConfig, cmd: str) -> str:
    """Build a shell script that runs ``cmd`` in the host's Grid/ASM environment.

    Args:
        target_config (TargetConfig): Target profile with Grid/ASM env settings.
        cmd (str): Shell command to execute after environment setup.

    Returns:
        str: Script with initialization followed by ``cmd``.

    Notes:
        Initialization order:
        1. ``asm_env_init`` when set
        2. ``oraenv`` when ``use_oraenv`` is True
        3. Simple ``ORACLE_HOME`` / ``ORACLE_SID`` exports when ``oracle_sid`` is set
        4. Otherwise ``cmd`` unchanged
    """
    cmd = cmd.strip()

    if target_config.asm_env_init:
        return f"{target_config.asm_env_init.strip()}\n{cmd}"

    if target_config.use_oraenv:
        assert target_config.oracle_sid is not None
        sid = shlex.quote(target_config.oracle_sid)
        op = shlex.quote(target_config.oraenv_path)
        return (
            f"export ORACLE_SID={sid}\n"
            f"export ORAENV_ASK=NO\n"
            f". {op}\n"
            f"{cmd}"
        )

    if target_config.oracle_sid:
        oh_raw = (target_config.oracle_home or target_config.grid_home).rstrip("/")
        oh = shlex.quote(oh_raw)
        sid = shlex.quote(target_config.oracle_sid)
        lines = [
            f"export ORACLE_HOME={oh}",
            f"export ORACLE_SID={sid}",
        ]
        if target_config.oracle_base:
            lines.append(f"export ORACLE_BASE={shlex.quote(target_config.oracle_base)}")
        lines.append("export PATH=$ORACLE_HOME/bin:$PATH")
        lines.append(
            "export LD_LIBRARY_PATH=$ORACLE_HOME/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
        )
        lines.append(cmd)
        return "\n".join(lines)

    return cmd


class AsmCmdClient:
    """Execute ``asmcmd`` via subprocess (local) or Fabric (remote).

    Remote mode uses :func:`wrap_remote_grid_command` and resolves the
    ``asmcmd`` binary under ``grid_home``. Local mode (no SSH session) invokes
    ``asmcmd`` from the shell ``PATH``.
    """

    def __init__(
        self,
        target_config: TargetConfig | None = None,
        *,
        connection: Connection | None = None,
        debug_log: Callable[[str], None] | None = None,
        debug: bool = False,
    ) -> None:
        self.target_config = target_config
        self.connection = connection
        self._debug_log = debug_log
        self._debug_enabled = bool(debug)

    def _debug(self, message: str) -> None:
        if self._debug_log is not None:
            self._debug_log(message)

    def asmcmd_bin(self) -> str:
        """Return the absolute path to ``asmcmd`` under ``grid_home``."""
        if self.target_config is None:
            raise RuntimeError("asmcmd_bin requires TargetConfig (grid_home).")
        gh = self.target_config.grid_home.rstrip("/")
        return f"{gh}/bin/asmcmd"

    def run_asmcmd(self, arguments: str) -> list[str]:
        """Run ``asmcmd`` with the given argument string (CLI text after ``asmcmd``).

        Examples:
            ``run_asmcmd("ls + 2>/dev/null")``
            ``run_asmcmd("ls -l +DATA/MYDB/DATAFILE 2>/dev/null")``

        Returns:
            list[str]: Stdout split into lines (same contract as :meth:`run_shell_command`).
        """
        return self.run_shell_command(f"asmcmd {arguments.lstrip()}")

    def run_local_shell_command(self, cmd: str) -> list[str]:
        """Execute a shell command locally and return stdout lines."""
        result = subprocess.run(
            cmd,
            shell=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        return result.stdout.splitlines()

    def run_remote_shell_command(self, cmd: str) -> list[str]:
        """Execute a shell command remotely via SSH and return stdout lines.

        Wraps the command with the host's Grid Infrastructure environment setup
        and runs it over Fabric. If the command starts with ``asmcmd `` (after
        leading whitespace), that prefix is replaced with the absolute binary
        path from ``grid_home``. On failure, returns an empty list.

        Raises:
            RuntimeError: If SSH connection or host profile is not configured.
        """
        if self.connection is None or self.target_config is None:
            raise RuntimeError("Remote command execution requires SSH connection and target profile.")

        stripped = cmd.lstrip()
        if stripped.startswith("asmcmd "):
            rest = stripped[len("asmcmd ") :]
            adapted = f"{self.asmcmd_bin()} {rest}"
        else:
            adapted = cmd

        script = wrap_remote_grid_command(self.target_config, adapted)
        wrapped = f"bash -lc {shlex.quote(script)}"

        self._debug(
            f"remote run: outer_cmd_chars={len(wrapped)}; script_lines={script.count(chr(10)) + 1}"
        )
        if self._debug_enabled:
            preview = script if len(script) <= 1200 else script[:1200] + "\n... [truncated]"
            self._debug(f"remote run script preview:\n{preview}")

        result = self.connection.run(wrapped, hide=True, warn=True)

        if result.failed:
            self._debug(f"remote run failed ok={result.ok!r} exited={getattr(result, 'exited', None)!r}")
            if result.stderr:
                self._debug(f"remote stderr:\n{result.stderr.strip()}")
            return []

        lines = (result.stdout or "").splitlines()
        self._debug(f"remote run ok, stdout_lines={len(lines)}")
        return lines

    def run_shell_command(self, cmd: str) -> list[str]:
        """Run a shell command locally or remotely depending on session mode.

        Valid modes: both ``connection`` and ``target_config`` set (SSH), or both
        unset (local). Same rules as :class:`~asm_cleanup.AsmCleanup` sessions.

        Raises:
            RuntimeError: If only one of connection / target_config is set.
        """
        if self.connection is not None and self.target_config is not None:
            return self.run_remote_shell_command(cmd)

        if self.connection is None and self.target_config is None:
            return self.run_local_shell_command(cmd)

        raise RuntimeError(
            "run_shell_command needs SSH (connection + target profile) or local mode (both unset)."
        )
