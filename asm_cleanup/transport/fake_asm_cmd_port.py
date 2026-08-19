"""In-memory AsmCmdPort for unit tests."""

from __future__ import annotations

from asm_cleanup.transport.command_result import CommandResult


class FakeAsmCmdPort:
    """In-memory AsmCmdPort for unit tests.

    Attributes:
        responses (dict[tuple[str, ...], CommandResult]): Map of argv tuple → result.
        default (CommandResult | None): Fallback result when argv is unknown.
        shell_handler: Optional callback for run_shell scripts.
        shell_scripts (list[str]): Captured shell scripts (for assertions).
    """

    def __init__(
        self,
        responses: dict[tuple[str, ...], CommandResult] | None = None,
        *,
        default: CommandResult | None = None,
        shell_handler: object | None = None,
    ) -> None:
        """Initialize with optional canned responses.

        Args:
            responses (dict[tuple[str, ...], CommandResult] | None): Argv → result map.
            default (CommandResult | None): Fallback when argv is not in responses.
            shell_handler (object | None): Optional `(script, use_grid_env) -> CommandResult`.
        """
        self.responses = dict(responses or {})
        self.default = default
        self.calls: list[list[str]] = []
        self.shell_handler = shell_handler
        self.shell_scripts: list[str] = []

    def _run(self, argv: list[str]) -> CommandResult:
        """Look up a canned response for argv.

        Args:
            argv (list[str]): Command argv.

        Returns:
            CommandResult: Matching or default result.

        Raises:
            KeyError: If no response and no default is configured.
        """
        self.calls.append(list(argv))
        key = tuple(argv)
        if key in self.responses:
            return self.responses[key]
        if self.default is not None:
            return self.default
        raise KeyError(f"No FakeAsmCmdPort response for {argv!r}")

    def run_shell(
        self,
        script: str,
        *,
        use_grid_env: bool = True,
        argv: list[str] | None = None,
    ) -> CommandResult:
        """Return a canned shell result via shell_handler.

        Args:
            script (str): Shell script body.
            use_grid_env (bool): Whether Grid env wrapping was requested.
            argv (list[str] | None): Optional recorded argv (unused by fake).

        Returns:
            CommandResult: Handler result.

        Raises:
            KeyError: If no shell_handler is configured.
        """
        del argv
        self.shell_scripts.append(script)
        if self.shell_handler is None:
            raise KeyError(f"No FakeAsmCmdPort shell_handler for script={script!r}")
        return self.shell_handler(script, use_grid_env=use_grid_env)  # type: ignore[operator]

    def list_long(self, path: str) -> CommandResult:
        """Return a canned `ls -l` result.

        Args:
            path (str): ASM path.

        Returns:
            CommandResult: Canned result.
        """
        return self._run(["asmcmd", "ls", "-l", path])

    def list_disk_groups(self) -> CommandResult:
        """Return a canned `ls +` result.

        Returns:
            CommandResult: Canned result.
        """
        return self._run(["asmcmd", "ls", "+"])
