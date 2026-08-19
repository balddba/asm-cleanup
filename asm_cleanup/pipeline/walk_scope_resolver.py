"""Resolve which ASM roots to walk from a target scope."""

from __future__ import annotations

import fnmatch

from loguru import logger

from asm_cleanup.config.scope_config import ScopeConfig
from asm_cleanup.domain.paths import (
    asm_path_prefix_match,
    expand_asm_walk_paths,
    is_diskgroup_token,
    normalize_asm_path,
    normalize_disk_group_token,
)
from asm_cleanup.transport.asm_cmd_port import AsmCmdPort


class WalkScopeResolver:
    """Resolve walk roots from scope, filters, and optional DG discovery.

    Attributes:
        scope (ScopeConfig): Walk path scope.
        port (AsmCmdPort | None): Port used for disk-group discovery when needed.
        database_filter (frozenset[str] | None): Optional database name filter.
    """

    def __init__(
        self,
        scope: ScopeConfig,
        port: AsmCmdPort | None = None,
        *,
        database_filter: list[str] | tuple[str, ...] | None = None,
    ) -> None:
        """Initialize the scope resolver.

        Args:
            scope (ScopeConfig): Walk path scope.
            port (AsmCmdPort | None): Optional port for disk group discovery.
            database_filter (list[str] | tuple[str, ...] | None): Optional DB filter.
        """
        self.scope = scope
        self.port = port
        self._database_filter = frozenset(database_filter) if database_filter else None
        self._discovered_disk_groups: list[str] | None = None

    def databases(self) -> list[str]:
        """Return database names after applying the optional filter.

        Returns:
            list[str]: Filtered database names.

        Raises:
            ValueError: If the filter contains names not in scope.databases.
        """
        base = list(self.scope.databases)
        excludes = {db.casefold() for db in self.scope.exclude_databases}
        base = [db for db in base if db.casefold() not in excludes]
        if not self._database_filter:
            return base
        unknown = self._database_filter - set(self.scope.databases)
        if unknown:
            raise ValueError(
                f"Databases not defined for this target: {sorted(unknown)}; "
                f"allowed: {self.scope.databases}."
            )
        return [d for d in base if d in self._database_filter]

    @staticmethod
    def normalize_disk_group_token(dg: str) -> str:
        """Normalize a disk group token to `+NAME` uppercase form.

        Args:
            dg (str): Raw disk group token (optional leading `+`, trailing `/`, mixed case).

        Returns:
            str: Normalized disk group such as `+DATA`.
        """
        return normalize_disk_group_token(dg)

    @staticmethod
    def normalize_asm_path(path: str) -> str:
        """Normalize walk-root ASM paths for comparisons.

        Uppercases the disk group and intermediate directories; leaves the final segment
        unchanged. Do not use for MOVE DATAFILE source strings — Oracle matches
        dictionary casing. Use asm_path_prefix_match for comparisons.

        Args:
            path (str): ASM path to normalize.

        Returns:
            str: Normalized path, or unchanged if it does not start with `+`.
        """
        return normalize_asm_path(path)

    @staticmethod
    def asm_path_prefix_match(path: str, prefix: str) -> bool:
        """Return True if path starts with prefix (case-insensitive).

        Args:
            path (str): ASM path to test.
            prefix (str): Expected path prefix.

        Returns:
            bool: True when path is under prefix.
        """
        return asm_path_prefix_match(path, prefix)

    @staticmethod
    def expand_asm_walk_paths(
        disk_groups: list[str], databases: list[str]
    ) -> list[str]:
        """Build `+DISKGROUP/DATABASE` paths from disk groups and database names.

        Args:
            disk_groups (list[str]): Disk group tokens (configured or discovered).
            databases (list[str]): Database names in scope.

        Returns:
            list[str]: Deduplicated normalized ASM paths (order preserved).
        """
        return expand_asm_walk_paths(disk_groups, databases)

    @staticmethod
    def is_diskgroup_token(token: str) -> bool:
        """Return True when token looks like an asmcmd disk-group listing entry.

        Args:
            token (str): Raw line from `asmcmd ls +`.

        Returns:
            bool: True if the token matches a disk-group name pattern.
        """
        return is_diskgroup_token(token)

    def disk_groups(self) -> list[str]:
        """Return configured or discovered disk groups.

        Returns:
            list[str]: Names like `+DATA`, `+FRA`.

        Raises:
            RuntimeError: If discovery is required but no port is configured.
        """
        configured = list(self.scope.disk_groups)
        if configured:
            return [self.normalize_disk_group_token(dg) for dg in configured]
        return self.discover_disk_groups()

    def discover_disk_groups(self) -> list[str]:
        """Discover disk groups via `asmcmd ls +` when scope.disk_groups is empty.

        Returns:
            list[str]: Discovered disk group names with leading `+` and uppercase.

        Raises:
            RuntimeError: If no AsmCmdPort is available for discovery.
        """
        if self._discovered_disk_groups is not None:
            return list(self._discovered_disk_groups)
        if self.port is None:
            raise RuntimeError(
                "disk group discovery requires an AsmCmdPort "
                "(configure scope.disk_groups or open a session with a live port)."
            )
        result = self.port.list_disk_groups()
        discovered: list[str] = []
        seen: set[str] = set()
        for entry in result.lines:
            token = entry.strip()
            if not token or not self.is_diskgroup_token(token):
                continue
            token = token.rstrip("/")
            if not token:
                continue
            normalized = self.normalize_disk_group_token(token)
            key = normalized.casefold()
            if key in seen:
                continue
            seen.add(key)
            discovered.append(normalized)
        self._discovered_disk_groups = discovered
        logger.debug("discovered disk groups: {!r}", discovered)
        return list(discovered)

    def resolve_paths(self, asm_path: str | None = None) -> list[str]:
        """Resolve one or more ASM walk roots.

        Args:
            asm_path (str | None): Explicit path, or None to use default/expansion.

        Returns:
            list[str]: Normalized ASM paths to process.

        Raises:
            ValueError: If no paths can be resolved.
        """
        raw = (asm_path or "").strip()
        if raw:
            resolved = self.normalize_asm_path(raw) if raw.startswith("+") else raw
            paths = [resolved]
        else:
            default = (self.scope.default_asm_path or "").strip()
            if default:
                resolved = (
                    self.normalize_asm_path(default)
                    if default.startswith("+")
                    else default
                )
                paths = [resolved]
            else:
                paths = self.expand_asm_walk_paths(self.disk_groups(), self.databases())
                if not paths:
                    raise ValueError(
                        "No ASM paths to walk: target needs non-empty scope.databases "
                        "(after any --database filter) and either configured "
                        "scope.disk_groups or discoverable disk groups from ASM."
                    )

        if self.scope.exclude_paths:
            filtered: list[str] = []
            for p in paths:
                if not any(
                    fnmatch.fnmatchcase(p.casefold(), pattern.casefold())
                    for pattern in self.scope.exclude_paths
                ):
                    filtered.append(p)
            paths = filtered

        if not paths:
            raise ValueError(
                "No ASM paths to walk after applying exclude_paths filter."
            )
        return paths
