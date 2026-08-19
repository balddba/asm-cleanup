"""Tests for Grid env wrapping and config model validation."""

import pytest
from pydantic import ValidationError

from asm_cleanup.config.connection_config import ConnectionConfig
from asm_cleanup.config.connection_mode import ConnectionMode
from asm_cleanup.config.move_policy import MovePolicy
from asm_cleanup.config.scope_config import ScopeConfig
from asm_cleanup.config.timezone import get_configured_timezone, get_current_time
from asm_cleanup.transport.command_result import CommandResult
from asm_cleanup.transport.ssh import SshGridAdapter
from asm_cleanup.transport.text import strip_ansi
from asm_cleanup.walk.asm_inventory import AsmInventory
from asm_cleanup.walk.directory_listing import DirectoryListing
from asm_cleanup.walk.transcript import inventory_to_transcript, transcript_to_inventory


def test_wrap_remote_grid_command_oraenv() -> None:
    """Wrap commands with oraenv when use_oraenv is set."""
    cfg = ConnectionConfig(
        mode=ConnectionMode.ssh,
        host="grid.example.com",
        user="oracle",
        grid_home="/u01/app/grid",
        oracle_sid="+ASM",
        use_oraenv=True,
    )
    script = SshGridAdapter.wrap_remote_grid_command(cfg, "asmcmd ls +")
    assert "ORAENV_ASK=NO" in script
    assert ". /usr/local/bin/oraenv >/dev/null" in script
    assert script.strip().endswith("asmcmd ls +")


def test_wrap_remote_grid_command_simple_env() -> None:
    """Use simple ORACLE_HOME/SID exports when oraenv is off."""
    cfg = ConnectionConfig(
        mode=ConnectionMode.ssh,
        host="grid.example.com",
        user="oracle",
        grid_home="/u01/app/grid",
        oracle_sid="+ASM1",
        oracle_home="/u01/app/19c/grid",
    )
    script = SshGridAdapter.wrap_remote_grid_command(cfg, "asmcmd ls +")
    assert "export ORACLE_HOME=/u01/app/19c/grid" in script
    assert "export ORACLE_SID=+ASM1" in script


def test_wrap_remote_grid_command_custom_init() -> None:
    """Prefer asm_env_init over oraenv/simple env."""
    cfg = ConnectionConfig(
        mode=ConnectionMode.ssh,
        host="grid.example.com",
        user="oracle",
        grid_home="/u01/app/grid",
        asm_env_init="export FOO=1",
    )
    script = SshGridAdapter.wrap_remote_grid_command(cfg, "asmcmd ls +")
    assert script.startswith("export FOO=1\n")


def test_strip_ansi_removes_color_codes() -> None:
    """Remove CSI color sequences from shell banner text."""
    raw = "\x1b[1;37m     DB     \x1b[m|\x1b[1;37m Version \x1b[m"
    assert strip_ansi(raw) == "     DB     | Version "


def test_command_result_strips_ansi_on_construct() -> None:
    """Sanitize ANSI out of CommandResult stdout and stderr."""
    result = CommandResult(
        argv=["asmcmd", "ls", "-l", "+DATA"],
        stdout="\x1b[1;37mType\x1b[m Name\n",
        stderr="\x1b[31mwarn\x1b[m\n",
        exit_code=0,
    )
    assert result.stdout == "Type Name\n"
    assert result.stderr == "warn\n"
    assert result.lines == ["Type Name"]


def test_transcript_roundtrip_strips_ansi() -> None:
    """Drop ANSI when serializing and when loading transcripts."""
    inventory = AsmInventory(
        root_path="+DATA/MYDB",
        directories=[
            DirectoryListing(
                path="+DATA/MYDB",
                long_lines=["\x1b[1;37mDATAFILE a.dbf => +DATA/X\x1b[m"],
            )
        ],
    )
    text = inventory_to_transcript(inventory)
    assert "\x1b" not in text
    assert "DATAFILE a.dbf => +DATA/X" in text
    loaded = transcript_to_inventory(text)
    assert loaded.directories[0].long_lines == ["DATAFILE a.dbf => +DATA/X"]


