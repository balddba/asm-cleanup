"""Tests for alias parsing and OMF SQL generation."""

import pytest

from asm_cleanup.config.move_policy import MovePolicy
from asm_cleanup.domain.alias_record import AliasRecord
from asm_cleanup.sql.move_sql_emitter import SCRIPT_DISCLAIMER, MoveSqlEmitter
from asm_cleanup.sql.unmapped_pdb_guid_error import UnmappedPdbGuidError
from asm_cleanup.walk.asm_inventory import AsmInventory
from asm_cleanup.walk.directory_listing import DirectoryListing
from asm_cleanup.walk.transcript import transcript_to_inventory


def _policy(**overrides: object) -> MovePolicy:
    """Build a MovePolicy with defaults.

    Args:
        **overrides (object): Field overrides.

    Returns:
        MovePolicy: Validated policy.
    """
    data: dict[str, object] = {"destination_disk_group": "+DATA"}
    data.update(overrides)
    return MovePolicy.model_validate(data)


def _emit(
    records: list[AliasRecord],
    policy: MovePolicy,
    *,
    fail_on_unmapped: bool = True,
) -> str:
    """Emit SQL via MoveSqlEmitter for test brevity.

    Args:
        records (list[AliasRecord]): Alias rows.
        policy (MovePolicy): Move policy.
        fail_on_unmapped (bool): Raise on unmapped PDB GUIDs.

    Returns:
        str: SQL script text.
    """
    return MoveSqlEmitter(policy).emit(records, fail_on_unmapped=fail_on_unmapped)


def test_pdb_guid_from_path() -> None:
    """Extract GUID directories and return None for CDB$ROOT layout."""
    guid = "49C96937E332EB45E0631A04010ABA14"
    assert (
        AliasRecord.pdb_guid_from_path(f"+DATA/MYDB/{guid}/DATAFILE/file.dbf")
        == guid.upper()
    )
    assert AliasRecord.pdb_guid_from_path("+DATA/MYDB/DATAFILE/file.dbf") is None


def test_extract_aliases_happy_path() -> None:
    """Parse directory listings into AliasRecord models."""
    inventory = AsmInventory(
        root_path="+DATA/MYDB",
        directories=[
            DirectoryListing(
                path="+DATA/MYDB/DATAFILE",
                long_lines=[
                    "DATAFILE users.dbf => +DATA/MYDB/DATAFILE/USERS.256.1",
                    "TEMPFILE temp.dbf => +DATA/MYDB/TEMPFILE/TEMP.257.1",
                ],
            )
        ],
    )
    aliases = inventory.extract_aliases()
    assert len(aliases) == 2
    assert aliases[0].file_type == "DATAFILE"
    assert aliases[0].source_path == "+DATA/MYDB/DATAFILE/users.dbf"
    assert aliases[0].target_path == "+DATA/MYDB/DATAFILE/USERS.256.1"
    assert aliases[0].pdb_guid is None
    assert aliases[0].disk_group == "+DATA"


def test_extract_aliases_from_transcript() -> None:
    """Rebuild inventory from a versioned transcript then extract aliases."""
    text = """# asm-cleanup-transcript:1
DIR: +DATA/MYDB/DATAFILE
------------------------------------------------------------
DATAFILE users.dbf => +DATA/MYDB/DATAFILE/USERS.256.1
"""
    inventory = transcript_to_inventory(text)
    aliases = inventory.extract_aliases()
    assert len(aliases) == 1
    assert aliases[0].source_path == "+DATA/MYDB/DATAFILE/users.dbf"


def test_extract_aliases_dedupes_casefold() -> None:
    """Drop duplicate aliases that differ only by case."""
    inventory = AsmInventory(
        root_path="+DATA/MYDB",
        directories=[
            DirectoryListing(
                path="+DATA/MYDB/DATAFILE",
                long_lines=[
                    "DATAFILE users.dbf => +DATA/MYDB/DATAFILE/USERS.256.1",
                    "DATAFILE USERS.DBF => +DATA/MYDB/DATAFILE/USERS.256.1",
                ],
            )
        ],
    )
    assert len(inventory.extract_aliases()) == 1


