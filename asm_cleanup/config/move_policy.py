"""OMF move SQL policy configuration."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator


class MovePolicy(BaseModel):
    """OMF move SQL policy: destination disk group and PDB GUID map.

    Attributes:
        destination_disk_group (str): Disk group for MOVE ... TO (required).
        pdb_guid_map (dict[str, str]): Optional manual ASM PDB GUID → PDB name.
        auto_pdb_guid_map (bool): When True, fetch GUID map via srvctl + sqlplus.
        online (bool): When True, emit MOVE ... ONLINE statements.
        lowercase_keywords (bool): When True, emit SQL keywords in lowercase.
        sql_header (str | None): Optional text prepended after the disclaimer.
        sql_footer (str | None): Optional text appended at the end of the script.
        spool_file (str | None): Optional SPOOL filename for SQL*Plus logging.
    """

    model_config = ConfigDict(extra="forbid")

    destination_disk_group: str
    pdb_guid_map: dict[str, str] = Field(default_factory=dict)
    auto_pdb_guid_map: bool = True
    online: bool = False
    lowercase_keywords: bool = False
    sql_header: str | None = None
    sql_footer: str | None = None
    spool_file: str | None = None

    @field_validator("destination_disk_group")
    @classmethod
    def _normalize_destination(cls, value: str) -> str:
        """Normalize destination disk group to +NAME uppercase.

        Args:
            value (str): Raw destination disk group.

        Returns:
            str: Normalized `+NAME` token.

        Raises:
            ValueError: If the value is empty.
        """
        text = value.strip()
        if not text:
            raise ValueError("destination_disk_group must be non-empty")
        if not text.startswith("+"):
            text = f"+{text}"
        name = text[1:].split("/", 1)[0].rstrip("/")
        if not name:
            raise ValueError("destination_disk_group must include a disk group name")
        return f"+{name.upper()}"