def test_scope_config_forbids_unknown_fields() -> None:
    """Reject unknown fields on ScopeConfig."""
    with pytest.raises(ValidationError):
        ScopeConfig.model_validate({"databases": ["MYDB"], "unexpected_field": "x"})


def test_use_oraenv_requires_sid() -> None:
    """Require oracle_sid when use_oraenv is true."""
    with pytest.raises(ValidationError):
        ConnectionConfig(
            mode=ConnectionMode.ssh,
            host="h",
            user="u",
            grid_home="/grid",
            use_oraenv=True,
        )


def test_move_policy_requires_destination() -> None:
    """Require destination_disk_group on MovePolicy."""
    with pytest.raises(ValidationError):
        MovePolicy.model_validate({})


def test_local_connection_skips_ssh_fields() -> None:
    """Allow local mode without host/user/grid_home."""
    connection = ConnectionConfig.model_validate({"mode": "local"})
    policy = MovePolicy.model_validate({"destination_disk_group": "data"})
    assert connection.mode is ConnectionMode.local
    assert policy.destination_disk_group == "+DATA"


def test_ssh_connection_requires_host_user_grid_home() -> None:
    """Reject SSH mode when required connection fields are missing."""
    with pytest.raises(ValidationError, match="host"):
        ConnectionConfig.model_validate({"mode": "ssh", "user": "u", "grid_home": "/g"})


def test_move_policy_rejects_empty_destination() -> None:
    """Reject blank or plus-only destination disk groups."""
    with pytest.raises(ValidationError, match="non-empty"):
        MovePolicy.model_validate({"destination_disk_group": "   "})
    with pytest.raises(ValidationError, match="disk group name"):
        MovePolicy.model_validate({"destination_disk_group": "+"})


def test_scope_config_rejects_negative_max_depth() -> None:
    """Reject negative max_depth values."""
    with pytest.raises(ValidationError, match="max_depth"):
        ScopeConfig.model_validate({"max_depth": -1})


def test_scope_exclusions_and_sql_customizations_config() -> None:
    """Validate scope exclusions and SQL customization options."""
    scope = ScopeConfig.model_validate(
        {
            "exclude_databases": ["legacy_db"],
            "exclude_paths": ["+DATA/LEGACY/*"],
        }
    )
    policy = MovePolicy.model_validate(
        {
            "destination_disk_group": "+DATA",
            "lowercase_keywords": True,
            "sql_header": "ALTER SESSION SET CURRENT_SCHEMA=SYS;",
            "sql_footer": "EXIT;",
            "spool_file": "move.log",
        }
    )
    assert scope.exclude_databases == ["legacy_db"]
    assert scope.exclude_paths == ["+DATA/LEGACY/*"]
    assert policy.lowercase_keywords is True
    assert policy.sql_header == "ALTER SESSION SET CURRENT_SCHEMA=SYS;"
    assert policy.sql_footer == "EXIT;"
    assert policy.spool_file == "move.log"


def test_timezone_config_default_utc(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fallback to UTC timezone when environment variable is unset or empty."""
    monkeypatch.delenv("ASM_CLEANUP_TIMEZONE", raising=False)
    tz = get_configured_timezone()
    assert tz.key == "UTC"

    now = get_current_time()
    assert now.tzinfo is not None
    assert now.tzinfo.utcoffset(now) is not None


def test_timezone_config_valid(monkeypatch: pytest.MonkeyPatch) -> None:
    """Parse valid timezone from environment variable."""
    monkeypatch.setenv("ASM_CLEANUP_TIMEZONE", "America/Detroit")
    tz = get_configured_timezone()
    assert tz.key == "America/Detroit"

    now = get_current_time()
    assert now.tzinfo == tz


def test_timezone_config_invalid(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fallback to UTC timezone and log warning when variable is invalid."""
    monkeypatch.setenv("ASM_CLEANUP_TIMEZONE", "Invalid/Timezone")
    tz = get_configured_timezone()
    assert tz.key == "UTC"