def test_extract_aliases_empty_without_dirs() -> None:
    """Return no aliases when inventory has no directory listings."""
    assert AsmInventory(root_path="+DATA/MYDB", directories=[]).extract_aliases() == []


def test_summarize_listing_stats() -> None:
    """Count examined files and alias rows from listings."""
    inventory = AsmInventory(
        root_path="+DATA/MYDB",
        directories=[
            DirectoryListing(
                path="+DATA/MYDB",
                long_lines=["DATAFILE a => +DATA/x", "DATAFILE b", "NFILE ignored"],
            )
        ],
    )
    assert inventory.summarize_listing_stats() == (2, 1)


def test_move_sql_emitter_datafile() -> None:
    """Emit MOVE DATAFILE SQL for CDB$ROOT entries using destination policy."""
    sql = _emit(
        [
            AliasRecord(
                file_type="DATAFILE",
                source_path="+DATA/MYDB/DATAFILE/users.dbf",
                target_path="+DATA/OMF",
                pdb_guid=None,
                disk_group="+DATA",
            )
        ],
        _policy(destination_disk_group="+FRA"),
    )
    assert sql.startswith(SCRIPT_DISCLAIMER)
    assert "WARNING: REVIEW BEFORE EXECUTION" in sql
    assert (
        "ALTER DATABASE MOVE DATAFILE '+DATA/MYDB/DATAFILE/users.dbf' TO '+FRA';" in sql
    )
    assert "ALTER SESSION SET CONTAINER" not in sql


def test_move_sql_emitter_pdb_switch() -> None:
    """Insert SET CONTAINER when PDB GUID maps to a name."""
    guid = "49C96937E332EB45E0631A04010ABA14"
    sql = _emit(
        [
            AliasRecord(
                file_type="DATAFILE",
                source_path=f"+DATA/MYDB/{guid}/DATAFILE/a.dbf",
                target_path="+DATA/OMF",
                pdb_guid=guid,
                disk_group="+DATA",
            )
        ],
        _policy(pdb_guid_map={guid: "TOOLKITPDB"}),
    )
    assert "ALTER SESSION SET CONTAINER = TOOLKITPDB;" in sql
    assert (
        "-- Switch to container TOOLKITPDB so following MOVE statements "
        "run in that PDB (or CDB$ROOT)." in sql
    )


def test_move_sql_emitter_unmapped_guid_fails() -> None:
    """Fail fast when PDB GUIDs are missing from move_policy."""
    guid = "49C96937E332EB45E0631A04010ABA14"
    with pytest.raises(UnmappedPdbGuidError, match="emit blocked"):
        _emit(
            [
                AliasRecord(
                    file_type="DATAFILE",
                    source_path=f"+DATA/MYDB/{guid}/DATAFILE/a.dbf",
                    target_path="+DATA/OMF",
                    pdb_guid=guid,
                    disk_group="+DATA",
                )
            ],
            _policy(pdb_guid_map={}),
        )


def test_move_sql_emitter_unmapped_guid_placeholder() -> None:
    """When fail_on_unmapped=False, emit with a PDB_GUID_xxxxxxxx container."""
    guid = "49C96937E332EB45E0631A04010ABA14"
    sql = _emit(
        [
            AliasRecord(
                file_type="DATAFILE",
                source_path=f"+DATA/MYDB/{guid}/DATAFILE/a.dbf",
                target_path="+DATA/OMF",
                pdb_guid=guid,
                disk_group="+DATA",
            )
        ],
        _policy(pdb_guid_map={}),
        fail_on_unmapped=False,
    )
    assert "ALTER SESSION SET CONTAINER = PDB_GUID_49C96937;" in sql
    assert f"MOVE DATAFILE '+DATA/MYDB/{guid}/DATAFILE/a.dbf'" in sql


