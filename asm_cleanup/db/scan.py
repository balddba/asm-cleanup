"""Scan execution run model."""

from __future__ import annotations

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship

from asm_cleanup.config.timezone import get_current_time
from asm_cleanup.db.base import Base


class Scan(Base):
    """Represents an execution run of the automated discovery pipeline on a target.

    Attributes:
        id (int): Primary key.
        target_id (int): Foreign key to targets.
        status (str): Current status ('pending', 'running', 'completed', 'failed').
        progress_message (str | None): Human-readable phase text while the scan runs.
        error_message (str | None): Detailed error description when failed.
        grid_home (str | None): Discovered Grid Home on target.
        disk_groups (str | None): Discovered disk groups (JSON string).
        databases (str | None): Discovered databases and parameters (JSON string).
        generated_sql (str | None): Generated OMF MOVE SQL.
        created_at (datetime.datetime): Scan execution timestamp.
    """

    __tablename__ = "scans"

    id = Column(Integer, primary_key=True)
    target_id = Column(
        Integer, ForeignKey("targets.id", ondelete="CASCADE"), nullable=False
    )
    status = Column(String, nullable=False, default="pending")
    progress_message = Column(String, nullable=True)
    error_message = Column(Text, nullable=True)
    grid_home = Column(String, nullable=True)
    disk_groups = Column(Text, nullable=True)
    databases = Column(Text, nullable=True)
    generated_sql = Column(Text, nullable=True)
    created_at = Column(DateTime, default=get_current_time, nullable=False)

    target = relationship("Target", back_populates="scans")
    aliases = relationship(
        "ScanAlias", back_populates="scan", cascade="all, delete-orphan"
    )
