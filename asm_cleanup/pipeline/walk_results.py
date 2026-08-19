"""Pipeline artifacts: output path naming and WalkResult DTO."""

from __future__ import annotations

import json
import re
from pathlib import Path

from loguru import logger
from pydantic import BaseModel, ConfigDict

from asm_cleanup.config.timezone import get_current_time

DEFAULT_LOG_DIR = Path("logs")


class WalkResult(BaseModel):
    """Immutable outcome for one walked or analyzed ASM path.

    Attributes:
        asm_path (str): ASM path that was walked or analyzed.
        display_path (str): Short path used in reports.
        outfile (Path): Walk transcript path.
        fixfile (Path): Generated fix SQL path.
        result_json (Path): Machine-readable summary JSON path.
        files_examined (int): DATAFILE/TEMPFILE rows counted.
        alias_rows (int): Rows containing alias mappings.
        unique_aliases (int): Deduplicated alias count.
        fix_written (bool): True when a fix SQL file was written.
        emit_blocked (str | None): Error message when SQL emit was blocked.
    """

    model_config = ConfigDict(frozen=True)

    asm_path: str
    display_path: str
    outfile: Path
    fixfile: Path
    result_json: Path
    files_examined: int
    alias_rows: int
    unique_aliases: int
    fix_written: bool
    emit_blocked: str | None = None

    @staticmethod
    def asm_path_slug(asm_path: str) -> str:
        """Convert an ASM path into a filesystem-safe filename token.

        Args:
            asm_path (str): ASM path such as `+DATA/MYDB/DATAFILE`.

        Returns:
            str: Slug such as `DATA_MYDB_DATAFILE`, or `asm` if empty after scrubbing.
        """
        p = asm_path.strip()
        p = p.removeprefix("+")
        slug = re.sub(r"[^0-9A-Za-z._-]", "_", p)
        return re.sub(r"_+", "_", slug).strip("_") or "asm"

    @staticmethod
    def format_scan_path(asm_path: str) -> str:
        """Return a short display path (disk group + first child).

        Args:
            asm_path (str): Full ASM path.

        Returns:
            str: Truncated path with at most two segments.
        """
        raw = asm_path.strip()
        if not raw.startswith("+"):
            return raw
        parts = [p for p in raw.split("/") if p]
        if len(parts) <= 2:
            return "/".join(parts)
        return "/".join(parts[:2])

    @classmethod
    def build_artifact_paths(
        cls,
        asm_path: str,
        *,
        date: str | None = None,
        sequence: int | None = None,
        log_dir: Path = DEFAULT_LOG_DIR,
    ) -> tuple[Path, Path, Path]:
        """Build default walk transcript, fix SQL, and JSON summary paths.

        Args:
            asm_path (str): ASM root used to derive the filename slug.
            date (str | None): YYYYMMDD stamp; defaults to today.
            sequence (int | None): Optional two-digit sequence for multi-path walks.
            log_dir (Path): Output directory (default: `logs`).

        Returns:
            tuple[Path, Path, Path]: (transcript, fix_sql, result_json).
        """
        stamp = date or get_current_time().strftime("%Y%m%d")
        slug = cls.asm_path_slug(asm_path)
        if sequence is not None:
            walk_stem = f"asm_walk_{stamp}_{sequence:02d}_{slug}"
            fix_stem = f"asm_omf_fix_{stamp}_{sequence:02d}_{slug}"
            json_stem = f"asm_result_{stamp}_{sequence:02d}_{slug}"
        else:
            walk_stem = f"asm_walk_{stamp}_{slug}"
            fix_stem = f"asm_omf_fix_{stamp}_{slug}"
            json_stem = f"asm_result_{stamp}_{slug}"
        return (
            log_dir / f"{walk_stem}.txt",
            log_dir / f"{fix_stem}.sql",
            log_dir / f"{json_stem}.json",
        )

    def write_json(self, path: Path) -> None:
        """Write a machine-readable WalkResult JSON artifact.

        Args:
            path (Path): Destination JSON path.
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = self.model_dump(mode="json")
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        logger.debug("wrote result json {}", path)
