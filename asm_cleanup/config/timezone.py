"""Timezone configuration helpers for date and time representation."""

from __future__ import annotations

import datetime
import os
import zoneinfo

from loguru import logger


def get_configured_timezone() -> zoneinfo.ZoneInfo:
    """Get the configured timezone from environment variable ASM_CLEANUP_TIMEZONE.

    Defaults to UTC if the variable is not set or the value is invalid.

    Returns:
        zoneinfo.ZoneInfo: The parsed timezone instance.
    """
    tz_str = os.environ.get("ASM_CLEANUP_TIMEZONE", "UTC").strip()
    if not tz_str:
        tz_str = "UTC"
    try:
        return zoneinfo.ZoneInfo(tz_str)
    except zoneinfo.ZoneInfoNotFoundError:
        logger.warning(
            "Configured timezone {!r} (from ASM_CLEANUP_TIMEZONE) not found. "
            "Falling back to UTC.",
            tz_str,
        )
        return zoneinfo.ZoneInfo("UTC")


def get_current_time() -> datetime.datetime:
    """Get the current datetime in the configured timezone.

    Returns:
        datetime.datetime: Timezone-aware datetime representing current time.
    """
    return datetime.datetime.now(get_configured_timezone())
