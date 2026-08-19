"""Remote asmcmd adapter via Fabric SSH and Grid env wrapping."""

from __future__ import annotations

import shlex
from pathlib import Path

from fabric import Connection
from loguru import logger

from asm_cleanup.config.connection_config import ConnectionConfig
from asm_cleanup.transport.command_result import CommandResult


class SshGridAdapter:
    """Execute asmcmd on a remote Grid host via Fabric.

    Attributes:
        connection_config (ConnectionConfig): SSH + Grid env settings.
        connection (Connection): Active Fabric connection.
        fail_loud (bool): When True, raise_for_status on each typed op.
    """

    def __init__(
        self,
        connection_config: ConnectionConfig,
        connection: Connection,
        *,
        fail_loud: bool = True,
    ) -> None:
        """Initialize the SSH adapter.

        Args:
            connection_config (ConnectionConfig): Profile with grid_home and env settings.
            connection (Connection): Open Fabric connection.
            fail_loud (bool): Raise AsmCmdError on non-zero exit when True.
        """
        self.connection_config = connection_config
        self.connection = connection
        self.fail_loud = fail_loud

    @staticmethod
    def resolve_ssh_key_path(path: str) -> str:
        """Return a concrete key path for Fabric (Paramiko does not expand `~` reliably).

        Args:
            path (str): Path from YAML or discovery.

        Returns:
            str: Absolute path when `~/` was used, otherwise path.strip().
        """
        p = path.strip()
        if p.startswith("~/"):
            rest = p[2:]
            return str(Path.home() / rest) if rest else str(Path.home())
        if p == "~":
            return str(Path.home())
        return p

    @staticmethod
    def merge_ssh_connect_kwargs(connect_kwargs: dict[str, str]) -> dict[str, str]:
        """Fill `key_filename` from the user's `.ssh` dir when omitted in YAML.

        Args:
            connect_kwargs (dict[str, str]): Fabric connect_kwargs from the connection profile.

        Returns:
            dict[str, str]: Copy of kwargs, possibly with key_filename added or rewritten.

        Raises:
            FileNotFoundError: If no suitable public/private key pair is found.
        """
        merged = dict(connect_kwargs)
        if merged.get("key_filename"):
            merged["key_filename"] = SshGridAdapter.resolve_ssh_key_path(
                str(merged["key_filename"])
            )
            return merged
        ssh_dir = Path.home() / ".ssh"
        for pub_name in ("id_ed25519.pub", "id_rsa.pub"):
            pub = ssh_dir / pub_name
            if not pub.is_file():
                continue
            private = pub.with_suffix("")
            if private.is_file():
                merged["key_filename"] = str(private)
                return merged
            raise FileNotFoundError(
                f"SSH public key exists at {pub} but private key {private} is missing"
            )
        raise FileNotFoundError(
            f"No SSH key found under {ssh_dir}: expected id_ed25519.pub or id_rsa.pub"
        )

    @staticmethod
    def wrap_remote_grid_command(connection: ConnectionConfig, cmd: str) -> str:
        """Build a shell script that runs `cmd` in the host's Grid/ASM environment.

        Args:
            connection (ConnectionConfig): Connection profile with Grid/ASM env settings.
            cmd (str): Shell command to execute after environment setup.

        Returns:
            str: Script with initialization followed by `cmd`.
        """
        cmd = cmd.strip()

        if connection.asm_env_init:
            return f"{connection.asm_env_init.strip()}\n{cmd}"

        if connection.use_oraenv:
            assert connection.oracle_sid is not None
            sid = shlex.quote(connection.oracle_sid)
            op = shlex.quote(connection.oraenv_path)
            # oraenv often prints a colored SID table to stdout; keep it out of captures.
            return f"export ORACLE_SID={sid}\nexport ORAENV_ASK=NO\n. {op} >/dev/null\n{cmd}"

        if connection.oracle_sid:
            oh_raw = (connection.oracle_home or connection.grid_home or "").rstrip("/")
            oh = shlex.quote(oh_raw)
            sid = shlex.quote(connection.oracle_sid)
            lines = [
                f"export ORACLE_HOME={oh}",
                f"export ORACLE_SID={sid}",
            ]
            if connection.oracle_base:
                lines.append(
                    f"export ORACLE_BASE={shlex.quote(connection.oracle_base)}"
                )
            lines.append("export PATH=$ORACLE_HOME/bin:$PATH")
            lines.append(
                "export LD_LIBRARY_PATH=$ORACLE_HOME/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
            )
            lines.append(cmd)
            return "\n".join(lines)

        return cmd

    def asmcmd_bin(self) -> str:
        """Return the absolute path to asmcmd under grid_home.

        Returns:
            str: Full path to the asmcmd binary.

        Raises:
            RuntimeError: If grid_home is not set.
        """
        grid_home = self.connection_config.grid_home
        if not grid_home:
            raise RuntimeError("asmcmd_bin requires connection.grid_home")
        return f"{grid_home.rstrip('/')}/bin/asmcmd"

    def run_argv(self, argv: list[str]) -> CommandResult:
        """Run argv remotely inside the Grid environment wrapper.

        Args:
            argv (list[str]): Argument vector (typically starts with `asmcmd`).

        Returns:
            CommandResult: Captured stdout/stderr/exit_code.
        """
        remote_argv = list(argv)
        if remote_argv and remote_argv[0] == "asmcmd":
            remote_argv[0] = self.asmcmd_bin()
        cmd = " ".join(shlex.quote(part) for part in remote_argv)
        return self.run_shell(cmd, use_grid_env=True, argv=list(argv))

    def run_shell(
        self,
        script: str,
        *,
        use_grid_env: bool = True,
        argv: list[str] | None = None,
    ) -> CommandResult:
        """Run a shell script remotely, optionally inside the Grid env wrapper.

        Args:
            script (str): Shell script body.
            use_grid_env (bool): When True, wrap with Grid/ASM environment setup.
            argv (list[str] | None): Optional argv recorded on CommandResult.

        Returns:
            CommandResult: Captured stdout/stderr/exit_code.
        """
        body = script.strip()
        if use_grid_env:
            body = self.wrap_remote_grid_command(self.connection_config, body)
        # Non-login, non-rc shell: login profiles often print colored banners to stdout.
        wrapped = f"bash --noprofile --norc -c {shlex.quote(body)}"
        recorded = list(argv) if argv is not None else ["bash", "-c", script]
        logger.debug(
            "remote run_shell use_grid_env={} script_lines={}",
            use_grid_env,
            body.count("\n") + 1,
        )
        result = self.connection.run(wrapped, hide=True, warn=True)
        if result is None:
            outcome = CommandResult(
                argv=recorded,
                stdout="",
                stderr="remote run returned no result",
                exit_code=1,
            )
        else:
            exit_code = int(getattr(result, "exited", None) or (0 if result.ok else 1))
            outcome = CommandResult(
                argv=recorded,
                stdout=result.stdout or "",
                stderr=result.stderr or "",
                exit_code=exit_code,
            )
        if self.fail_loud:
            outcome.raise_for_status()
        return outcome

    def list_long(self, path: str) -> CommandResult:
        """Run remote `asmcmd ls -l <path>`.

        Args:
            path (str): ASM directory path.

        Returns:
            CommandResult: Command outcome.
        """
        return self.run_argv(["asmcmd", "ls", "-l", path])

    def list_disk_groups(self) -> CommandResult:
        """Run remote `asmcmd ls +`.

        Returns:
            CommandResult: Command outcome.
        """
        return self.run_argv(["asmcmd", "ls", "+"])
