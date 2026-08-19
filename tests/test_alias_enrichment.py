"""Unit tests for AliasEnricher catalog database-name propagation."""

from __future__ import annotations

from unittest.mock import MagicMock

from asm_cleanup.domain.alias_record import AliasRecord
from asm_cleanup.services.alias_enrichment import AliasEnricher


def test_enrich_uses_catalog_database_for_dg_root_alias() -> None:
    """Prefer discovered database unique name over a DG-root filename path."""
    session = MagicMock()
    scan = MagicMock()
    scan.id = 1
    enricher = AliasEnricher(session, scan, "+DATA")

    walked = [
        AliasRecord(
            file_type="DATAFILE",
            source_path="+DATA/junk.dbf",
            target_path="+DATA/KEYTEST/DATAFILE/JUNK.256.1",
            disk_group="+DATA",
        )
    ]
    all_db_files = {
        "+data/junk.dbf": {
            "con_name": "CDB$ROOT",
            "file_type": "DATAFILE",
            "database": "KEYTEST",
            "raw_path": "+DATA/junk.dbf",
        }
    }

    records = enricher.enrich_and_persist(
        walked,
        all_db_files=all_db_files,
        guid_pdb_map={},
    )

    assert len(records) == 1
    assert records[0].database_name == "KEYTEST"
    added = session.add.call_args[0][0]
    assert added.database_name == "KEYTEST"
