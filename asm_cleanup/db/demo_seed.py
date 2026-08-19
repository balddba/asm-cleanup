"""Build a fictional demo SQLite database for documentation screenshots.

The demo database is completely separate from the runtime application database.
Builders refuse production DB filenames so real target data cannot be overwritten.
"""

from __future__ import annotations

import datetime
import json
from pathlib import Path

from loguru import logger

from asm_cleanup.config.move_policy import MovePolicy
from asm_cleanup.db.db_manager import DbManager
from asm_cleanup.db.scan import Scan
from asm_cleanup.db.scan_alias import ScanAlias
from asm_cleanup.db.target import Target
from asm_cleanup.domain.alias_record import AliasRecord
from asm_cleanup.sql.move_sql_emitter import MoveSqlEmitter

DEFAULT_DEMO_DB_PATH = Path("docs/demo/asm_cleanup_demo.db")
PRODUCTION_DB_BASENAMES = frozenset({"asm_cleanup.db"})

_SALES_PDB_GUID = "49C96937E332EB45E0631A04010ABA14"
_HR_PDB_GUID = "5061D3DDBF80C747E0631A04010AB48B"
_FIN_PDB_GUID = "61A2B4CCD091D858E0631A04010AC59C"


class ProductionDatabaseError(ValueError):
    """Raised when a builder is pointed at a production database path."""


def assert_not_production_database(path: Path) -> None:
    """Refuse paths that resolve to the production SQLite filename.

    Args:
        path (Path): Candidate database file path.

    Raises:
        ProductionDatabaseError: When the basename is a production DB name.
    """
    if path.name in PRODUCTION_DB_BASENAMES:
        raise ProductionDatabaseError(
            f"Refusing to write demo data to production database path {path}. "
            "Use docs/demo/asm_cleanup_demo.db (or another non-production filename)."
        )


def _render_demo_sql(aliases: list[ScanAlias], destination_disk_group: str) -> str:
    """Render demo SQL through the production emitter.

    Args:
        aliases (list[ScanAlias]): Seed aliases for one completed scan.
        destination_disk_group (str): Destination disk group for generated SQL.

    Returns:
        str: JSON-encoded map of database names to generated SQL scripts.
    """
    records = [
        AliasRecord(
            file_type=alias.file_type,
            source_path=alias.source_path,
            target_path=alias.target_path,
            pdb_guid=alias.pdb_guid,
            disk_group=AliasRecord.disk_group_from_asm_path(alias.source_path),
            database_name=alias.database_name,
        )
        for alias in aliases
    ]
    pdb_guid_map = {
        alias.pdb_guid: alias.container_name
        for alias in aliases
        if alias.pdb_guid and alias.container_name
    }
    policy = MovePolicy(
        destination_disk_group=destination_disk_group,
        pdb_guid_map=pdb_guid_map,
        auto_pdb_guid_map=False,
    )
    return json.dumps(MoveSqlEmitter(policy).emit_by_database(records))


