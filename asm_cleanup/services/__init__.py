"""Application services: connection factory, scan orchestration, enrichment."""

from __future__ import annotations

from typing import TYPE_CHECKING

from asm_cleanup.services.connection_factory import ConnectionFactory

if TYPE_CHECKING:
    from asm_cleanup.services.alias_enrichment import AliasEnricher
    from asm_cleanup.services.scan_service import ScanService
    from asm_cleanup.services.target_mapper import TargetMapper

__all__ = [
    "AliasEnricher",
    "ConnectionFactory",
    "ScanService",
    "TargetMapper",
]


def __getattr__(name: str) -> object:
    """Lazy-load scan orchestration exports to avoid circular imports.

    Args:
        name (str): Attribute name requested on this package.

    Returns:
        object: Resolved export.

    Raises:
        AttributeError: If `name` is not a known export.
    """
    if name == "AliasEnricher":
        from asm_cleanup.services.alias_enrichment import AliasEnricher

        return AliasEnricher
    if name == "ScanService":
        from asm_cleanup.services.scan_service import ScanService

        return ScanService
    if name == "TargetMapper":
        from asm_cleanup.services.target_mapper import TargetMapper

        return TargetMapper
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
