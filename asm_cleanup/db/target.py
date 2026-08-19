"""Remote database host target connection profile model."""

from __future__ import annotations

from sqlalchemy import Boolean, Column, DateTime, Integer, String, Text
from sqlalchemy.orm import relationship

from asm_cleanup.config.timezone import get_current_time
from asm_cleanup.db.base import Base


class Target(Base):
    """Represents a remote database host target connection profile.

    Attributes:
        id (int): Primary key.
        name (str): Unique, user-defined profile name.
        host (str): SSH hostname or IP.
        user (str): SSH username (typically 'oracle' or 'grid').
        ssh_key_path (str | None): Optional absolute path to SSH private key.
        ssh_key_content (str | None): Optional leftover plaintext (migrated into cryptfile).
        grid_home (str | None): Optional Grid Home path override.
        oracle_sid (str | None): Optional ASM SID override (e.g. +ASM1).
        destination_disk_group (str): Target disk group for MOVE commands (e.g. +DATA).
        move_online (bool): When True, generated MOVE SQL includes ONLINE.
        created_at (datetime.datetime): When target was added.
        updated_at (datetime.datetime): When target was last updated.
    """

    __tablename__ = "targets"

    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True, nullable=False, index=True)
    host = Column(String, nullable=False)
    user = Column(String, nullable=False)
    ssh_key_path = Column(String, nullable=True)
    ssh_key_content = Column(Text, nullable=True)
    grid_home = Column(String, nullable=True)
    oracle_sid = Column(String, nullable=True)
    destination_disk_group = Column(String, nullable=False, default="+DATA")
    move_online = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, default=get_current_time, nullable=False)
    updated_at = Column(
        DateTime,
        default=get_current_time,
        onupdate=get_current_time,
        nullable=False,
    )

    scans = relationship("Scan", back_populates="target", cascade="all, delete-orphan")
