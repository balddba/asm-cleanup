"""OMF move SQL emitter driven by MovePolicy."""

from __future__ import annotations

import json
import re

from asm_cleanup.config.move_policy import MovePolicy
from asm_cleanup.domain.alias_record import AliasRecord
from asm_cleanup.sql.unmapped_pdb_guid_error import UnmappedPdbGuidError

SCRIPT_DISCLAIMER = """\
-- =============================================================================
-- WARNING: REVIEW BEFORE EXECUTION
-- =============================================================================
-- This script was generated automatically. You must read, understand, and
-- verify every statement before running it against a database.
-- Running unverified SQL can cause data loss, outages, or other damage.
-- The author of this tool accepts no responsibility for any damages arising
-- from use or misuse of this generated script. Use entirely at your own risk.
-- ============================================================================="""


class MoveSqlEmitter:
    """Pure emitter of review-only OMF MOVE SQL from alias records.

    Attributes:
        policy (MovePolicy): Destination disk group and PDB GUID map.
    """

    def __init__(self, policy: MovePolicy) -> None:
        """Initialize the emitter.

        Args:
            policy (MovePolicy): Required destination DG and optional PDB map.
        """
        self.policy = policy

    @staticmethod
    def sql_alter_session_set_container(
        pdb_name: str, *, lowercase: bool = False
    ) -> str:
        """Build an ALTER SESSION SET CONTAINER statement with identifier quoting.

        Args:
            pdb_name (str): Target PDB or CDB$ROOT name.
            lowercase (bool): When True, emit statement in lowercase.

        Returns:
            str: Comment plus SQL statement ending with a semicolon.
        """
        if re.fullmatch(r"[A-Za-z][A-Za-z0-9_$#]*", pdb_name):
            clause = pdb_name
        else:
            clause = '"' + pdb_name.replace('"', '""') + '"'
        kw = (
            "alter session set container"
            if lowercase
            else "ALTER SESSION SET CONTAINER"
        )
        return (
            f"-- Switch to container {clause} so following MOVE statements "
            f"run in that PDB (or CDB$ROOT).\n"
            f"{kw} = {clause};"
        )

    @staticmethod
    def sort_alias_records(records: list[AliasRecord]) -> list[AliasRecord]:
        """Order aliases by disk group, database, PDB GUID, then source path.

        Args:
            records (list[AliasRecord]): Unordered alias records.

        Returns:
            list[AliasRecord]: Deterministically sorted copy.
        """
        return sorted(
            records,
            key=lambda r: (
                r.disk_group.casefold(),
                (r.database_name or "").casefold(),
                r.pdb_guid or "",
                r.source_path.casefold(),
            ),
        )

    def find_unmapped_pdb_guids(self, records: list[AliasRecord]) -> list[str]:
        """Return sorted unique PDB GUIDs missing from the move policy map.

        Args:
            records (list[AliasRecord]): Alias records from inventory.

        Returns:
            list[str]: Sorted uppercase GUIDs that are not mapped.
        """
        mapped = {k.upper() for k in self.policy.pdb_guid_map}
        found = {r.pdb_guid.upper() for r in records if r.pdb_guid}
        return sorted(found - mapped)

    def emit(self, records: list[AliasRecord], *, fail_on_unmapped: bool = True) -> str:
        """Generate OMF MOVE SQL with optional PDB container switches.

        Args:
            records (list[AliasRecord]): Alias rows from inventory extraction.
            fail_on_unmapped (bool): Raise when PDB GUIDs are unmapped (default True).

        Returns:
            str: SQL script text (statements separated by blank lines).

        Raises:
            UnmappedPdbGuidError: If fail_on_unmapped and any GUID is missing from the map.
        """
        unmapped = self.find_unmapped_pdb_guids(records)
        if fail_on_unmapped and unmapped:
            raise UnmappedPdbGuidError(unmapped)

        guid_to_name = {k.upper(): v for k, v in self.policy.pdb_guid_map.items()}
        destination = self.policy.destination_disk_group
        ordered = self.sort_alias_records(records)

        def resolved_container(guid: str | None) -> str:
            """Resolve a PDB GUID to a container label.

            Args:
                guid (str | None): 32-hex GUID or None for CDB$ROOT.

            Returns:
                str: PDB name or `CDB$ROOT`.
            """
            if not guid:
                return "CDB$ROOT"
            mapped = guid_to_name.get(guid)
            if mapped is not None:
                return mapped
            return f"PDB_GUID_{guid[:8]}"

        def kw(word: str) -> str:
            return word.lower() if self.policy.lowercase_keywords else word.upper()

        sql: list[str] = [SCRIPT_DISCLAIMER]
        if self.policy.sql_header:
            sql.append(self.policy.sql_header.strip())

        if self.policy.spool_file:
            sql.append(f"{kw('SPOOL')} {self.policy.spool_file};")

        last_container: str | None = "__INIT__"

        for record in ordered:
            label = resolved_container(record.pdb_guid)
            if label != last_container:
                if not (last_container == "__INIT__" and label == "CDB$ROOT"):
                    sql.append(
                        self.sql_alter_session_set_container(
                            label, lowercase=self.policy.lowercase_keywords
                        )
                    )
                last_container = label

            kind = record.file_type.value
            online_clause = f" {kw('ONLINE')}" if self.policy.online else ""
            stmt = (
                f"{kw('ALTER DATABASE MOVE')} {kw(kind)} '{record.source_path}' "
                f"{kw('TO')} '{destination}'{online_clause};"
            )
            sql.append(
                f"""-- =========================================================
-- FIX {kind}
-- Source: {record.source_path}
-- Target: {record.target_path}
-- =========================================================
{stmt}"""
            )

        if self.policy.spool_file:
            sql.append(f"{kw('SPOOL OFF')};")

        if self.policy.sql_footer:
            sql.append(self.policy.sql_footer.strip())

        return "\n\n".join(sql)

    def emit_by_database(
        self, records: list[AliasRecord], *, fail_on_unmapped: bool = True
    ) -> dict[str, str]:
        """Generate one OMF MOVE SQL script per database unique name.

        Args:
            records (list[AliasRecord]): Alias rows from inventory extraction.
            fail_on_unmapped (bool): Raise when PDB GUIDs are unmapped (default True).

        Returns:
            dict[str, str]: Map of database unique name → SQL script text.

        Raises:
            UnmappedPdbGuidError: If fail_on_unmapped and any GUID is missing from the map.
        """
        groups: dict[str, list[AliasRecord]] = {}
        for record in records:
            name = record.database_name or "UNKNOWN"
            groups.setdefault(name, []).append(record)

        return {
            name: self.emit(group, fail_on_unmapped=fail_on_unmapped)
            for name, group in sorted(
                groups.items(), key=lambda item: item[0].casefold()
            )
        }


