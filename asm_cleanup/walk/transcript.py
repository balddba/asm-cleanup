"""Serialize and load versioned ASM walk transcripts."""

from __future__ import annotations

from pathlib import Path

from asm_cleanup.transport.text import strip_ansi
from asm_cleanup.walk.asm_inventory import (
    TRANSCRIPT_HEADER,
    TRANSCRIPT_SCHEMA_VERSION,
    AsmInventory,
)
from asm_cleanup.walk.directory_listing import DirectoryListing


def inventory_to_transcript(inventory: AsmInventory) -> str:
    """Serialize an inventory to the versioned transcript text format.

    Args:
        inventory (AsmInventory): Structured walk inventory.

    Returns:
        str: Transcript text including the schema header.
    """
    lines: list[str] = [TRANSCRIPT_HEADER]
    for listing in inventory.directories:
        lines.append(f"DIR: {listing.path}")
        lines.append("-" * 60)
        lines.extend(strip_ansi(line) for line in listing.long_lines)
    return "\n".join(lines) + ("\n" if lines else "")


def transcript_to_inventory(
    text: str,
    *,
    root_path: str | None = None,
) -> AsmInventory:
    """Parse a versioned (or legacy DIR:) transcript into an AsmInventory.

    Args:
        text (str): Transcript contents.
        root_path (str | None): Override root; defaults to first DIR path or `unknown`.

    Returns:
        AsmInventory: Structured inventory rebuilt from directory listings.

    Raises:
        ValueError: If the transcript header declares an unsupported schema version.
    """
    directories: list[DirectoryListing] = []
    current_path: str | None = None
    current_lines: list[str] = []
    schema_version = TRANSCRIPT_SCHEMA_VERSION

    def flush() -> None:
        """Append the current directory listing buffer if open."""
        nonlocal current_path, current_lines
        if current_path is not None:
            directories.append(
                DirectoryListing(path=current_path, long_lines=list(current_lines))
            )
        current_path = None
        current_lines = []

    for raw in text.splitlines():
        line = raw.rstrip("\n")
        stripped = line.strip()
        if stripped.startswith("# asm-cleanup-transcript:"):
            try:
                schema_version = int(stripped.split(":", 1)[1])
            except (IndexError, ValueError) as exc:
                raise ValueError(f"Invalid transcript header: {stripped!r}") from exc
            if schema_version != TRANSCRIPT_SCHEMA_VERSION:
                raise ValueError(
                    f"Unsupported transcript schema version {schema_version}; "
                    f"expected {TRANSCRIPT_SCHEMA_VERSION}"
                )
            continue
        if stripped.startswith("DIR:"):
            flush()
            current_path = stripped.removeprefix("DIR:").strip()
            current_lines = []
            continue
        if current_path is not None:
            if stripped == "-" * 60:
                continue
            current_lines.append(line)
    flush()

    resolved_root = root_path
    if not resolved_root:
        resolved_root = directories[0].path if directories else "unknown"
    return AsmInventory(
        root_path=resolved_root,
        directories=directories,
        schema_version=schema_version,
    )


def load_transcript(path: str | Path, *, root_path: str | None = None) -> AsmInventory:
    """Load a transcript file into an AsmInventory.

    Args:
        path (str | Path): Path to a walk transcript.
        root_path (str | None): Optional root override.

    Returns:
        AsmInventory: Parsed inventory.
    """
    text = strip_ansi(Path(path).read_text(encoding="utf-8"))
    return transcript_to_inventory(text, root_path=root_path)


def write_transcript(path: str | Path, inventory: AsmInventory) -> None:
    """Write an inventory as a versioned transcript file.

    Args:
        path (str | Path): Destination transcript path.
        inventory (AsmInventory): Inventory to serialize.
    """
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(inventory_to_transcript(inventory), encoding="utf-8")
