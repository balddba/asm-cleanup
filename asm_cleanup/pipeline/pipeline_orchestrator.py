"""Thin pipeline orchestrator: walk → inventory → emit → artifacts."""

from __future__ import annotations

import concurrent.futures
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from loguru import logger

from asm_cleanup.config.connection_config import ConnectionConfig
from asm_cleanup.config.move_policy import MovePolicy
from asm_cleanup.config.scope_config import ScopeConfig
from asm_cleanup.config.timezone import get_current_time
from asm_cleanup.domain.alias_record import AliasRecord
from asm_cleanup.domain.paths import normalize_asm_path
from asm_cleanup.pipeline.walk_results import WalkResult
from asm_cleanup.pipeline.walk_scope_resolver import WalkScopeResolver
from asm_cleanup.report.walk_progress_reporter import WalkProgressReporter
from asm_cleanup.sql.move_sql_emitter import MoveSqlEmitter
from asm_cleanup.sql.unmapped_pdb_guid_error import UnmappedPdbGuidError
from asm_cleanup.transport.asm_cmd_port import AsmCmdPort
from asm_cleanup.transport.pdb_guid_map_collector import PdbGuidMapCollector
from asm_cleanup.transport.pdb_guid_map_error import PdbGuidMapError
from asm_cleanup.transport.shell_runner import ShellRunner
from asm_cleanup.walk.asm_inventory import AsmInventory
from asm_cleanup.walk.asm_walker import AsmWalker, WalkProgressCallback
from asm_cleanup.walk.transcript import load_transcript, write_transcript