def parse_generated_sql_by_database(raw: str | None) -> dict[str, str]:
    """Parse stored generated SQL into a database → script map when JSON-encoded.

    Args:
        raw (str | None): Stored `Scan.generated_sql` value.

    Returns:
        dict[str, str]: Per-database scripts, or empty when `raw` is plain SQL/text.
    """
    if not raw:
        return {}
    text = raw.strip()
    if not text.startswith("{"):
        return {}
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return {}
    if not isinstance(parsed, dict) or not parsed:
        return {}
    if not all(
        isinstance(key, str) and isinstance(value, str) for key, value in parsed.items()
    ):
        return {}
    return parsed


def format_generated_sql_for_display(raw: str | None) -> str:
    """Format stored generated SQL for CLI or single-pane display.

    Args:
        raw (str | None): Stored `Scan.generated_sql` value.

    Returns:
        str: Plain script text, or per-database scripts joined with headers.
    """
    by_database = parse_generated_sql_by_database(raw)
    if not by_database:
        return raw or ""
    if len(by_database) == 1:
        return next(iter(by_database.values()))
    parts: list[str] = []
    for name, script in by_database.items():
        parts.append(f"-- Database: {name}\n\n{script}")
    return "\n\n".join(parts)


__all__ = [
    "SCRIPT_DISCLAIMER",
    "MoveSqlEmitter",
    "format_generated_sql_for_display",
    "parse_generated_sql_by_database",
]
