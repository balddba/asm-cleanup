"""In-memory ASM walk inventory model."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

from asm_cleanup.walk.directory_listing import DirectoryListing

if TYPE_CHECKING:
    from asm_cleanup.domain.alias_record import AliasRecord

_DATA_TEMPFILE_ROW = re.compile(r"^(DATAFILE|TEMPFILE)\b.*$")


class AsmInventory(BaseModel):
    """In-memory inventory produced by a walk (or loaded from a transcript).

    Attributes:
        root_path (str): Walk root ASM path.
        directories (list[DirectoryListing]): Listings in walk order.
        schema_version (int): Transcript/inventory schema version.
    """

    model_config = ConfigDict(frozen=True)

    root_path: str
    directories: list[DirectoryListing] = Field(default_factory=list)
    schema_version: int = 1

    def extract_aliases(self) -> list[AliasRecord]:
        """Extract deduplicated alias records from this inventory.

        Returns:
            list[AliasRecord]: Deduplicated aliases (casefold on source/target).
        """
        results: list[AliasRecord] = []
        seen: set[str] = set()
        for listing in self.directories:
            for record in listing.extract_aliases():
                dedupe_key = (
                    f"{record.file_type}|{record.source_path.casefold()}|"
                    f"{record.target_path.casefold()}"
                )
                if dedupe_key in seen:
                    continue
                seen.add(dedupe_key)
                results.append(record)
        return results

    def summarize_listing_stats(self) -> tuple[int, int]:
        """Count examined files and alias rows across directory listings.

        Returns:
            tuple[int, int]: (files_examined, alias_rows).
        """
        files_examined = 0
        alias_rows = 0
        for listing in self.directories:
            for line in listing.long_lines:
                text = line.strip()
                if not _DATA_TEMPFILE_ROW.match(text):
                    continue
                files_examined += 1
                if "=>" in text:
                    alias_rows += 1
        return files_examined, alias_rows


TRANSCRIPT_SCHEMA_VERSION = 1
TRANSCRIPT_HEADER = f"# asm-cleanup-transcript:{TRANSCRIPT_SCHEMA_VERSION}"
