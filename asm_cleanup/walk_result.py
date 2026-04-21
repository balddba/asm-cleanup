from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel


class WalkResult(BaseModel):
    """Per-path walk/analyze/fix outcome used for reporting.

    Attributes:
        asm_path (str): The ASM path that was walked/analyzed.
        display_path (str): Human-readable representation of the path for reporting.
        outfile (Path): Path to the output file containing analysis results.
        fixfile (Path): Path to the file containing fix/remediation SQL statements.
        files_examined (int): Number of files examined during the walk operation.
        alias_rows (int): Total number of alias rows found in the analysis.
        unique_aliases (int): Count of unique aliases discovered.
        fix_written (bool): Flag indicating whether fix file was successfully written.
    """

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
