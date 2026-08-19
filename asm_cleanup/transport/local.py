"""Local asmcmd adapter via subprocess argv lists (no shell=True)."""

from __future__ import annotations

import subprocess

from loguru import logger

from asm_cleanup.transport.command_result import CommandResult


class LocalShellAdapter:
    """Execute asmcmd from the local PATH using argv lists.

    Attributes:
        asmcmd (str): Binary name or path used as argv[0].
        fail_loud (bool): When True, raise_for_status on each typed op.
    """

    def __init__(self, *, asmcmd: str = "asmcmd", fail_loud: bool = True) -> None:
        """Initialize the local adapter.

        Args:
            asmcmd (str): Binary name or path (default: `asmcmd` on PATH).
            fail_loud (bool): Raise AsmCmdError on non-zero exit when True.
        """
        self.asmcmd = asmcmd
        self.fail_loud = fail_loud

    def run_argv(self, argv: list[str]) -> CommandResult:
        """Run argv locally and return a CommandResult.

        Args:
            argv (list[str]): Argument vector (no shell expansion).

        Returns:
            CommandResult: Captured stdout/stderr/exit_code.
        """
        logger.debug("local run argv={!r}", argv)
        completed = subprocess.run(
            argv,
            shell=False,
            text=True,
            capture_output=True,
            check=False,
        )
        result = CommandResult(
            argv=list(argv),
            stdout=completed.stdout or "",
            stderr=completed.stderr or "",
            exit_code=int(completed.returncode),
        )
        if self.fail_loud:
            result.raise_for_status()
        return result

    def run_shell(
        self,
        script: str,
        *,
        use_grid_env: bool = True,
        argv: list[str] | None = None,
    ) -> CommandResult:
        """Run a shell script locally via bash -c.

        Args:
            script (str): Shell script body.
            use_grid_env (bool): Unused locally (PATH is caller responsibility).
            argv (list[str] | None): Optional argv recorded on CommandResult.

        Returns:
            CommandResult: Captured stdout/stderr/exit_code.
        """
        del use_grid_env  # local PATH/env is already whatever the process has
        recorded = list(argv) if argv is not None else ["bash", "-c", script]
        logger.debug("local run_shell script_lines={}", script.count("\n") + 1)
        completed = subprocess.run(
            ["bash", "--noprofile", "--norc", "-c", script],
            shell=False,
            text=True,
            capture_output=True,
            check=False,
        )
        result = CommandResult(
            argv=recorded,
            stdout=completed.stdout or "",
            stderr=completed.stderr or "",
            exit_code=int(completed.returncode),
        )
        if self.fail_loud:
            result.raise_for_status()
        return result

    def list_long(self, path: str) -> CommandResult:
        """Run `asmcmd ls -l <path>`.

        Args:
            path (str): ASM directory path.

        Returns:
            CommandResult: Command outcome.
        """
        return self.run_argv([self.asmcmd, "ls", "-l", path])

    def list_disk_groups(self) -> CommandResult:
        """Run `asmcmd ls +`.

        Returns:
            CommandResult: Command outcome.
        """
        return self.run_argv([self.asmcmd, "ls", "+"])
