"""Discovered ASM alias row persisted for a scan."""

from __future__ import annotations

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import relationship

from asm_cleanup.config.timezone import get_current_time
from asm_cleanup.db.base import Base


class ScanAlias(Base):
    """Represents one discovered ASM alias datafile/tempfile from a Scan.

    Attributes:
        id (int): Primary key.
        scan_id (int): Foreign key to scans.
        database_name (str): Discovered unique name of the database.
        container_name (str): CDB$ROOT or the PDB name.
        file_type (str): 'DATAFILE' or 'TEMPFILE'.
        source_path (str): Original alias file path (e.g. +DATA/MYDB/datafile/a.dbf).
        target_path (str): Target OMF file path (e.g. +DATA/MYDB/DATAFILE/SYSTEM.255.1).
        pdb_guid (str | None): Uppercase 32-character PDB GUID.
        created_at (datetime.datetime): Creation timestamp.
    """

    __tablename__ = "scan_aliases"

    id = Column(Integer, primary_key=True)
    scan_id = Column(
        Integer, ForeignKey("scans.id", ondelete="CASCADE"), nullable=False
    )
    database_name = Column(String, nullable=False)
    container_name = Column(String, nullable=False)
    file_type = Column(String, nullable=False)
    source_path = Column(String, nullable=False)
    target_path = Column(String, nullable=False)
    pdb_guid = Column(String, nullable=True)
    created_at = Column(DateTime, default=get_current_time, nullable=False)

    scan = relationship("Scan", back_populates="aliases")
