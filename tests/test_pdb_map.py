"""Tests for srvctl/sqlplus PDB GUID map collection."""

from __future__ import annotations

from pathlib import Path

import pytest

from asm_cleanup.config.connection_config import ConnectionConfig
from asm_cleanup.config.move_policy import MovePolicy
from asm_cleanup.config.scope_config import ScopeConfig
from asm_cleanup.domain.alias_record import AliasRecord
from asm_cleanup.pipeline.pipeline_orchestrator import PipelineOrchestrator
from asm_cleanup.transport.command_result import CommandResult
from asm_cleanup.transport.fake_asm_cmd_port import FakeAsmCmdPort
from asm_cleanup.transport.pdb_guid_map_collector import PdbGuidMapCollector
from asm_cleanup.transport.pdb_guid_map_error import PdbGuidMapError


def test_parse_srvctl_database_config_single_instance() -> None:
    """Parse Oracle home and instance from srvctl config output."""
    stdout = """
Database unique name: homelab
Database name: homelab
Oracle home: /u01/app/oracle/product/23ai/dbhome_1
Oracle user: oracle
Database instance: HOMELAB
"""
    home, sid = PdbGuidMapCollector.parse_srvctl_database_config(
        stdout, database="homelab"
    )
    assert home == "/u01/app/oracle/product/23ai/dbhome_1"
    assert sid == "HOMELAB"


def test_parse_srvctl_database_config_rac_uses_first_instance() -> None:
    """Fall back to the first RAC instance when listed."""
    stdout = """
Oracle home: /u01/app/oracle/product/19.0.0/dbhome_1
Database instances: orcl1,orcl2
"""
    home, sid = PdbGuidMapCollector.parse_srvctl_database_config(
        stdout, database="orcl"
    )
    assert home.endswith("dbhome_1")
    assert sid == "orcl1"


def test_parse_srvctl_database_config_missing_home_fails() -> None:
    """Raise when Oracle home is absent."""
    with pytest.raises(PdbGuidMapError, match="Oracle home"):
        PdbGuidMapCollector.parse_srvctl_database_config(
            "Database name: x\n", database="x"
        )


def test_parse_vpdbs_pipe_output() -> None:
    """Parse name|guid rows into an uppercase GUID map."""
    guid = "49CA3A83042A049CE0631A04010A1C7C"
    mapping = PdbGuidMapCollector.parse_vpdbs_pipe_output(
        f"PDB$SEED|{guid.lower()}\nTOOLKITPDB|{guid}\nCDB$ROOT|{'0' * 32}\n"
    )
    assert mapping[guid] == "TOOLKITPDB"
    assert mapping["0" * 32] == "CDB$ROOT"


def test_parse_vpdbs_pipe_output_empty_fails() -> None:
    """Raise when sqlplus returned no usable rows."""
    with pytest.raises(PdbGuidMapError, match="no name\\|guid"):
        PdbGuidMapCollector.parse_vpdbs_pipe_output("\nSQL> \n")


def test_build_sqlplus_vpdbs_script_sets_home_and_sid() -> None:
    """sqlplus script exports ORACLE_HOME/SID and queries v$pdbs."""
    script = PdbGuidMapCollector.build_sqlplus_vpdbs_script("/u01/dbhome", "HOMELAB")
    assert "export ORACLE_HOME=/u01/dbhome" in script
    assert "export ORACLE_SID=HOMELAB" in script
    assert "RAWTOHEX(guid)" in script
    assert "sqlplus -s / as sysdba" in script


def test_merge_pdb_guid_maps_yaml_wins() -> None:
    """YAML overrides auto-fetched names for the same GUID."""
    guid = "AABBCCDDEEFF00112233445566778899"
    merged = PdbGuidMapCollector.merge_pdb_guid_maps(
        {guid.lower(): "AUTO_PDB"},
        {guid: "YAML_PDB"},
    )
    assert merged[guid] == "YAML_PDB"


