"""YAML ``targets`` map: one :class:`TargetConfig` per logical ASM/SSH target."""

from __future__ import annotations

if __name__ == "__main__" and __package__ is None:
    raise SystemExit(
        "Run this module as a package: python -m asm_cleanup.target_config "
        "(from the project root). Do not execute asm_cleanup/target_config.py by file path."
    )

from pathlib import Path
from typing import Optional, Self, Union

import yaml
from pydantic import BaseModel, Field, model_validator


class TargetConfig(BaseModel):
    """SSH + Grid/ASM settings for one target (one entry under ``targets`` in YAML).

    ``target_id`` is the YAML key (e.g. ``lab``). Other fields match the nested mapping.

    Args:
        target_id (str): Logical id (``targets.<id>`` in the config file).
        host (str): SSH hostname or IP.
        user (str): SSH username.
        grid_home (str): Grid Infrastructure ORACLE_HOME path (or GI root used with oraenv).
        monitor_interval (int): Seconds between monitoring checks (default: 60).
        monitor_count (int): Monitoring iterations (default: 1).
        connect_kwargs (dict[str, str]): Extra Fabric SSH kwargs (e.g. ``key_filename``).
        disk_groups (list[str]): ASM disk groups to walk (empty may mean discover all).
        databases (list[str]): Database names for path expansion and monitoring.
        default_asm_path (str | None): Default walk root when ``asm_path`` is omitted.
        oracle_sid (str | None): ASM instance for oraenv or simple exports.
        use_oraenv (bool): Source ``oraenv`` before remote ``asmcmd`` (default: False).
        oraenv_path (str): Path to ``oraenv`` script.
        oracle_home (str | None): Explicit GI home when not using oraenv.
        oracle_base (str | None): Optional ``ORACLE_BASE`` for simple-env mode.
        asm_env_init (str | None): Custom shell preamble instead of oraenv/short exports.
        pdb_guid_map (dict[str, str]): ASM PDB directory GUID to PDB name for generated SQL.
    """

    target_id: str
    host: str
    user: str
    grid_home: str
    monitor_interval: int = 60
    monitor_count: int = 1
    connect_kwargs: dict[str, str] = Field(default_factory=dict)
    disk_groups: list[str] = Field(default_factory=list)
    databases: list[str] = Field(default_factory=list)
    default_asm_path: Optional[str] = None
    oracle_sid: Optional[str] = None
    use_oraenv: bool = False
    oraenv_path: str = "/usr/local/bin/oraenv"
    oracle_home: Optional[str] = None
    oracle_base: Optional[str] = None
    asm_env_init: Optional[str] = None
    pdb_guid_map: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _oraenv_requires_sid(self) -> Self:
        if self.use_oraenv and not self.oracle_sid:
            raise ValueError("use_oraenv requires oracle_sid (e.g. +ASM or +ASM1)")
        return self

    @classmethod
    def from_target_entry(cls, target_id: str, data: object) -> Self:
        """Build from one value under ``targets`` (YAML key + nested mapping).

        Args:
            target_id (str): Key under ``targets``.
            data (object): Nested mapping; must not include ``target_id`` (it is injected).

        Returns:
            TargetConfig: Validated config with ``target_id`` set.

        Raises:
            TypeError: If ``data`` is not a mapping.
        """
        if not isinstance(data, dict):
            raise TypeError(
                f"targets.{target_id!r}: expected a mapping, got {type(data).__name__}"
            )
        if "target_id" in data:
            raise ValueError(
                f"targets.{target_id!r}: do not set 'target_id' in YAML (it comes from the key)"
            )
        return cls.model_validate({"target_id": str(target_id), **data})


def load_targets(path: Union[str, Path] = "config.yaml") -> dict[str, TargetConfig]:
    """Load top-level ``targets`` from a YAML file.

    Args:
        path (str | Path): YAML file whose root is a mapping containing ``targets``.

    Returns:
        dict[str, TargetConfig]: Non-empty ``target_id`` → config (``target_id`` matches each key).

    Raises:
        ValueError: If layout is invalid, ``targets`` is missing/empty, or legacy keys need migration.
    """
    path = Path(path)
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle)

    if not isinstance(data, dict):
        raise ValueError(f"{path}: YAML root must be a mapping with a top-level 'targets' key")

    if "targets" not in data:
        if "hosts" in data:
            raise ValueError(
                f"{path}: rename top-level 'hosts:' to 'targets:' (host entries are now targets)."
            )
        asm_block = data.get("asm")
        if isinstance(asm_block, dict) and "hosts" in asm_block:
            raise ValueError(
                f"{path}: use top-level 'targets:' instead of 'asm.hosts' "
                "(the 'asm' wrapper and 'hosts' key are no longer supported)."
            )
        raise ValueError(f"'targets' not found in {path}")

    raw = data["targets"]
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: targets must be a mapping (target id → settings)")
    if not raw:
        raise ValueError("targets must contain at least one entry")

    return {
        str(tid): TargetConfig.from_target_entry(str(tid), entry)
        for tid, entry in raw.items()
    }