def build_demo_database(output_path: Path | None = None) -> Path:
    """Create or overwrite a pre-populated fictional demo SQLite database.

    Args:
        output_path (Path | None): Destination SQLite file. Defaults to
            docs/demo/asm_cleanup_demo.db.

    Returns:
        Path: Absolute path of the written demo database.

    Raises:
        ProductionDatabaseError: When output_path uses a production DB basename.
    """
    dest = (output_path or DEFAULT_DEMO_DB_PATH).resolve()
    assert_not_production_database(dest)

    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        dest.unlink()

    database_url = f"sqlite:///{dest}"
    logger.info("building demo database at {}", dest)
    db_manager = DbManager(database_url)
    db_manager.run_migrations()

    now = datetime.datetime(2026, 6, 15, 14, 30, 0, tzinfo=datetime.UTC)
    older = datetime.datetime(2026, 6, 14, 9, 0, 0, tzinfo=datetime.UTC)

    with db_manager.session() as session:
        prod = Target(
            name="prod-grid-01",
            host="grid.prod.baldba.com",
            user="oracle",
            ssh_key_path="/home/oracle/.ssh/id_ed25519",
            grid_home="/u01/app/19.0.0/grid",
            oracle_sid="+ASM1",
            destination_disk_group="+DATA",
            created_at=older,
            updated_at=older,
        )
        lab = Target(
            name="lab-asm",
            host="lab-asm.baldba.com",
            user="grid",
            grid_home="/u01/app/19.0.0/grid",
            oracle_sid="+ASM",
            destination_disk_group="+DATA",
            created_at=older,
            updated_at=older,
        )
        session.add_all([prod, lab])
        session.flush()

        prod_db_meta = {
            "salescdb": {
                "oracle_home": "/u01/app/oracle/product/19.0.0/dbhome_1",
                "oracle_sid": "salescdb",
                "parameters": {
                    "db_create_file_dest": "+DATA",
                    "db_recovery_file_dest": "+FRA",
                },
                "pdb_count": 1,
                "pdbs": [{"name": "SALESPDB", "guid": _SALES_PDB_GUID}],
            },
            "hrcdb": {
                "oracle_home": "/u01/app/oracle/product/19.0.0/dbhome_1",
                "oracle_sid": "hrcdb",
                "parameters": {
                    "db_create_file_dest": "+DATA",
                    "db_recovery_file_dest": "+FRA",
                },
                "pdb_count": 1,
                "pdbs": [{"name": "HRPDB", "guid": _HR_PDB_GUID}],
            },
        }
        lab_db_meta = {
            "fincdb": {
                "oracle_home": "/u01/app/oracle/product/19.0.0/dbhome_1",
                "oracle_sid": "fincdb",
                "parameters": {
                    "db_create_file_dest": "+DATA",
                    "db_recovery_file_dest": "+FRA",
                },
                "pdb_count": 1,
                "pdbs": [{"name": "FINPDB", "guid": _FIN_PDB_GUID}],
            }
        }

        prod_scan = Scan(
            target_id=prod.id,
            status="completed",
            progress_message=None,
            error_message=None,
            grid_home="/u01/app/19.0.0/grid",
            disk_groups=json.dumps(["+DATA", "+FRA"]),
            databases=json.dumps(prod_db_meta),
            created_at=now,
        )
        lab_scan = Scan(
            target_id=lab.id,
            status="completed",
            progress_message=None,
            error_message=None,
            grid_home="/u01/app/19.0.0/grid",
            disk_groups=json.dumps(["+DATA", "+FRA"]),
            databases=json.dumps(lab_db_meta),
            created_at=older,
        )
        session.add_all([prod_scan, lab_scan])
        session.flush()

        aliases = [
            ScanAlias(
                scan_id=prod_scan.id,
                database_name="salescdb",
                container_name="CDB$ROOT",
                file_type="DATAFILE",
                source_path="+DATA/SALESCDB/datafile/system_custom.dbf",
                target_path="+DATA",
                pdb_guid=None,
                created_at=now,
            ),
            ScanAlias(
                scan_id=prod_scan.id,
                database_name="salescdb",
                container_name="CDB$ROOT",
                file_type="DATAFILE",
                source_path="+DATA/SALESCDB/datafile/sysaux_custom.dbf",
                target_path="+DATA",
                pdb_guid=None,
                created_at=now,
            ),
            ScanAlias(
                scan_id=prod_scan.id,
                database_name="salescdb",
                container_name="CDB$ROOT",
                file_type="DATAFILE",
                source_path="+DATA/SALESCDB/datafile/undotbs1.dbf",
                target_path="+DATA",
                pdb_guid=None,
                created_at=now,
            ),
            ScanAlias(
                scan_id=prod_scan.id,
                database_name="salescdb",
                container_name="CDB$ROOT",
                file_type="DATAFILE",
                source_path="+DATA/SALESCDB/datafile/users.dbf",
                target_path="+DATA",
                pdb_guid=None,
                created_at=now,
            ),
            ScanAlias(
                scan_id=prod_scan.id,
                database_name="salescdb",
                container_name="CDB$ROOT",
                file_type="TEMPFILE",
                source_path="+DATA/SALESCDB/tempfile/temp01.dbf",
                target_path="+DATA",
                pdb_guid=None,
                created_at=now,
            ),
            ScanAlias(
                scan_id=prod_scan.id,
                database_name="salescdb",
                container_name="SALESPDB",
                file_type="DATAFILE",
                source_path=(f"+DATA/SALESCDB/{_SALES_PDB_GUID}/datafile/system.dbf"),
                target_path="+DATA",
                pdb_guid=_SALES_PDB_GUID,
                created_at=now,
            ),
            ScanAlias(
                scan_id=prod_scan.id,
                database_name="salescdb",
                container_name="SALESPDB",
                file_type="DATAFILE",
                source_path=(f"+DATA/SALESCDB/{_SALES_PDB_GUID}/datafile/sysaux.dbf"),
                target_path="+DATA",
                pdb_guid=_SALES_PDB_GUID,
                created_at=now,
            ),
            ScanAlias(
                scan_id=prod_scan.id,
                database_name="salescdb",
                container_name="SALESPDB",
                file_type="DATAFILE",
                source_path=(f"+DATA/SALESCDB/{_SALES_PDB_GUID}/datafile/users.dbf"),
                target_path="+DATA",
                pdb_guid=_SALES_PDB_GUID,
                created_at=now,
            ),
            ScanAlias(
                scan_id=prod_scan.id,
                database_name="hrcdb",
                container_name="CDB$ROOT",
                file_type="DATAFILE",
                source_path="+DATA/HRCDB/datafile/system_custom.dbf",
                target_path="+DATA",
                pdb_guid=None,
                created_at=now,
            ),
            ScanAlias(
                scan_id=prod_scan.id,
                database_name="hrcdb",
                container_name="HRPDB",
                file_type="DATAFILE",
                source_path=(f"+DATA/HRCDB/{_HR_PDB_GUID}/datafile/system.dbf"),
                target_path="+DATA",
                pdb_guid=_HR_PDB_GUID,
                created_at=now,
            ),
            ScanAlias(
                scan_id=prod_scan.id,
                database_name="hrcdb",
                container_name="HRPDB",
                file_type="TEMPFILE",
                source_path=(f"+DATA/HRCDB/{_HR_PDB_GUID}/tempfile/temp.dbf"),
                target_path="+DATA",
                pdb_guid=_HR_PDB_GUID,
                created_at=now,
            ),
            ScanAlias(
                scan_id=lab_scan.id,
                database_name="fincdb",
                container_name="CDB$ROOT",
                file_type="DATAFILE",
                source_path="+DATA/FINCDB/datafile/users_custom.dbf",
                target_path="+DATA",
                pdb_guid=None,
                created_at=older,
            ),
            ScanAlias(
                scan_id=lab_scan.id,
                database_name="fincdb",
                container_name="FINPDB",
                file_type="DATAFILE",
                source_path=(f"+DATA/FINCDB/{_FIN_PDB_GUID}/datafile/system.dbf"),
                target_path="+DATA",
                pdb_guid=_FIN_PDB_GUID,
                created_at=older,
            ),
        ]
        prod_aliases = [alias for alias in aliases if alias.scan_id == prod_scan.id]
        lab_aliases = [alias for alias in aliases if alias.scan_id == lab_scan.id]
        prod_scan.generated_sql = _render_demo_sql(
            prod_aliases, prod.destination_disk_group
        )
        lab_scan.generated_sql = _render_demo_sql(
            lab_aliases, lab.destination_disk_group
        )
        session.add_all(aliases)

    db_manager.engine.dispose()
    logger.info("demo database ready at {}", dest)
    return dest