def test_move_sql_emitter_online() -> None:
    """Append ONLINE when MovePolicy.online is True."""
    sql = _emit(
        [
            AliasRecord(
                file_type="DATAFILE",
                source_path="+DATA/MYDB/DATAFILE/users.dbf",
                target_path="+DATA/OMF",
                pdb_guid=None,
                disk_group="+DATA",
            )
        ],
        _policy(destination_disk_group="+FRA", online=True),
    )
    assert (
        "ALTER DATABASE MOVE DATAFILE '+DATA/MYDB/DATAFILE/users.dbf' "
        "TO '+FRA' ONLINE;" in sql
    )


def test_move_sql_emitter_by_database() -> None:
    """Emit separate scripts keyed by database unique name."""
    scripts = MoveSqlEmitter(_policy(destination_disk_group="+DATA")).emit_by_database(
        [
            AliasRecord(
                file_type="DATAFILE",
                source_path="+DATA/SALESCDB/DATAFILE/a.dbf",
                target_path="+DATA/OMF",
                pdb_guid=None,
                disk_group="+DATA",
            ),
            AliasRecord(
                file_type="DATAFILE",
                source_path="+DATA/HRCDB/DATAFILE/b.dbf",
                target_path="+DATA/OMF",
                pdb_guid=None,
                disk_group="+DATA",
            ),
        ]
    )
    assert set(scripts) == {"SALESCDB", "HRCDB"}
    assert "SALESCDB/DATAFILE/a.dbf" in scripts["SALESCDB"]
    assert "HRCDB/DATAFILE/b.dbf" in scripts["HRCDB"]
    assert "SALESCDB" not in scripts["HRCDB"]


def test_move_sql_emitter_by_database_uses_explicit_name() -> None:
    """Group scripts by catalog database name, not a DG-root filename segment."""
    scripts = MoveSqlEmitter(_policy(destination_disk_group="+DATA")).emit_by_database(
        [
            AliasRecord(
                file_type="DATAFILE",
                source_path="+DATA/junk.dbf",
                target_path="+DATA/KEYTEST/DATAFILE/junk.dbf",
                pdb_guid=None,
                disk_group="+DATA",
                database_name="KEYTEST",
            ),
        ]
    )
    assert set(scripts) == {"KEYTEST"}
    assert "junk.dbf" not in scripts
    assert "+DATA/junk.dbf" in scripts["KEYTEST"]


def test_parse_generated_sql_by_database() -> None:
    """Parse JSON-encoded per-database scripts and ignore plain SQL text."""
    from asm_cleanup.sql.move_sql_emitter import (
        format_generated_sql_for_display,
        parse_generated_sql_by_database,
    )

    assert parse_generated_sql_by_database(None) == {}
    assert parse_generated_sql_by_database("-- plain sql") == {}
    raw = '{"salescdb": "ALTER ...;", "hrcdb": "ALTER ...;"}'
    assert parse_generated_sql_by_database(raw) == {
        "salescdb": "ALTER ...;",
        "hrcdb": "ALTER ...;",
    }
    formatted = format_generated_sql_for_display(raw)
    assert "-- Database: salescdb" in formatted
    assert "-- Database: hrcdb" in formatted


def test_move_sql_emitter_customizations() -> None:
    """Respect lowercase, headers, footers, and spool file options."""
    guid = "49C96937E332EB45E0631A04010ABA14"
    policy = _policy(
        destination_disk_group="+FRA",
        pdb_guid_map={guid: "TOOLKITPDB"},
        lowercase_keywords=True,
        sql_header="alter session set current_schema=sys;",
        sql_footer="exit;",
        spool_file="move.log",
    )
    sql = _emit(
        [
            AliasRecord(
                file_type="DATAFILE",
                source_path=f"+DATA/MYDB/{guid}/DATAFILE/a.dbf",
                target_path="+DATA/OMF",
                pdb_guid=guid,
                disk_group="+DATA",
            )
        ],
        policy,
    )
    assert sql.startswith(SCRIPT_DISCLAIMER)
    assert "alter session set container = TOOLKITPDB;" in sql
    assert (
        "alter database move datafile '+DATA/MYDB/49C96937E332EB45E0631A04010ABA14/DATAFILE/a.dbf' to '+FRA';"
        in sql
    )
    assert "alter session set current_schema=sys;" in sql
    assert "spool move.log;" in sql
    assert "spool off;" in sql
    assert sql.endswith("exit;")
