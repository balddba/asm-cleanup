"""Pydantic schemas for web target create/update requests and list responses."""

from __future__ import annotations

from pydantic import BaseModel, Field


class TargetBase(BaseModel):
    """Request body for creating or updating a target profile.

    Attributes:
        name (str): Unique target profile name.
        host (str): SSH hostname or IP.
        user (str): SSH username.
        ssh_key_path (str | None): Optional path to SSH private key.
        ssh_key_content (str | None): Optional pasted SSH private key content.
        grid_home (str | None): Optional Grid Home path override.
        oracle_sid (str | None): Optional ASM SID override.
        destination_disk_group (str): Destination disk group for MOVE SQL.
        move_online (bool): When True, generate MOVE ... ONLINE SQL.
    """

    name: str = Field(..., min_length=1)
    host: str = Field(..., min_length=1)
    user: str = Field(..., min_length=1)
    ssh_key_path: str | None = None
    ssh_key_content: str | None = None
    grid_home: str | None = None
    oracle_sid: str | None = None
    destination_disk_group: str = "+DATA"
    move_online: bool = False


class TargetResponse(BaseModel):
    """Public target profile returned by the API (no private key material).

    Attributes:
        id (int): Target primary key.
        name (str): Unique target profile name.
        host (str): SSH hostname or IP.
        user (str): SSH username.
        ssh_key_path (str | None): Optional path to SSH private key.
        has_ssh_key (bool): True when a pasted private key is stored in the cryptfile.
        grid_home (str | None): Optional Grid Home path override.
        oracle_sid (str | None): Optional ASM SID override.
        destination_disk_group (str): Destination disk group for MOVE SQL.
        move_online (bool): When True, generate MOVE ... ONLINE SQL.
        created_at (str): ISO-8601 creation timestamp.
    """

    id: int
    name: str
    host: str
    user: str
    ssh_key_path: str | None = None
    has_ssh_key: bool = False
    grid_home: str | None = None
    oracle_sid: str | None = None
    destination_disk_group: str
    move_online: bool = False
    created_at: str
