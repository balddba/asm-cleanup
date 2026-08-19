"""Live progress reporting while walking ASM directories."""

from __future__ import annotations

import sys
from typing import TextIO

from loguru import logger


class WalkProgressReporter:
    """Report directory-walk progress on stderr (TTY line) or via loguru.

    On a TTY, updates a single carriage-return line so remote walks feel live
    without flooding the log. When stderr is not a TTY (piped/CI), emits
    periodic INFO lines instead.

    Attributes:
        root_path (str): ASM walk root shown in the progress prefix.
        stream (TextIO): Output stream for TTY progress (default stderr).
    """

    def __init__(
        self,
        root_path: str,
        *,
        stream: TextIO | None = None,
        log_every: int = 25,
    ) -> None:
        """Initialize the reporter for one walk root.

        Args:
            root_path (str): ASM walk root label.
            stream (TextIO | None): Progress stream; defaults to sys.stderr.
            log_every (int): Non-TTY INFO cadence (directories visited).
        """
        self.root_path = root_path
        self.stream = sys.stderr if stream is None else stream
        self.log_every = max(1, log_every)
        self.directories_visited = 0
        self._tty = hasattr(self.stream, "isatty") and self.stream.isatty()
        self._finished = False

    def __call__(self, directories_visited: int, path: str) -> None:
        """Handle a walker progress tick for one scanned directory.

        Args:
            directories_visited (int): Cumulative directories visited so far.
            path (str): ASM path just listed.
        """
        self.directories_visited = directories_visited
        if self._tty:
            suffix = path if len(path) <= 72 else f"…{path[-71:]}"
            line = f"\rWalking {self.root_path}: {directories_visited} dirs  {suffix}"
            self.stream.write(f"{line:<120}")
            self.stream.flush()
            return
        if directories_visited == 1 or directories_visited % self.log_every == 0:
            logger.info(
                "walking {}: {} directories (at {})",
                self.root_path,
                directories_visited,
                path,
            )

    def finish(self) -> None:
        """Close the TTY progress line or log the final directory count."""
        if self._finished:
            return
        self._finished = True
        if self._tty:
            done = f"\rWalking {self.root_path}: {self.directories_visited} dirs done"
            self.stream.write(f"{done:<120}\n")
            self.stream.flush()
            return
        if self.directories_visited:
            logger.info(
                "finished walking {}: {} directories",
                self.root_path,
                self.directories_visited,
            )
