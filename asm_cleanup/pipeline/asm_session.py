"""AsmSession: public library entry for walk / inventory / SQL emit."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Self

from fabric import Connection
from loguru import logger

from asm_cleanup.config.connection_config import ConnectionConfig
from asm_cleanup.config.move_policy import MovePolicy
from asm_cleanup.config.scope_config import ScopeConfig
from asm_cleanup.domain.alias_record import AliasRecord
from asm_cleanup.pipeline.pipeline_orchestrator import PipelineOrchestrator
from asm_cleanup.pipeline.walk_results import WalkResult
from asm_cleanup.services.connection_factory import ConnectionFactory
from asm_cleanup.transport.asm_cmd_port import AsmCmdPort
from asm_cleanup.walk.asm_inventory import AsmInventory


class AsmSession:
    """Session holding scope/policy, command port, and pipeline orchestrator.

    Attributes:
        scope (ScopeConfig | None): Walk path scope.
        move_policy (MovePolicy | None): SQL emit policy.
        connection (ConnectionConfig | None): Connection settings when opened via open().
        port (AsmCmdPort): Typed asmcmd adapter.
    """

    def __init__(
        self,
        *,
        port: AsmCmdPort,
        scope: ScopeConfig | None = None,
        move_policy: MovePolicy | None = None,
        connection: ConnectionConfig | None = None,
        database_filter: list[str] | tuple[str, ...] | None = None,
        debug: bool = False,
        _connection: Connection | None = None,
    ) -> None:
        """Initialize a session.

        Args:
            port (AsmCmdPort): Typed asmcmd adapter.
            scope (ScopeConfig | None): Walk path scope.
            move_policy (MovePolicy | None): SQL emit policy.
            connection (ConnectionConfig | None): Connection settings.
            database_filter (list[str] | tuple[str, ...] | None): Optional DB filter.
            debug (bool): Request debug-level messages (does not reconfigure handlers).
            _connection (Connection | None): Fabric connection owned by the session.
        """
        from asm_cleanup.logging_config import is_debug_enabled

        self._debug = is_debug_enabled(debug)
        self.scope = scope
        self.move_policy = move_policy
        self.connection = connection
        self.port = port
        self._database_filter = frozenset(database_filter) if database_filter else None
        self._connection = _connection
        self._orchestrator = PipelineOrchestrator(
            port=port,
            scope=scope,
            move_policy=move_policy,
            connection=connection,
            database_filter=database_filter,
        )
        if self._debug:
            logger.debug(
                "session init mode={!r} scope_databases={!r}",
                getattr(connection, "mode", None),
                getattr(scope, "databases", None) if scope else None,
            )

    @classmethod
    @contextmanager
    def open(
        cls,
        connection: ConnectionConfig,
        *,
        scope: ScopeConfig | None = None,
        move_policy: MovePolicy | None = None,
        databases: list[str] | tuple[str, ...] | None = None,
        debug: bool = False,
        connection_factory: ConnectionFactory | None = None,
    ) -> Iterator[Self]:
        """Open a session from connection settings (SSH or local).

        Args:
            connection (ConnectionConfig): SSH or local execution settings.
            scope (ScopeConfig | None): Walk path scope.
            move_policy (MovePolicy | None): SQL emit policy.
            databases (list[str] | tuple[str, ...] | None): Optional database filter.
            debug (bool): Enable debug logging.
            connection_factory (ConnectionFactory | None): Optional factory override.

        Yields:
            Iterator[Self]: Active session.

        Raises:
            FileNotFoundError: If the SSH key cannot be resolved.
        """
        factory = connection_factory or ConnectionFactory()
        with factory.open_port(connection) as port:
            fabric_conn = getattr(port, "connection", None)
            yield cls(
                port=port,
                scope=scope,
                move_policy=move_policy,
                connection=connection,
                database_filter=databases,
                debug=debug,
                _connection=fabric_conn
                if isinstance(fabric_conn, Connection)
                else None,
            )

    def walk(self, asm_path: str) -> AsmInventory:
        """Walk an ASM path into a structured inventory.

        Args:
            asm_path (str): ASM root path.

        Returns:
            AsmInventory: Walk inventory.
        """
        return self._orchestrator.walk_path(asm_path)

    def analyze(self, inventory: AsmInventory) -> list[AliasRecord]:
        """Extract alias records from a walk inventory.

        Args:
            inventory (AsmInventory): Walk inventory.

        Returns:
            list[AliasRecord]: Deduplicated alias mappings.
        """
        return inventory.extract_aliases()

    def emit_sql(
        self,
        inventory_or_aliases: AsmInventory | list[AliasRecord],
        *,
        policy: MovePolicy | None = None,
    ) -> str:
        """Emit OMF MOVE SQL from an inventory or alias list.

        Args:
            inventory_or_aliases (AsmInventory | list[AliasRecord]): Inventory or aliases.
            policy (MovePolicy | None): Override move policy.

        Returns:
            str: SQL script text.
        """
        if isinstance(inventory_or_aliases, AsmInventory):
            records = inventory_or_aliases.extract_aliases()
        else:
            records = inventory_or_aliases
        return self._orchestrator.emit_sql(records, policy=policy)

    def run(
        self,
        asm_path: str | None = None,
        *,
        do_walk: bool = True,
        do_analyze: bool = True,
        do_fix: bool = True,
        from_transcript: Path | None = None,
        outfile: Path | None = None,
        fixfile: Path | None = None,
        result_json: Path | None = None,
        max_workers: int | None = None,
    ) -> list[WalkResult]:
        """Run the pipeline for one explicit path or all resolved scope paths.

        Args:
            asm_path (str | None): Explicit path; omit to expand scope paths.
            do_walk (bool): Perform live walks.
            do_analyze (bool): Extract aliases.
            do_fix (bool): Emit SQL.
            from_transcript (Path | None): Offline transcript (single-path only).
            outfile (Path | None): Transcript path (single-path only).
            fixfile (Path | None): SQL path (single-path only).
            result_json (Path | None): JSON path (single-path only).
            max_workers (int | None): Thread pool size for multi-path walks.

        Returns:
            list[WalkResult]: One result per processed path.
        """
        return self._orchestrator.run(
            asm_path,
            do_walk=do_walk,
            do_analyze=do_analyze,
            do_fix=do_fix,
            from_transcript=from_transcript,
            outfile=outfile,
            fixfile=fixfile,
            result_json=result_json,
            max_workers=max_workers,
        )


__all__ = ["AsmSession"]
