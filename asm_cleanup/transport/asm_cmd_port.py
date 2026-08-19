"""Protocol for typed asmcmd operations."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from asm_cleanup.transport.command_result import CommandResult


@runtime_checkable
class AsmCmdPort(Protocol):
    """Typed asmcmd operations (local or SSH)."""

    def list_long(self, path: str) -> CommandResult:
        """Run `asmcmd ls -l <path>` and return the result.

        Args:
            path (str): ASM directory path.

        Returns:
            CommandResult: Command outcome (caller may raise_for_status).
        """
        ...

    def list_disk_groups(self) -> CommandResult:
        """Run `asmcmd ls +` and return the result.

        Returns:
            CommandResult: Command outcome.
        """
        ...
