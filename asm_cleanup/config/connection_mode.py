"""Connection mode enum for target profiles."""

from __future__ import annotations

from enum import Enum


class ConnectionMode(str, Enum):
    """How asmcmd is executed for a target profile."""

    ssh = "ssh"
    local = "local"
