from __future__ import annotations

import shlex
from typing import Optional

from pydantic import BaseModel, Field, model_validator


class HostConfig(BaseModel):
    """Configuration for a Grid Infrastructure host with SSH connection and ASM settings.

    Args:
        host (str): SSH target hostname or IP address.
        user (str): SSH username for remote connections.
        grid_home (str): Grid Infrastructure home directory path.
        monitor_interval (int): Seconds between monitoring checks (default: 60).
        monitor_count (int): Number of monitoring iterations to perform (default: 1).
        connect_kwargs (dict[str, str]): Additional SSH connection parameters (e.g. key_filename).
        disk_groups (list[str]): ASM disk group names to process (e.g. ["+DATA", "+FRA"]).
        databases (list[str]): Database names associated with this host.
        default_asm_path (str | None): Default ASM path for walk operations when no path specified.
        oracle_sid (str | None): ASM SID for oraenv or simple export (e.g. "+ASM", "+ASM1").
        use_oraenv (bool): Source oraenv script to set Oracle environment variables (default: False).
        oraenv_path (str): Full path to oraenv script (default: "/usr/local/bin/oraenv").
        oracle_home (str | None): Grid Infrastructure ORACLE_HOME for simple-env mode.
        oracle_base (str | None): Oracle base directory for simple-env mode.
        asm_env_init (str | None): Custom shell fragment for ASM environment initialization.
        pdb_guid_map (dict[str, str]): Mapping from ASM PDB directory GUID to PDB name.
    """

    host: str
    user: str
    grid_home: str
    monitor_interval: int = 60
    monitor_count: int = 1
    connect_kwargs: dict[str, str] = Field(default_factory=dict)
    disk_groups: list[str] = Field(default_factory=list)
    databases: list[str] = Field(default_factory=list)
    #: If set, :meth:`AsmCleanup.run` may be called with no ``asm_path`` (walk starts here).
    default_asm_path: Optional[str] = None
    #: ASM SID passed to ``oraenv`` or used with the simple export block (e.g. ``+ASM``, ``+ASM1``).
    oracle_sid: Optional[str] = None
    #: If ``True`` (and ``asm_env_init`` is unset), remote commands prefix ``ORAENV_ASK=NO`` and
    #: ``. oraenv`` so ``ORACLE_HOME``, ``ORACLE_BASE``, and ``LD_LIBRARY_PATH`` match an
    #: interactive login (fixes ASMCMD-8102 when ``grid_home`` alone is not the real GI home).
    use_oraenv: bool = False
    #: Script sourced for ``use_oraenv`` (default is common ``root.sh`` location).
    oraenv_path: str = "/usr/local/bin/oraenv"
    #: Optional real GI ``ORACLE_HOME`` for simple-env mode; defaults to ``grid_home``.
    oracle_home: Optional[str] = None
    #: Optional ``ORACLE_BASE`` for simple-env mode (ignored when ``use_oraenv`` is used).
    oracle_base: Optional[str] = None
    #: If set, this shell fragment is run before each remote ``asmcmd`` line; ``use_oraenv`` and
    #: ``oracle_sid`` shortcuts are skipped. Multiline YAML is fine.
    asm_env_init: Optional[str] = None
    #: Map ASM PDB directory GUID (32 hex chars, as under ``+DG/DBNAME/<GUID>/DATAFILE``) to PDB
    #: name for ``ALTER SESSION SET CONTAINER`` in generated move SQL. Keys may be any case.
    pdb_guid_map: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _oraenv_requires_sid(self) -> HostConfig:
        # Ensure oracle_sid is provided when use_oraenv is enabled
        if self.use_oraenv and not self.oracle_sid:
            raise ValueError("use_oraenv requires oracle_sid (e.g. +ASM or +ASM1)")
        return self

    def wrap_remote_grid_command(self, cmd: str) -> str:
        """Wrap command with ASM environment initialization for remote execution.

        Args:
            cmd (str): Shell command to execute in the Grid/ASM environment.

        Returns:
            str: Complete shell script with environment setup followed by the command.

        Notes:
            Environment initialization follows this priority order:
            1. Custom asm_env_init script if provided
            2. oraenv script sourcing if use_oraenv is True
            3. Manual environment exports if oracle_sid is set
            4. No environment setup (returns command as-is)
        """
        cmd = cmd.strip()

        # Use custom environment initialization script if provided
        if self.asm_env_init:
            return f"{self.asm_env_init.strip()}\n{cmd}"

        # Use oraenv script to set Oracle environment variables
        if self.use_oraenv:
            assert self.oracle_sid is not None
            sid = shlex.quote(self.oracle_sid)
            op = shlex.quote(self.oraenv_path)
            return (
                f"export ORACLE_SID={sid}\n"
                f"export ORAENV_ASK=NO\n"
                f". {op}\n"
                f"{cmd}"
            )

        # Manual environment setup with explicit exports
        if self.oracle_sid:
            oh_raw = (self.oracle_home or self.grid_home).rstrip("/")
            oh = shlex.quote(oh_raw)
            sid = shlex.quote(self.oracle_sid)
            lines = [
                f"export ORACLE_HOME={oh}",
                f"export ORACLE_SID={sid}",
            ]
            if self.oracle_base:
                lines.append(f"export ORACLE_BASE={shlex.quote(self.oracle_base)}")
            lines.append("export PATH=$ORACLE_HOME/bin:$PATH")
            lines.append(
                "export LD_LIBRARY_PATH=$ORACLE_HOME/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
            )
            lines.append(cmd)
            return "\n".join(lines)

        # No environment setup needed, return command as-is
        return cmd
