"""Raised when alias inventory has PDB GUIDs not present in move_policy."""

from __future__ import annotations


class UnmappedPdbGuidError(ValueError):
    """Raised when alias inventory has PDB GUIDs not present in move_policy.

    Attributes:
        unmapped (list[str]): Sorted unique unmapped GUID values.
    """

    def __init__(self, unmapped: list[str]) -> None:
        """Initialize with the list of unmapped GUIDs.

        Args:
            unmapped (list[str]): PDB GUIDs missing from pdb_guid_map.
        """
        self.unmapped = list(unmapped)
        guids = ", ".join(self.unmapped)
        super().__init__(
            f"inventory OK, emit blocked: {len(self.unmapped)} unmapped PDB GUID(s): "
            f"{guids}. Ensure move_policy.auto_pdb_guid_map can reach the DB "
            "(srvctl config database + sqlplus / as sysdba), or add them under "
            "move_policy.pdb_guid_map (SELECT name, RAWTOHEX(guid) FROM v$pdbs) "
            "then regenerate."
        )