def test_collector_runs_srvctl_then_sqlplus() -> None:
    """Collector parses srvctl config then sqlplus output."""
    guid = "49CA3A83042A049CE0631A04010A1C7C"
    calls: list[tuple[str, bool]] = []

    def handler(script: str, *, use_grid_env: bool = True) -> CommandResult:
        calls.append((script, use_grid_env))
        if "srvctl" in script and "config database" in script:
            return CommandResult(
                argv=["srvctl"],
                stdout=(
                    "Oracle home: /u01/app/oracle/product/23ai/dbhome_1\n"
                    "Database instance: HOMELAB\n"
                ),
                exit_code=0,
            )
        return CommandResult(
            argv=["sqlplus"],
            stdout=f"TOOLKITPDB|{guid}\n",
            exit_code=0,
        )

    port = FakeAsmCmdPort(shell_handler=handler)
    collector = PdbGuidMapCollector(
        port,
        connection=ConnectionConfig.model_validate(
            {
                "mode": "ssh",
                "host": "grid.example.com",
                "user": "oracle",
                "grid_home": "/u01/app/grid",
            }
        ),
    )
    mapping = collector.collect("homelab")
    assert mapping[guid] == "TOOLKITPDB"
    assert calls[0][1] is True
    assert "/u01/app/grid/bin/srvctl" in calls[0][0]
    assert calls[1][1] is False
    assert "ORACLE_HOME=/u01/app/oracle/product/23ai/dbhome_1" in calls[1][0]
    # Cache hit does not re-run shell
    assert collector.collect("HOMELAB") == mapping
    assert len(calls) == 2


def test_process_path_auto_fetches_pdb_guid_map(tmp_path: Path) -> None:
    """Emit succeeds when auto_pdb_guid_map fills the GUID from v$pdbs."""
    guid = "49CA3A83042A049CE0631A04010A1C7C"
    listing = f"DATAFILE users.dbf => +DATA/HOMELAB/{guid}/DATAFILE/USERS.256.1\n"

    def shell_handler(script: str, *, use_grid_env: bool = True) -> CommandResult:
        del use_grid_env
        if "srvctl" in script:
            return CommandResult(
                argv=["srvctl"],
                stdout=("Oracle home: /u01/dbhome\nDatabase instance: HOMELAB\n"),
                exit_code=0,
            )
        return CommandResult(
            argv=["sqlplus"],
            stdout=f"TOOLKITPDB|{guid}\n",
            exit_code=0,
        )

    port = FakeAsmCmdPort(
        {
            ("asmcmd", "ls", "-l", "+DATA/HOMELAB"): CommandResult(
                argv=["asmcmd", "ls", "-l", "+DATA/HOMELAB"],
                stdout=listing,
                exit_code=0,
            ),
            ("asmcmd", "ls", "+DATA/HOMELAB"): CommandResult(
                argv=["asmcmd", "ls", "+DATA/HOMELAB"],
                stdout="",
                exit_code=0,
            ),
        },
        shell_handler=shell_handler,
    )
    scope = ScopeConfig.model_validate({"databases": ["homelab"]})
    move_policy = MovePolicy.model_validate(
        {
            "destination_disk_group": "+DATA",
            "auto_pdb_guid_map": True,
        }
    )
    orch = PipelineOrchestrator(port=port, scope=scope, move_policy=move_policy)
    result = orch.process_path(
        "+DATA/HOMELAB",
        do_walk=True,
        do_analyze=True,
        do_fix=True,
        outfile=tmp_path / "walk.txt",
        fixfile=tmp_path / "fix.sql",
        result_json=tmp_path / "result.json",
    )
    assert result.emit_blocked is None
    assert result.fix_written is True
    sql = (tmp_path / "fix.sql").read_text(encoding="utf-8")
    assert "ALTER SESSION SET CONTAINER = TOOLKITPDB;" in sql


def test_resolve_move_policy_skips_when_auto_disabled() -> None:
    """auto_pdb_guid_map false leaves the YAML map unchanged."""

    def boom(script: str, *, use_grid_env: bool = True) -> CommandResult:
        raise AssertionError(f"shell should not run: {script!r} grid={use_grid_env}")

    port = FakeAsmCmdPort(shell_handler=boom)
    scope = ScopeConfig.model_validate({"databases": ["homelab"]})
    move_policy = MovePolicy.model_validate(
        {
            "destination_disk_group": "+DATA",
            "auto_pdb_guid_map": False,
            "pdb_guid_map": {"AABBCCDDEEFF00112233445566778899": "X"},
        }
    )
    orch = PipelineOrchestrator(port=port, scope=scope, move_policy=move_policy)
    policy = orch.resolve_move_policy_for_emit(
        [
            AliasRecord(
                file_type="DATAFILE",
                source_path=(
                    "+DATA/HOMELAB/AABBCCDDEEFF00112233445566778899/DATAFILE/a.dbf"
                ),
                target_path="+DATA/OMF",
                pdb_guid="AABBCCDDEEFF00112233445566778899",
                disk_group="+DATA",
            )
        ]
    )
    assert policy.pdb_guid_map == {"AABBCCDDEEFF00112233445566778899": "X"}
