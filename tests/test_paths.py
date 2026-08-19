"""Tests for ASM path helpers."""

from pathlib import Path

from asm_cleanup.domain.alias_record import AliasRecord
from asm_cleanup.pipeline.walk_results import WalkResult
from asm_cleanup.pipeline.walk_scope_resolver import WalkScopeResolver


def test_normalize_asm_path_uppercases_intermediates() -> None:
    """Uppercase disk group and intermediate dirs; keep final segment."""
    assert (
        WalkScopeResolver.normalize_asm_path("+data/mydb/datafile/file.dbf")
        == "+DATA/MYDB/DATAFILE/file.dbf"
    )


def test_normalize_asm_path_non_asm_unchanged() -> None:
    """Leave non-ASM paths unchanged."""
    assert WalkScopeResolver.normalize_asm_path("relative/path") == "relative/path"


def test_normalize_disk_group_token() -> None:
    """Normalize disk group tokens to +NAME."""
    assert WalkScopeResolver.normalize_disk_group_token("data/") == "+DATA"
    assert WalkScopeResolver.normalize_disk_group_token("+fra") == "+FRA"


def test_asm_path_prefix_match_casefold() -> None:
    """Match prefixes case-insensitively."""
    assert WalkScopeResolver.asm_path_prefix_match("+DATA/MYDB/file", "+data/mydb")
    assert not WalkScopeResolver.asm_path_prefix_match("+DATA/OTHER/file", "+DATA/MYDB")


def test_asm_path_slug_and_format() -> None:
    """Slug and display helpers produce stable tokens."""
    assert WalkResult.asm_path_slug("+DATA/MYDB") == "DATA_MYDB"
    assert WalkResult.format_scan_path("+DATA/MYDB/DATAFILE/x") == "+DATA/MYDB"


def test_disk_group_from_asm_path() -> None:
    """Extract normalized disk group from alias source paths."""
    assert AliasRecord.disk_group_from_asm_path("+data/mydb/datafile/x.dbf") == "+DATA"
    assert AliasRecord.disk_group_from_asm_path("relative/path") == ""
    assert AliasRecord.disk_group_from_asm_path("+DATA") == "+DATA"


def test_alias_record_database_name_and_listing() -> None:
    """Cover database_name extraction and non-alias listing lines."""
    record = AliasRecord(
        file_type="DATAFILE",
        source_path="not-an-asm-path",
        target_path="also-not-asm",
    )
    assert record.database_name is None
    assert AliasRecord.from_listing_line("+DATA/DB", "TYPE file.dbf") is None
    alias = AliasRecord.from_listing_line(
        "+DATA/DB",
        "N DATAFILE custom.dbf => +DATA/DB/DATAFILE/custom.dbf",
    )
    assert alias is not None
    assert alias.source_path.endswith("custom.dbf")
    assert alias.database_name == "DB"

    dg_root = AliasRecord.from_listing_line(
        "+DATA",
        "N DATAFILE junk.dbf => +DATA/KEYTEST/DATAFILE/junk.dbf",
    )
    assert dg_root is not None
    assert dg_root.source_path == "+DATA/junk.dbf"
    assert dg_root.database_name == "KEYTEST"

    explicit = AliasRecord(
        file_type="DATAFILE",
        source_path="+DATA/junk.dbf",
        target_path="+DATA/OMF",
        database_name="KEYTEST",
    )
    assert explicit.database_name == "KEYTEST"
    assert AliasRecord.database_name_from_path("+DATA/junk.dbf") is None
    assert (
        AliasRecord.database_name_from_path("+DATA/KEYTEST/DATAFILE/junk.dbf")
        == "KEYTEST"
    )


def test_build_artifact_paths_without_sequence() -> None:
    """Default output paths omit sequence when None."""
    walk, fix, result = WalkResult.build_artifact_paths("+DATA/MYDB", date="20260101")
    assert walk == Path("logs/asm_walk_20260101_DATA_MYDB.txt")
    assert fix == Path("logs/asm_omf_fix_20260101_DATA_MYDB.sql")
    assert result == Path("logs/asm_result_20260101_DATA_MYDB.json")


def test_build_artifact_paths_with_sequence() -> None:
    """Sequenced output paths include a two-digit index."""
    walk, fix, result = WalkResult.build_artifact_paths(
        "+DATA/MYDB", date="20260101", sequence=3
    )
    assert walk.name == "asm_walk_20260101_03_DATA_MYDB.txt"
    assert fix.name == "asm_omf_fix_20260101_03_DATA_MYDB.sql"
    assert result.name == "asm_result_20260101_03_DATA_MYDB.json"


def test_expand_asm_walk_paths_dedupes() -> None:
    """Expand disk_groups × databases and drop casefold duplicates."""
    paths = WalkScopeResolver.expand_asm_walk_paths(["+DATA", "data"], ["MYDB"])
    assert paths == ["+DATA/MYDB"]
