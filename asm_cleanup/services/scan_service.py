"""Full discovery scan orchestration for a Target/Scan pair."""

from __future__ import annotations

import json
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass, field

from fabric import Connection
from loguru import logger
from sqlalchemy.orm import Session

from asm_cleanup.auth.ssh_key_store import (
    SshKeyStore,
    load_pasted_ssh_key,
    ssh_key_store_from_env,
)
from asm_cleanup.config.move_policy import MovePolicy
from asm_cleanup.config.scope_config import ScopeConfig
from asm_cleanup.db.scan import Scan
from asm_cleanup.db.target import Target
from asm_cleanup.discovery.host_discovery import HostDiscovery
from asm_cleanup.domain.alias_record import AliasRecord
from asm_cleanup.pipeline.pipeline_orchestrator import PipelineOrchestrator
from asm_cleanup.services.alias_enrichment import AliasEnricher
from asm_cleanup.services.connection_factory import ConnectionFactory
from asm_cleanup.services.target_mapper import TargetMapper
from asm_cleanup.transport.asm_cmd_port import AsmCmdPort
from asm_cleanup.transport.ssh import SshGridAdapter


@dataclass
class WalkAliasOutcome:
    """Result of walking scoped ASM paths during a discovery scan.

    Attributes:
        records (list[AliasRecord]): Deduplicated aliases from successful walks.
        paths_attempted (int): Number of ASM roots the walker tried.
        failed_paths (list[str]): Paths that raised during walk.
    """

    records: list[AliasRecord]
    paths_attempted: int
    failed_paths: list[str] = field(default_factory=list)


