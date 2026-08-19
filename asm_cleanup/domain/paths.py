"""ASM path normalization and walk-root expansion helpers."""

from __future__ import annotations

import re

_DISKGROUP_TOKEN = re.compile(r"^\+?[A-Za-z0-9_$#-]+/?$")


def normalize_disk_group_token(dg: str) -> str:
    """Normalize a disk group token to `+NAME` uppercase form.

    Args:
        dg (str): Raw disk group token (optional leading `+`, trailing `/`, mixed case).

    Returns:
        str: Normalized disk group such as `+DATA`.
    """
    dg = dg.strip()
    if not dg.startswith("+"):
        dg = f"+{dg}"
    dg = dg.rstrip("/")
    name = dg[1:].split("/", 1)[0]
    return f"+{name.upper()}"


def normalize_asm_path(path: str) -> str:
    """Normalize walk-root ASM paths for comparisons.

    Uppercases the disk group and intermediate directories; leaves the final segment
    unchanged. Do not use for MOVE DATAFILE source strings — Oracle matches
    dictionary casing. Use asm_path_prefix_match for comparisons.

    Args:
        path (str): ASM path to normalize.

    Returns:
        str: Normalized path, or unchanged if it does not start with `+`.
    """
    if not path.startswith("+"):
        return path
    parts = path.split("/")
    normalized: list[str] = [parts[0].upper()]
    for part in parts[1:-1]:
        normalized.append(part.upper())
    if len(parts) > 1:
        normalized.append(parts[-1])
    return "/".join(normalized)


def asm_path_prefix_match(path: str, prefix: str) -> bool:
    """Return True if path starts with prefix (case-insensitive).

    Args:
        path (str): ASM path to test.
        prefix (str): Expected path prefix.

    Returns:
        bool: True when path is under prefix.
    """
    return path.strip().casefold().startswith(prefix.strip().casefold())


def expand_asm_walk_paths(disk_groups: list[str], databases: list[str]) -> list[str]:
    """Build `+DISKGROUP/DATABASE` paths from disk groups and database names.

    Args:
        disk_groups (list[str]): Disk group tokens (configured or discovered).
        databases (list[str]): Database names in scope.

    Returns:
        list[str]: Deduplicated normalized ASM paths (order preserved).
    """
    paths: list[str] = []
    seen: set[str] = set()
    for dg in disk_groups:
        ndg = normalize_disk_group_token(dg)
        for db in databases:
            p = normalize_asm_path(f"{ndg}/{db}")
            key = p.casefold()
            if key not in seen:
                seen.add(key)
                paths.append(p)
    return paths


def is_diskgroup_token(token: str) -> bool:
    """Return True when token looks like an asmcmd disk-group listing entry.

    Args:
        token (str): Raw line from `asmcmd ls +`.

    Returns:
        bool: True if the token matches a disk-group name pattern.
    """
    return bool(_DISKGROUP_TOKEN.fullmatch(token.strip()))
