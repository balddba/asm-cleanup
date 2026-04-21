from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel


class WalkResult(BaseModel):
    """Per-path walk/analyze/fix outcome used for reporting."""

    asm_path: str
    display_path: str
    outfile: Path
    fixfile: Path
    files_examined: int
    alias_rows: int
    unique_aliases: int
    fix_written: bool

    class Config:
        frozen = True
