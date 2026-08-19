"""Alias record model and listing-line parsing."""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from asm_cleanup.domain.file_type import FileType

_GUID32 = re.compile(r"^[0-9A-Fa-f]{32}$")
_FILE_SUFFIX = re.compile(r"\.(dbf|tmp)$", re.IGNORECASE)


class AliasRecord(BaseModel):
    """One DATAFILE/TEMPFILE alias mapping discovered under an ASM directory.

    Attributes:
        file_type (FileType): DATAFILE or TEMPFILE.
        source_path (str): Full alias source path (original casing).
        target_path (str): OMF target path from the `=>` clause.
        pdb_guid (str | None): 32-hex PDB directory GUID when present.
        disk_group (str): Normalized disk group of the source path.
        database_name (str | None): Database unique name; catalog value preferred over path guess.
    """

    model_config = ConfigDict(frozen=True)

    file_type: FileType
    source_path: str
    target_path: str
    pdb_guid: str | None = None
    disk_group: str = ""
    database_name: str | None = Field(default=None)

    @model_validator(mode="before")
    @classmethod
    def _default_database_name(cls, data: Any) -> Any:
        """Fill database_name from ASM paths when not supplied explicitly.

        Prefers source_path, then target_path. Skips segments that look like
        filenames (e.g. junk.dbf at disk-group root).

        Args:
            data (Any): Raw model input.

        Returns:
            Any: Input with database_name set when it can be derived.
        """
        if not isinstance(data, dict):
            return data
        if data.get("database_name"):
            return data
        for key in ("source_path", "target_path"):
            path = data.get(key)
            if not isinstance(path, str):
                continue
            derived = AliasRecord.database_name_from_path(path)
            if derived is not None:
                data["database_name"] = derived
                break
        return data

    @staticmethod
    def database_name_from_path(asm_path: str) -> str | None:
        """Return the database unique-name segment from an ASM path when present.

        The second path segment after the disk group is treated as the database
        name unless it looks like a datafile/tempfile name (DG-root aliases).

        Args:
            asm_path (str): ASM path such as `+DATA/MYDB/DATAFILE/x.dbf`.

        Returns:
            str | None: Database unique-name segment, or None when absent/ambiguous.
        """
        parts = [p for p in asm_path.split("/") if p]
        if len(parts) < 2 or not parts[0].startswith("+"):
            return None
        candidate = parts[1]
        if _FILE_SUFFIX.search(candidate):
            return None
        if candidate.upper() in {member.value for member in FileType}:
            return None
        if _GUID32.match(candidate):
            return None
        return candidate

    @staticmethod
    def disk_group_from_asm_path(path: str) -> str:
        """Extract the disk group token from an ASM path.

        Args:
            path (str): ASM path such as `+DATA/MYDB/DATAFILE/x`.

        Returns:
            str: Normalized `+NAME`, or empty string if path has no disk group.
        """
        text = path.strip()
        if not text.startswith("+"):
            return ""
        dg = text.split("/", 1)[0].strip()
        if not dg.startswith("+"):
            dg = f"+{dg}"
        dg = dg.rstrip("/")
        name = dg[1:].split("/", 1)[0]
        return f"+{name.upper()}"

    @staticmethod
    def from_listing_line(base: str, line: str) -> AliasRecord | None:
        """Parse one `asmcmd ls -l` row into an alias record when it matches.

        Args:
            base (str): ASM directory path for the listing.
            line (str): Raw long-listing line.

        Returns:
            AliasRecord | None: Parsed alias row, or None when the line is not an alias.
        """
        match = re.search(r"(DATAFILE|TEMPFILE).*?\s(\S+)\s*=>\s*(\+\S+)", line.strip())
        if not match:
            return None
        file_type = FileType(match.group(1))
        filename = match.group(2)
        target = match.group(3).strip()
        full_source = f"{base.rstrip('/')}/{filename}".replace("//", "/")
        pdb_guid = AliasRecord.pdb_guid_from_path(
            full_source
        ) or AliasRecord.pdb_guid_from_path(target)
        database_name = AliasRecord.database_name_from_path(
            full_source
        ) or AliasRecord.database_name_from_path(target)
        return AliasRecord(
            file_type=file_type,
            source_path=full_source,
            target_path=target,
            pdb_guid=pdb_guid,
            disk_group=AliasRecord.disk_group_from_asm_path(full_source),
            database_name=database_name,
        )

    @staticmethod
    def pdb_guid_from_path(asm_path: str) -> str | None:
        """Extract a 32-character PDB directory GUID from an ASM path when present.

        Args:
            asm_path (str): ASM path that may include a PDB GUID directory.

        Returns:
            str | None: Uppercase GUID, or None for CDB$ROOT-style paths.
        """
        parts = asm_path.strip().split("/")
        for i, seg in enumerate(parts):
            if seg.upper() in {member.value for member in FileType} and i > 0:
                prev = parts[i - 1]
                if _GUID32.match(prev):
                    return prev.upper()
                return None
        return None


__all__ = ["AliasRecord"]
