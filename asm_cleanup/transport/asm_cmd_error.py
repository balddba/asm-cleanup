"""Raised when an asmcmd invocation fails."""

from __future__ import annotations

from asm_cleanup.transport.command_result import CommandResult


class AsmCmdError(RuntimeError):
    """Raised when an asmcmd invocation fails.

    Attributes:
        argv (list[str]): Failed command argv.
        exit_code (int): Process exit code.
        stdout (str): Captured stdout.
        stderr (str): Captured stderr.
    """

    def __init__(
        self,
        message: str,
        *,
        argv: list[str],
        exit_code: int,
        stdout: str = "",
        stderr: str = "",
    ) -> None:
        """Initialize the error with command details.

        Args:
            message (str): Human-readable error summary.
            argv (list[str]): Failed command argv.
            exit_code (int): Process exit code.
            stdout (str): Captured stdout.
            stderr (str): Captured stderr.
        """
        super().__init__(message)
        self.argv = list(argv)
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = stderr

    @classmethod
    def from_result(cls, result: CommandResult) -> AsmCmdError:
        """Build an error from a failed CommandResult.

        Args:
            result (CommandResult): Failed command result.

        Returns:
            AsmCmdError: Structured error including remediation for ASMCMD-8102.
        """
        cmd = " ".join(result.argv)
        stderr = (result.stderr or "").strip()
        hint = ""
        if "ASMCMD-8102" in stderr or "ASMCMD-8102" in (result.stdout or ""):
            hint = (
                " Hint: non-interactive SSH often needs connection.use_oraenv + "
                "oracle_sid (or asm_env_init); see README."
            )
        message = (
            f"asmcmd failed (exit {result.exit_code}): {cmd}"
            + (f"; stderr: {stderr}" if stderr else "")
            + hint
        )
        return cls(
            message,
            argv=result.argv,
            exit_code=result.exit_code,
            stdout=result.stdout,
            stderr=result.stderr,
        )
