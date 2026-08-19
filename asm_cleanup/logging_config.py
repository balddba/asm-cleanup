"""Shared loguru configuration for CLI and web entrypoints."""

from __future__ import annotations

import os
import sys

from loguru import logger


def is_debug_enabled(debug: bool = False) -> bool:
    """Return True when debug logging should be enabled.

    Args:
        debug (bool): Explicit debug flag from the caller.

    Returns:
        bool: True when debug is requested via flag or ASM_CLEANUP_DEBUG.
    """
    if debug:
        return True
    return os.environ.get("ASM_CLEANUP_DEBUG", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def configure_logging(*, debug: bool = False) -> None:
    """Configure loguru handlers once for process entrypoints.

    Args:
        debug (bool): Enable DEBUG level when True.
    """
    logger.remove()
    logger.add(
        sys.stderr,
        level="DEBUG" if is_debug_enabled(debug) else "INFO",
    )
