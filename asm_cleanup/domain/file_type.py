"""Oracle ASM alias row type enum."""

from __future__ import annotations

from enum import Enum


class FileType(str, Enum):
    """Oracle ASM alias row type."""

    DATAFILE = "DATAFILE"
    TEMPFILE = "TEMPFILE"
