"""SSH or local execution settings for a target profile."""

from __future__ import annotations

from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from asm_cleanup.config.connection_mode import ConnectionMode


class ConnectionConfig(BaseModel):
    """SSH or local execution settings for a target.

    Attributes:
        mode (ConnectionMode): `ssh` or `local` execution.
        host (str | None): SSH hostname (required for ssh).
        user (str | None): SSH username (required for ssh).
        grid_home (str | None): Grid home path (required for ssh).
        connect_kwargs (dict[str, str]): Extra Fabric SSH kwargs (e.g. key_filename).
        oracle_sid (str | None): ASM instance SID for oraenv or simple exports.
        use_oraenv (bool): Source oraenv before remote asmcmd.
        oraenv_path (str): Path to the oraenv script.
        oracle_home (str | None): Explicit GI home when not using oraenv.
        oracle_base (str | None): Optional ORACLE_BASE for simple-env mode.
        asm_env_init (str | None): Custom shell preamble instead of oraenv/exports.
    """

    model_config = ConfigDict(extra="forbid")

    mode: ConnectionMode = ConnectionMode.ssh
    host: str | None = None
    user: str | None = None
    grid_home: str | None = None
    connect_kwargs: dict[str, str] = Field(default_factory=dict)
    oracle_sid: str | None = None
    use_oraenv: bool = False
    oraenv_path: str = "/usr/local/bin/oraenv"
    oracle_home: str | None = None
    oracle_base: str | None = None
    asm_env_init: str | None = None

    @model_validator(mode="after")
    def _validate_connection(self) -> Self:
        """Require SSH fields and oraenv SID when applicable.

        Returns:
            Self: Validated connection config.

        Raises:
            ValueError: If required SSH fields or oracle_sid are missing.
        """
        if self.mode is ConnectionMode.ssh:
            missing = [
                name
                for name, value in (
                    ("host", self.host),
                    ("user", self.user),
                    ("grid_home", self.grid_home),
                )
                if not value
            ]
            if missing:
                raise ValueError(
                    f"connection.mode=ssh requires fields: {', '.join(missing)}"
                )
        if self.use_oraenv and not self.oracle_sid:
            raise ValueError("use_oraenv requires oracle_sid (e.g. +ASM or +ASM1)")
        return self
