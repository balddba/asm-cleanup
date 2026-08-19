"""Outcome of one asmcmd (or shell) invocation."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, field_validator

from asm_cleanup.transport.text import strip_ansi


class CommandResult(BaseModel):
    """Outcome of one asmcmd (or shell) invocation.

    Attributes:
        argv (list[str]): Argument vector that was executed.
        stdout (str): Combined stdout text (ANSI stripped).
        stderr (str): Combined stderr text (ANSI stripped).
        exit_code (int): Process exit code.
    """

    model_config = ConfigDict(frozen=True)

    argv: list[str]
    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0

    @field_validator("stdout", "stderr", mode="before")
    @classmethod
    def _strip_ansi_fields(cls, value: object) -> object:
        """Strip ANSI escape sequences from captured command streams.

        Args:
            value (object): Raw field value before coercion.

        Returns:
            object: Cleaned string when input is str; otherwise unchanged.
        """
        if isinstance(value, str):
            return strip_ansi(value)
        return value

    @property
    def lines(self) -> list[str]:
        """Return stdout split into lines without trailing newlines.

        Returns:
            list[str]: Stdout lines.
        """
        if not self.stdout:
            return []
        return self.stdout.splitlines()

    @property
    def ok(self) -> bool:
        """Return True when the exit code is zero.

        Returns:
            bool: True if exit_code == 0.
        """
        return self.exit_code == 0

    def raise_for_status(self) -> None:
        """Raise AsmCmdError when the command failed.

        Raises:
            AsmCmdError: If exit_code is non-zero.
        """
        if not self.ok:
            from asm_cleanup.transport.asm_cmd_error import AsmCmdError

            raise AsmCmdError.from_result(self)
