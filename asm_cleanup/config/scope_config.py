"""Walk scope configuration for target profiles."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ScopeConfig(BaseModel):
    """Walk scope: disk groups, databases, and optional default path.

    Attributes:
        disk_groups (list[str]): ASM disk groups (empty may mean discover all).
        databases (list[str]): Database names for path expansion.
        exclude_databases (list[str]): Database names to omit from expansion.
        exclude_paths (list[str]): ASM path prefixes to omit.
        default_asm_path (str | None): Single walk root when asm_path is omitted.
        max_depth (int | None): Optional recursion depth limit (None = unlimited).
    """

    model_config = ConfigDict(extra="forbid")

    disk_groups: list[str] = Field(default_factory=list)
    databases: list[str] = Field(default_factory=list)
    exclude_databases: list[str] = Field(default_factory=list)
    exclude_paths: list[str] = Field(default_factory=list)
    default_asm_path: str | None = None
    max_depth: int | None = None

    @field_validator("max_depth")
    @classmethod
    def _max_depth_non_negative(cls, value: int | None) -> int | None:
        """Reject negative depth limits.

        Args:
            value (int | None): Configured max depth.

        Returns:
            int | None: Validated depth.

        Raises:
            ValueError: If depth is negative.
        """
        if value is not None and value < 0:
            raise ValueError("max_depth must be >= 0")
        return value