class PipelineOrchestrator:
    """Coordinate walk/analyze/sql phases without SSH or print formatting.

    Attributes:
        port (AsmCmdPort | None): Live asmcmd port (None for offline-only).
        scope (ScopeConfig | None): Walk path scope.
        move_policy (MovePolicy | None): SQL emit policy.
        connection (ConnectionConfig | None): Connection settings for PDB GUID fetch.
        database_filter (list[str] | None): Optional database filter.
    """

    def __init__(
        self,
        *,
        port: AsmCmdPort | None = None,
        scope: ScopeConfig | None = None,
        move_policy: MovePolicy | None = None,
        connection: ConnectionConfig | None = None,
        database_filter: list[str] | tuple[str, ...] | None = None,
    ) -> None:
        """Initialize the orchestrator.

        Args:
            port (AsmCmdPort | None): Typed asmcmd port for live walks.
            scope (ScopeConfig | None): Walk path scope.
            move_policy (MovePolicy | None): SQL emit policy.
            connection (ConnectionConfig | None): Connection for PDB GUID auto-fetch.
            database_filter (list[str] | tuple[str, ...] | None): Optional DB filter.
        """
        self.port = port
        self.scope = scope
        self.move_policy = move_policy
        self.connection = connection
        self.database_filter = database_filter

    def scope_resolver(self) -> WalkScopeResolver:
        """Build a WalkScopeResolver for the current scope.

        Returns:
            WalkScopeResolver: Scope resolver.

        Raises:
            RuntimeError: If no scope is configured.
        """
        if self.scope is None:
            raise RuntimeError("scope resolution requires a ScopeConfig")
        return WalkScopeResolver(
            self.scope,
            self.port,
            database_filter=self.database_filter,
        )

    def walk_path(
        self,
        asm_path: str,
        *,
        max_depth: int | None = None,
        on_scan: WalkProgressCallback | None = None,
        show_progress: bool = True,
        port: AsmCmdPort | None = None,
    ) -> AsmInventory:
        """Walk a single ASM path into a structured inventory.

        Args:
            asm_path (str): ASM root to walk.
            max_depth (int | None): Optional depth override.
            on_scan (WalkProgressCallback | None): Optional progress callback.
            show_progress (bool): When True and on_scan is None, use WalkProgressReporter.
            port (AsmCmdPort | None): Optional thread-specific port.

        Returns:
            AsmInventory: Structured inventory.

        Raises:
            RuntimeError: If no port is configured.
        """
        active_port = port or self.port
        if active_port is None:
            raise RuntimeError("live walk requires an AsmCmdPort")
        walk_root = asm_path.strip()
        if walk_root.startswith("+"):
            walk_root = normalize_asm_path(walk_root)
        depth = max_depth
        if depth is None and self.scope is not None:
            depth = self.scope.max_depth
        logger.info("walking {}", walk_root)
        progress = on_scan
        reporter: WalkProgressReporter | None = None
        if progress is None and show_progress:
            reporter = WalkProgressReporter(walk_root)
            progress = reporter
        try:
            return AsmWalker(active_port, max_depth=depth).walk(
                walk_root, on_scan=progress
            )
        finally:
            if reporter is not None:
                reporter.finish()

    def emit_sql(
        self,
        records: list[AliasRecord],
        *,
        policy: MovePolicy | None = None,
        fail_on_unmapped: bool = True,
    ) -> str:
        """Emit OMF MOVE SQL for alias records.

        Args:
            records (list[AliasRecord]): Alias records.
            policy (MovePolicy | None): Override policy; defaults to profile policy.
            fail_on_unmapped (bool): Raise when PDB GUIDs are unmapped.

        Returns:
            str: SQL script text.

        Raises:
            RuntimeError: If no move policy is available.
            UnmappedPdbGuidError: When unmapped GUIDs block emit.
        """
        resolved = policy or self.move_policy
        if resolved is None:
            raise RuntimeError("SQL emit requires a MovePolicy")
        return MoveSqlEmitter(resolved).emit(records, fail_on_unmapped=fail_on_unmapped)

    def emit_sql_by_database(
        self,
        records: list[AliasRecord],
        *,
        policy: MovePolicy | None = None,
        fail_on_unmapped: bool = True,
    ) -> dict[str, str]:
        """Emit one OMF MOVE SQL script per database unique name.

        Args:
            records (list[AliasRecord]): Alias records.
            policy (MovePolicy | None): Override policy; defaults to profile policy.
            fail_on_unmapped (bool): Raise when PDB GUIDs are unmapped.

        Returns:
            dict[str, str]: Map of database unique name → SQL script text.

        Raises:
            RuntimeError: If no move policy is available.
            UnmappedPdbGuidError: When unmapped GUIDs block emit.
        """
        resolved = policy or self.move_policy
        if resolved is None:
            raise RuntimeError("SQL emit requires a MovePolicy")
        return MoveSqlEmitter(resolved).emit_by_database(
            records, fail_on_unmapped=fail_on_unmapped
        )

    def _databases_for_pdb_map(self, aliases: list[AliasRecord]) -> list[str]:
        """Resolve database unique names for PDB GUID auto-fetch.

        Args:
            aliases (list[AliasRecord]): Alias records that may include DB path segments.

        Returns:
            list[str]: Deduplicated database names (scope first, then aliases).
        """
        names: list[str] = []
        seen: set[str] = set()

        def _add(name: str | None) -> None:
            """Add a non-empty database name once.

            Args:
                name (str | None): Candidate database name.
            """
            if not name:
                return
            key = name.casefold()
            if key in seen:
                return
            seen.add(key)
            names.append(name)

        if self.scope is not None:
            try:
                for db in self.scope_resolver().databases():
                    _add(db)
            except ValueError:
                pass
        for record in aliases:
            _add(record.database_name)
        return names

    def resolve_move_policy_for_emit(
        self,
        aliases: list[AliasRecord],
        port: AsmCmdPort | None = None,
    ) -> MovePolicy:
        """Return move policy with optional auto-fetched PDB GUID map merged in.

        When `auto_pdb_guid_map` is True and the port supports shell execution,
        runs `srvctl config database` then `sqlplus / as sysdba` against v$pdbs.
        Manual pdb_guid_map entries override auto-fetched names for the same GUID.

        Args:
            aliases (list[AliasRecord]): Alias records for the current path.
            port (AsmCmdPort | None): Optional thread-specific port.

        Returns:
            MovePolicy: Policy used for SQL emit.

        Raises:
            RuntimeError: If no move policy is available.
            PdbGuidMapError: When auto-fetch fails and unmapped PDB GUIDs remain.
        """
        base = self.move_policy
        if base is None:
            raise RuntimeError("SQL emit requires a MovePolicy")
        if not base.auto_pdb_guid_map:
            return base
        active_port = port or self.port
        if active_port is None or not isinstance(active_port, ShellRunner):
            logger.debug(
                "auto_pdb_guid_map enabled but port lacks ShellRunner; using manual map only"
            )
            return base

        databases = self._databases_for_pdb_map(aliases)
        needed = {r.pdb_guid.upper() for r in aliases if r.pdb_guid}
        if not needed:
            return base
        if not databases:
            logger.warning(
                "auto_pdb_guid_map enabled but no database names available "
                "(set scope.databases or walk a +DG/DB path)"
            )
            return base

        connection = self.connection
        collector = PdbGuidMapCollector(active_port, connection)

        try:
            auto_map = collector.collect_many(databases)
        except PdbGuidMapError as exc:
            already = {k.upper() for k in base.pdb_guid_map}
            if needed - already:
                raise PdbGuidMapError(
                    f"auto PDB GUID fetch failed and unmapped GUIDs remain: {exc}"
                ) from exc
            logger.warning(
                "auto PDB GUID fetch failed (manual map covers aliases): {}",
                exc,
            )
            return base

        merged = PdbGuidMapCollector.merge_pdb_guid_maps(auto_map, base.pdb_guid_map)
        return base.model_copy(update={"pdb_guid_map": merged})

    def process_path(
        self,
        asm_path: str,
        *,
        do_walk: bool = True,
        do_analyze: bool = True,
        do_fix: bool = True,
        from_transcript: Path | None = None,
        outfile: Path | None = None,
        fixfile: Path | None = None,
        result_json: Path | None = None,
        sequence: int | None = None,
        date: str | None = None,
        port: AsmCmdPort | None = None,
    ) -> WalkResult:
        """Run walk/analyze/fix for one ASM path and write artifacts.

        Args:
            asm_path (str): ASM path label (and live walk root when do_walk).
            do_walk (bool): Perform a live walk (ignored if from_transcript is set).
            do_analyze (bool): Extract aliases from inventory.
            do_fix (bool): Emit and write SQL when aliases exist.
            from_transcript (Path | None): Load inventory from this transcript.
            outfile (Path | None): Transcript output path.
            fixfile (Path | None): SQL output path.
            result_json (Path | None): JSON summary path.
            sequence (int | None): Multi-path sequence index for default names.
            date (str | None): YYYYMMDD stamp for default names.
            port (AsmCmdPort | None): Optional thread-specific port.

        Returns:
            WalkResult: Outcome summary for reporting.
        """
        normalized_path = asm_path.strip()
        if normalized_path.startswith("+"):
            normalized_path = normalize_asm_path(normalized_path)
        default_out, default_fix, default_json = WalkResult.build_artifact_paths(
            normalized_path, date=date, sequence=sequence
        )
        outfile = outfile or default_out
        fixfile = fixfile or default_fix
        result_json = result_json or default_json
        display_path = WalkResult.format_scan_path(normalized_path)

        inventory: AsmInventory | None = None
        if from_transcript is not None:
            inventory = load_transcript(from_transcript, root_path=normalized_path)
            write_transcript(outfile, inventory)
        elif do_walk:
            inventory = self.walk_path(normalized_path, port=port)
            write_transcript(outfile, inventory)
        elif outfile.is_file():
            inventory = load_transcript(outfile, root_path=normalized_path)
        else:
            inventory = AsmInventory(root_path=normalized_path, directories=[])

        aliases: list[AliasRecord] = []
        if do_analyze and inventory is not None:
            aliases = inventory.extract_aliases()

        files_examined, alias_rows = (
            inventory.summarize_listing_stats() if inventory is not None else (0, 0)
        )
        fix_written = False
        emit_blocked: str | None = None

        if do_fix and aliases:
            try:
                policy = self.resolve_move_policy_for_emit(aliases, port=port)
                sql_text = self.emit_sql(aliases, policy=policy)
                fixfile.parent.mkdir(parents=True, exist_ok=True)
                fixfile.write_text(sql_text, encoding="utf-8")
                fix_written = True
            except (UnmappedPdbGuidError, PdbGuidMapError) as exc:
                emit_blocked = str(exc)
                logger.error("{}", emit_blocked)

        result = WalkResult(
            asm_path=normalized_path,
            display_path=display_path,
            outfile=outfile,
            fixfile=fixfile,
            result_json=result_json,
            files_examined=files_examined,
            alias_rows=alias_rows,
            unique_aliases=len(aliases),
            fix_written=fix_written,
            emit_blocked=emit_blocked,
        )
        result.write_json(result_json)
        return result

    @contextmanager
    def _get_port_for_path(self) -> Iterator[AsmCmdPort | None]:
        """Create a connection/adapter clone for the current thread when SSH, or return the shared port.

        Yields:
            Iterator[AsmCmdPort | None]: Port instance.
        """
        if self.port is None:
            yield None
            return

        from asm_cleanup.services.connection_factory import ConnectionFactory
        from asm_cleanup.transport.ssh import SshGridAdapter

        if isinstance(self.port, SshGridAdapter):
            profile_conn = self.port.connection_config
            with ConnectionFactory().open_port(
                profile_conn, fail_loud=self.port.fail_loud
            ) as port:
                yield port
        else:
            yield self.port

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
                None uses the default executor size; pass 1 to stay sequential
                (required when sharing a single Fabric connection).

        Returns:
            list[WalkResult]: One result per processed path.

        Raises:
            ValueError: On invalid path/outfile combinations.
        """
        if from_transcript is not None:
            path = (asm_path or "").strip() or "from-transcript"
            if path.startswith("+"):
                path = normalize_asm_path(path)
            return [
                self.process_path(
                    path,
                    do_walk=False,
                    do_analyze=do_analyze,
                    do_fix=do_fix,
                    from_transcript=from_transcript,
                    outfile=outfile,
                    fixfile=fixfile,
                    result_json=result_json,
                )
            ]

        if self.scope is None:
            raw = (asm_path or "").strip()
            if not raw:
                raise ValueError(
                    "asm_path is required without scope "
                    "(pass a path or configure ScopeConfig)."
                )
            resolved = normalize_asm_path(raw) if raw.startswith("+") else raw
            return [
                self.process_path(
                    resolved,
                    do_walk=do_walk,
                    do_analyze=do_analyze,
                    do_fix=do_fix,
                    outfile=outfile,
                    fixfile=fixfile,
                    result_json=result_json,
                )
            ]

        paths = self.scope_resolver().resolve_paths(asm_path)
        if len(paths) == 1:
            return [
                self.process_path(
                    paths[0],
                    do_walk=do_walk,
                    do_analyze=do_analyze,
                    do_fix=do_fix,
                    outfile=outfile,
                    fixfile=fixfile,
                    result_json=result_json,
                )
            ]

        if outfile is not None or fixfile is not None or result_json is not None:
            raise ValueError(
                "outfile/fixfile/result_json may only be used with a single asm_path."
            )

        date = get_current_time().strftime("%Y%m%d")
        results: list[WalkResult] = [None] * len(paths)

        def _process_one(idx: int, path: str) -> tuple[int, WalkResult]:
            with self._get_port_for_path() as active_port:
                res = self.process_path(
                    path,
                    do_walk=do_walk,
                    do_analyze=do_analyze,
                    do_fix=do_fix,
                    sequence=idx,
                    date=date,
                    port=active_port,
                )
                return idx, res

        workers = max_workers if max_workers is not None else None
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
            future_to_idx = {
                executor.submit(_process_one, i, path): i
                for i, path in enumerate(paths)
            }
            for future in concurrent.futures.as_completed(future_to_idx):
                idx, res = future.result()
                results[idx] = res

        return results
