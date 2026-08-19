"""One ASM directory and its asmcmd ls -l rows."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from asm_cleanup.domain.alias_record import AliasRecord


class DirectoryListing(BaseModel):
    """One ASM directory and its `asmcmd ls -l` rows.

    Attributes:
        path (str): Absolute ASM directory path.
        long_lines (list[str]): Raw `ls -l` lines for this directory.
    """

    model_config = ConfigDict(frozen=True)

    path: str
    long_lines: list[str] = Field(default_factory=list)

    def extract_aliases(self) -> list[AliasRecord]:
        """Extract alias records from this directory's long-listing rows.

        Returns:
            list[AliasRecord]: Alias rows found under this directory.
        """
        from asm_cleanup.domain.alias_record import AliasRecord

        results: list[AliasRecord] = []
        base = self.path.rstrip("/")
        for line in self.long_lines:
            record = AliasRecord.from_listing_line(base, line)
            if record is not None:
                results.append(record)
        return results
