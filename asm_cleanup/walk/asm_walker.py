"""Recursive ASM directory walker using AsmCmdPort."""

from __future__ import annotations

from collections.abc import Callable

from loguru import logger

from asm_cleanup.transport.asm_cmd_port import AsmCmdPort
from asm_cleanup.walk.asm_inventory import AsmInventory
from asm_cleanup.walk.directory_listing import DirectoryListing

# Called after each directory listing as (directories_visited, path).
WalkProgressCallback = Callable[[int, str], None]


class AsmWalker:
    """Walk an ASM tree via typed asmcmd list operations.

    Attributes:
        port (AsmCmdPort): Command port (local, SSH, or fake).
        max_depth (int | None): Optional recursion depth limit (0 = root only).
    """

    def __init__(
        self,
        port: AsmCmdPort,
        *,
        max_depth: int | None = None,
    ) -> None:
        """Initialize the walker.

        Args:
            port (AsmCmdPort): Typed asmcmd port.
            max_depth (int | None): Max directory depth from root (None = unlimited).
        """
        self.port = port
        self.max_depth = max_depth

    def walk(
        self,
        root_path: str,
        *,
        on_scan: WalkProgressCallback | None = None,
    ) -> AsmInventory:
        """Recursively walk an ASM directory tree.

        Args:
            root_path (str): ASM directory root (e.g. `+DATA/MYDB`).
            on_scan (WalkProgressCallback | None): Optional (dirs_visited, path) callback.

        Returns:
            AsmInventory: Structured inventory of directory listings.
        """
        root = root_path.strip()
        directories: list[DirectoryListing] = []
        self._walk_one(root, directories, depth=0, on_scan=on_scan)
        return AsmInventory(root_path=root, directories=directories)

    def _walk_one(
        self,
        path: str,
        directories: list[DirectoryListing],
        *,
        depth: int,
        on_scan: WalkProgressCallback | None,
    ) -> None:
        """Walk one directory and recurse into trailing-slash children.

        Args:
            path (str): Current ASM directory.
            directories (list[DirectoryListing]): Mutable inventory buffer.
            depth (int): Current depth from the walk root.
            on_scan (WalkProgressCallback | None): Optional progress callback.
        """
        logger.debug("walking asm path={} depth={}", path, depth)
        long_result = self.port.list_long(path)
        long_lines = long_result.lines
        directories.append(DirectoryListing(path=path, long_lines=long_lines))
        if on_scan is not None:
            on_scan(len(directories), path)

        if self.max_depth is not None and depth >= self.max_depth:
            return

        subdirs: list[str] = []
        for line in long_lines:
            text = line.strip()
            if not text:
                continue
            parts = text.split()
            if not parts:
                continue
            last_part = parts[-1]
            if last_part.endswith("/") and "=>" not in text:
                subdirs.append(last_part[:-1])

        for subdir_name in subdirs:
            subdir = f"{path}/{subdir_name}"
            self._walk_one(
                subdir,
                directories,
                depth=depth + 1,
                on_scan=on_scan,
            )
