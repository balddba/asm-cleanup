"""Enrich walked ASM aliases with dictionary casing and persist ScanAlias rows."""

from __future__ import annotations

import re

from loguru import logger
from sqlalchemy.orm import Session

from asm_cleanup.db.scan import Scan
from asm_cleanup.db.scan_alias import ScanAlias
from asm_cleanup.domain.alias_record import AliasRecord
from asm_cleanup.domain.file_type import FileType

_OMF_FILENAME = re.compile(r"\.[0-9]+\.[0-9]+$")


class AliasEnricher:
    """Remap walked aliases to dictionary casing and persist ScanAlias rows.

    Attributes:
        session (Session): SQLAlchemy database session.
        scan (Scan): Scan whose aliases are persisted.
        destination_disk_group (str): Fallback destination for non-OMF files.
    """

    def __init__(
        self, session: Session, scan: Scan, destination_disk_group: str
    ) -> None:
        """Initialize the enricher.

        Args:
            session (Session): Database session.
            scan (Scan): Scan to attach ScanAlias rows to.
            destination_disk_group (str): Destination DG for standalone non-OMF files.
        """
        self.session = session
        self.scan = scan
        self.destination_disk_group = destination_disk_group

    def enrich_and_persist(
        self,
        walked: list[AliasRecord],
        *,
        all_db_files: dict[str, dict[str, str]],
        guid_pdb_map: dict[str, str],
    ) -> list[AliasRecord]:
        """Remap aliases to dictionary casing, add non-OMF files, persist ScanAlias rows.

        Args:
            walked (list[AliasRecord]): Aliases from the library ASM walk.
            all_db_files (dict[str, dict[str, str]]): Catalog paths keyed by casefold.
            guid_pdb_map (dict[str, str]): GUID → PDB name map.

        Returns:
            list[AliasRecord]: Enriched records for SQL emit (dictionary casing, non-OMF).
        """
        records: list[AliasRecord] = []
        matched_db_files: set[str] = set()
        dest_dg = self.destination_disk_group

        for alias in walked:
            norm_src = alias.source_path.strip().casefold()
            norm_tgt = alias.target_path.strip().casefold()
            matched_db_files.add(norm_src)
            matched_db_files.add(norm_tgt)

            db_name = None
            container_name = "CDB$ROOT"
            file_type: FileType = alias.file_type
            source_path = alias.source_path

            db_file_info = all_db_files.get(norm_src) or all_db_files.get(norm_tgt)
            if db_file_info:
                db_name = db_file_info["database"]
                container_name = db_file_info["con_name"]
                file_type = (
                    FileType.DATAFILE
                    if db_file_info["file_type"] == "DATAFILE"
                    else FileType.TEMPFILE
                )
                source_path = db_file_info["raw_path"]
            else:
                db_name = (
                    AliasRecord.database_name_from_path(alias.source_path)
                    or AliasRecord.database_name_from_path(alias.target_path)
                    or alias.database_name
                )

            guid = alias.pdb_guid or AliasRecord.pdb_guid_from_path(source_path)
            if guid:
                guid_upper = guid.upper()
                if guid_upper in guid_pdb_map:
                    container_name = guid_pdb_map[guid_upper]
                else:
                    container_name = f"PDB_GUID_{guid_upper[:8]}"

            resolved_db = db_name or "UNKNOWN"
            self.session.add(
                ScanAlias(
                    scan_id=self.scan.id,
                    database_name=resolved_db,
                    container_name=container_name,
                    file_type=file_type.value,
                    source_path=source_path,
                    target_path=alias.target_path,
                    pdb_guid=guid,
                )
            )
            records.append(
                AliasRecord(
                    file_type=file_type,
                    source_path=source_path,
                    target_path=alias.target_path,
                    pdb_guid=guid,
                    disk_group=alias.disk_group,
                    database_name=resolved_db,
                )
            )

        non_omf_count = 0
        for norm_path, info in all_db_files.items():
            if norm_path in matched_db_files:
                continue
            raw_path = info["raw_path"]
            filename = raw_path.strip().split("/")[-1]
            if _OMF_FILENAME.search(filename):
                continue

            non_omf_count += 1
            guid = AliasRecord.pdb_guid_from_path(raw_path)
            parts_src = [p for p in raw_path.split("/") if p]
            dg = parts_src[0] if parts_src and parts_src[0].startswith("+") else dest_dg
            file_type = (
                FileType.DATAFILE
                if info["file_type"] == "DATAFILE"
                else FileType.TEMPFILE
            )
            resolved_db = info["database"] or "UNKNOWN"
            self.session.add(
                ScanAlias(
                    scan_id=self.scan.id,
                    database_name=resolved_db,
                    container_name=info["con_name"],
                    file_type=file_type.value,
                    source_path=raw_path,
                    target_path=dest_dg,
                    pdb_guid=guid,
                )
            )
            records.append(
                AliasRecord(
                    file_type=file_type,
                    source_path=raw_path,
                    target_path=dest_dg,
                    pdb_guid=guid,
                    disk_group=dg,
                    database_name=resolved_db,
                )
            )

        if non_omf_count > 0:
            logger.info(
                "discovered {} standalone non-OMF files in database",
                non_omf_count,
            )
        return records
