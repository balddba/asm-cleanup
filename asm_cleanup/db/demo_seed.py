"""Build a fictional demo SQLite database for documentation screenshots.

The demo database is completely separate from the runtime application database.
Builders refuse production DB filenames so real target data cannot be overwritten.
"""

from __future__ import annotations

import datetime
import json
from pathlib import Path

from loguru import logger

from asm_cleanup.db.db_manager import DbManager
from asm_cleanup.db.scan import Scan
from asm_cleanup.db.scan_alias import ScanAlias
from asm_cleanup.db.target import Target

DEFAULT_DEMO_DB_PATH = Path("docs/demo/asm_cleanup_demo.db")
PRODUCTION_DB_BASENAMES = frozenset({"asm_cleanup.db"})

_SALES_PDB_GUID = "49C96937E332EB45E0631A04010ABA14"
_HR_PDB_GUID = "5061D3DDBF80C747E0631A04010AB48B"
_FIN_PDB_GUID = "61A2B4CCD091D858E0631A04010AC59C"

_PROD_GENERATED_SQL = json.dumps(
    {
        "salescdb": """\
-- Review-only OMF MOVE script (demo data; not executed by asm-cleanup)
-- Target: prod-grid-01 / SALESCDB -> +DATA

ALTER DATABASE MOVE DATAFILE '+DATA/SALESCDB/datafile/system_custom.dbf' TO '+DATA';
ALTER DATABASE MOVE DATAFILE '+DATA/SALESCDB/datafile/sysaux_custom.dbf' TO '+DATA';
ALTER DATABASE MOVE DATAFILE '+DATA/SALESCDB/datafile/undotbs1.dbf' TO '+DATA';
ALTER DATABASE MOVE DATAFILE '+DATA/SALESCDB/datafile/users.dbf' TO '+DATA';
ALTER DATABASE MOVE DATAFILE '+DATA/SALESCDB/tempfile/temp01.dbf' TO '+DATA';
ALTER DATABASE MOVE DATAFILE '+DATA/SALESCDB/49C96937E332EB45E0631A04010ABA14/datafile/system.dbf' TO '+DATA';
ALTER DATABASE MOVE DATAFILE '+DATA/SALESCDB/49C96937E332EB45E0631A04010ABA14/datafile/sysaux.dbf' TO '+DATA';
ALTER DATABASE MOVE DATAFILE '+DATA/SALESCDB/49C96937E332EB45E0631A04010ABA14/datafile/users.dbf' TO '+DATA';
""",
        "hrcdb": """\
-- Review-only OMF MOVE script (demo data; not executed by asm-cleanup)
-- Target: prod-grid-01 / HRCDB -> +DATA

ALTER DATABASE MOVE DATAFILE '+DATA/HRCDB/datafile/system_custom.dbf' TO '+DATA';
ALTER DATABASE MOVE DATAFILE '+DATA/HRCDB/5061D3DDBF80C747E0631A04010AB48B/datafile/system.dbf' TO '+DATA';
ALTER DATABASE MOVE DATAFILE '+DATA/HRCDB/5061D3DDBF80C747E0631A04010AB48B/tempfile/temp.dbf' TO '+DATA';
""",
    }
)

_LAB_GENERATED_SQL = json.dumps(
    {
        "fincdb": """\
-- Review-only OMF MOVE script (demo data; not executed by asm-cleanup)
-- Target: lab-asm / FINCDB -> +DATA

ALTER DATABASE MOVE DATAFILE '+DATA/FINCDB/datafile/users_custom.dbf' TO '+DATA';
ALTER DATABASE MOVE DATAFILE '+DATA/FINCDB/61A2B4CCD091D858E0631A04010AC59C/datafile/system.dbf' TO '+DATA';
"""
    }
)


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
            generated_sql=_PROD_GENERATED_SQL,
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
            generated_sql=_LAB_GENERATED_SQL,
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
        session.add_all(aliases)

    db_manager.engine.dispose()
    logger.info("demo database ready at {}", dest)
    return dest
