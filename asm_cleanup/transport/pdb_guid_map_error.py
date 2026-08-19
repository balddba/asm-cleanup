"""Raised when srvctl/sqlplus PDB GUID discovery fails."""

from __future__ import annotations


class PdbGuidMapError(RuntimeError):
    """Raised when srvctl/sqlplus PDB GUID discovery fails."""
