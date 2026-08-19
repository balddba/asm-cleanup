"""Automated Oracle Grid / ASM discovery runner."""

from __future__ import annotations

from typing import TYPE_CHECKING

from asm_cleanup.discovery.discovery_error import DiscoveryError
from asm_cleanup.discovery.host_discovery import HostDiscovery

if TYPE_CHECKING:
    from asm_cleanup.discovery.target_discovery_runner import TargetDiscoveryRunner

__all__ = [
    "DiscoveryError",
    "HostDiscovery",
    "TargetDiscoveryRunner",
]


def __getattr__(name: str) -> object:
    """Lazy-load TargetDiscoveryRunner to avoid circular imports with ScanService.

    Args:
        name (str): Attribute name requested on this package.

    Returns:
        object: Resolved export.

    Raises:
        AttributeError: If `name` is not a known export.
    """
    if name == "TargetDiscoveryRunner":
        from asm_cleanup.discovery.target_discovery_runner import TargetDiscoveryRunner

        return TargetDiscoveryRunner
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
