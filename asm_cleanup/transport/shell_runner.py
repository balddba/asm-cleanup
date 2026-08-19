"""Protocol for running shell scripts locally or over SSH."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from asm_cleanup.transport.command_result import CommandResult


@runtime_checkable
class ShellRunner(Protocol):
    """Run shell scripts locally or over SSH (srvctl, sqlplus, etc.)."""

    def run_shell(
        self,
        script: str,
        *,
        use_grid_env: bool = True,
        argv: list[str] | None = None,
    ) -> CommandResult:
        """Execute a shell script and return the result.

        Args:
            script (str): Shell script body (executed via bash -c).
            use_grid_env (bool): When True, wrap with Grid/ASM env (SSH).
            argv (list[str] | None): Optional argv recorded on CommandResult.

        Returns:
            CommandResult: Command outcome.
        """
        ...
