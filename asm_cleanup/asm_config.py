"""YAML-backed ASM layout: multiple hosts, each with databases and SSH settings."""

from __future__ import annotations

from pathlib import Path
from typing import Self, Union

import yaml
from pydantic import BaseModel, Field, model_validator

from asm_cleanup import HostConfig


class AsmConfigFile(BaseModel):
    """Root object under the ``asm:`` key: named hosts with their own settings."""

    hosts: dict[str, HostConfig] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _nonempty_hosts(self) -> AsmConfigFile:
        if not self.hosts:
            raise ValueError("asm.hosts must contain at least one host entry")
        return self

    def get_host(self, host_id: str) -> HostConfig:
        """Return configuration for ``host_id`` (YAML key under ``hosts``)."""
        if host_id not in self.hosts:
            known = ", ".join(sorted(self.hosts))
            raise KeyError(f"Unknown host id {host_id!r}; configured hosts: {known}")
        return self.hosts[host_id]

    @classmethod
    def load(cls, path: Union[str, Path] = "config.yaml") -> Self:
        """Load from a YAML file containing an ``asm:`` mapping with a ``hosts`` table."""
        path = Path(path)
        with path.open("r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle)

        asm_section = data.get("asm") if isinstance(data, dict) else None
        if not asm_section:
            raise ValueError(f"'asm' section not found in {path}")

        if "hosts" not in asm_section and "host" in asm_section:
            raise ValueError(
                f"{path}: legacy single-host asm layout is not supported. "
                "Use asm.hosts.<id> with nested host, user, grid_home, databases, etc."
            )

        return cls(**asm_section)