class ScanService:
    """Orchestrate host discovery, ASM walk, enrichment, SQL emit, and persistence.

    Attributes:
        session (Session): SQLAlchemy database session.
        target (Target): Target connection profile model.
        scan (Scan): Associated Scan model tracking progress and output.
    """

    def __init__(
        self,
        session: Session,
        target: Target,
        scan: Scan,
        connection_factory: ConnectionFactory | None = None,
        ssh_key_store: SshKeyStore | None = None,
    ) -> None:
        """Initialize the scan service.

        Args:
            session (Session): Database session.
            target (Target): Connection target.
            scan (Scan): Scan to update.
            connection_factory (ConnectionFactory | None): Optional SSH/port factory.
            ssh_key_store (SshKeyStore | None): Optional pasted-key cryptfile store.
        """
        self.session = session
        self.target = target
        self.scan = scan
        self._connection_factory = connection_factory or ConnectionFactory()
        self._ssh_key_store = ssh_key_store
        self._host = HostDiscovery(target)

    def _set_progress(self, message: str) -> None:
        """Persist a human-readable scan phase for UI polling.

        Args:
            message (str): Short progress description shown in the web UI.
        """
        self.scan.progress_message = message
        self.session.commit()
        logger.debug("scan_id={} progress={!r}", self.scan.id, message)

    @contextmanager
    def _ssh_connection(self) -> Generator[Connection]:
        """Establish a Fabric SSH connection via ConnectionFactory.

        Yields:
            Connection: Active Fabric connection.
        """
        store = self._ssh_key_store or ssh_key_store_from_env()
        had_legacy = bool((self.target.ssh_key_content or "").strip())
        pasted_key = load_pasted_ssh_key(self.target, store)
        if had_legacy and not (self.target.ssh_key_content or "").strip():
            self.session.commit()
        with self._connection_factory.open_fabric(
            self.target.host,
            self.target.user,
            ssh_key_path=self.target.ssh_key_path,
            ssh_key_content=pasted_key,
            allow_missing_key=True,
        ) as conn:
            yield conn

    def _walk_asm_aliases(
        self,
        port: AsmCmdPort,
        *,
        scope: ScopeConfig,
    ) -> WalkAliasOutcome:
        """Walk scoped ASM paths via AsmWalker and extract alias records.

        Args:
            port (AsmCmdPort): Typed asmcmd port (typically SshGridAdapter).
            scope (ScopeConfig): Discovered walk scope.

        Returns:
            WalkAliasOutcome: Deduplicated aliases plus per-path failure metadata.
        """
        orch = PipelineOrchestrator(port=port, scope=scope)
        try:
            paths = orch.scope_resolver().resolve_paths()
        except ValueError as exc:
            logger.warning("no ASM paths to walk: {}", exc)
            return WalkAliasOutcome(records=[], paths_attempted=0, failed_paths=[])

        records: list[AliasRecord] = []
        seen: set[str] = set()
        failed_paths: list[str] = []
        total = len(paths)
        for index, path in enumerate(paths, start=1):
            self._set_progress(f"Walking ASM path {index}/{total}: {path}")
            logger.info("walking ASM path={}", path)
            try:
                inventory = orch.walk_path(path, show_progress=False)
            except Exception as exc:  # noqa: BLE001 (Resilience: continue walking other paths if one fails)
                logger.warning("failed to walk ASM path {}: {}", path, exc)
                failed_paths.append(path)
                continue
            for record in inventory.extract_aliases():
                key = (
                    f"{record.file_type}|{record.source_path.casefold()}|"
                    f"{record.target_path.casefold()}"
                )
                if key in seen:
                    continue
                seen.add(key)
                records.append(record)
        return WalkAliasOutcome(
            records=records,
            paths_attempted=total,
            failed_paths=failed_paths,
        )

    def run(self) -> None:
        """Execute host discovery, library walk/SQL, and persist scan results."""
        try:
            self.scan.status = "running"
            self._set_progress("Connecting to target host...")

            with self._ssh_connection() as conn:
                self._set_progress("Discovering Grid Home and ASM SID...")
                grid_home, asm_sid = self._host.discover_grid_home_and_sid(conn)
                self.scan.grid_home = grid_home
                logger.info(
                    "discovered grid_home={!r} and asm_sid={!r}", grid_home, asm_sid
                )

                self._set_progress("Discovering ASM disk groups...")
                disk_groups = self._host.discover_disk_groups(conn, grid_home, asm_sid)
                self.scan.disk_groups = json.dumps(disk_groups)
                logger.info("discovered disk groups: {}", disk_groups)

                self._set_progress("Discovering databases...")
                databases = self._host.discover_databases(conn, grid_home)
                logger.info("discovered database list: {}", list(databases.keys()))

                db_metadata: dict[str, object] = {}
                all_db_files: dict[str, dict[str, str]] = {}
                guid_pdb_map: dict[str, str] = {}
                db_names = list(databases.keys())
                db_total = len(db_names)

                for index, db_name in enumerate(db_names, start=1):
                    self._set_progress(
                        f"Collecting database details {index}/{db_total}: {db_name}"
                    )
                    logger.info(
                        "collecting configuration and files for database={}", db_name
                    )
                    db_home, db_sid = self._host.get_database_home_and_sid(
                        conn, db_name, grid_home
                    )
                    db_params, db_pdbs, db_files = self._host.collect_database_details(
                        conn, db_name, db_home, db_sid
                    )
                    logger.info(
                        "discovered {} PDB(s) in database={}", len(db_pdbs), db_name
                    )
                    db_metadata[db_name] = {
                        "oracle_home": db_home,
                        "oracle_sid": db_sid,
                        "parameters": db_params,
                        "pdb_count": len(db_pdbs),
                        "pdbs": [{"name": p[0], "guid": p[1]} for p in db_pdbs],
                    }
                    for pdb_name, guid in db_pdbs:
                        guid_pdb_map[guid.upper()] = pdb_name
                    for file_path, _con_id, con_name, file_type in db_files:
                        norm_path = file_path.strip().casefold()
                        all_db_files[norm_path] = {
                            "con_name": con_name,
                            "file_type": file_type,
                            "database": db_name,
                            "raw_path": file_path,
                        }

                self.scan.databases = json.dumps(db_metadata)

                connection = TargetMapper.to_connection_config(
                    self.target,
                    grid_home=grid_home,
                    oracle_sid=asm_sid,
                )
                scope = ScopeConfig(
                    disk_groups=disk_groups,
                    databases=list(databases.keys()),
                )
                move_policy = MovePolicy(
                    destination_disk_group=self.target.destination_disk_group,
                    pdb_guid_map=dict(guid_pdb_map),
                    auto_pdb_guid_map=False,
                    online=bool(self.target.move_online),
                )
                # fail_loud=False: missing +DG/DB paths should not abort the scan
                port: AsmCmdPort = SshGridAdapter(connection, conn, fail_loud=False)

                logger.info("scanning ASM paths via library walker")
                walk_outcome = self._walk_asm_aliases(port, scope=scope)
                if (
                    walk_outcome.paths_attempted
                    and len(walk_outcome.failed_paths) == walk_outcome.paths_attempted
                ):
                    failed = ", ".join(walk_outcome.failed_paths[:8])
                    raise RuntimeError(
                        "ASM walk failed for every path "
                        f"({walk_outcome.paths_attempted}): {failed}"
                    )
                if walk_outcome.failed_paths:
                    logger.warning(
                        "ASM walk failed for {} of {} paths: {}",
                        len(walk_outcome.failed_paths),
                        walk_outcome.paths_attempted,
                        walk_outcome.failed_paths,
                    )
                walked = walk_outcome.records
                logger.info("discovered {} alias files in ASM", len(walked))

                self._set_progress(
                    f"Enriching {len(walked)} alias record(s) and matching DB files..."
                )
                enricher = AliasEnricher(
                    self.session,
                    self.scan,
                    self.target.destination_disk_group,
                )
                records = enricher.enrich_and_persist(
                    walked,
                    all_db_files=all_db_files,
                    guid_pdb_map=guid_pdb_map,
                )

                orch = PipelineOrchestrator(
                    port=port,
                    scope=scope,
                    move_policy=move_policy,
                    connection=connection,
                )
                self._set_progress("Generating OMF move SQL...")
                if records:
                    # Inject placeholder names for any still-unmapped GUIDs
                    emit_map = dict(guid_pdb_map)
                    for record in records:
                        if record.pdb_guid:
                            key = record.pdb_guid.upper()
                            emit_map.setdefault(key, f"PDB_GUID_{key[:8]}")
                    policy = move_policy.model_copy(update={"pdb_guid_map": emit_map})
                    by_database = orch.emit_sql_by_database(
                        records, policy=policy, fail_on_unmapped=False
                    )
                    self.scan.generated_sql = json.dumps(by_database)
                else:
                    self.scan.generated_sql = (
                        "-- No alias files found that require moving."
                    )

                self.scan.status = "completed"
                self.scan.progress_message = None
                self.session.commit()
                logger.info("scan finished successfully")

        except Exception as exc:  # noqa: BLE001 (Prevent runner thread from crashing and leaving scan in running state)
            logger.error("scan failed with exception: {}", exc)
            self.scan.status = "failed"
            self.scan.progress_message = None
            self.scan.error_message = str(exc)
            self.session.commit()
