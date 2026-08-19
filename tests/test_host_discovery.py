"""Unit tests for HostDiscovery remote discovery helpers."""

from __future__ import annotations

import shlex
from unittest.mock import MagicMock

import pytest

from asm_cleanup.db.target import Target
from asm_cleanup.discovery.discovery_error import DiscoveryError
from asm_cleanup.discovery.host_discovery import HostDiscovery


def _target(**kwargs: object) -> Target:
    """Build a Target ORM instance without a DB session.

    Args:
        **kwargs (object): Field overrides.

    Returns:
        Target: In-memory target profile.
    """
    defaults: dict[str, object] = {
        "name": "t1",
        "host": "127.0.0.1",
        "user": "oracle",
        "destination_disk_group": "+DATA",
    }
    defaults.update(kwargs)
    return Target(**defaults)  # type: ignore[arg-type]


def _ok(stdout: str = "", stderr: str = "") -> MagicMock:
    """Build a successful Fabric result mock.

    Args:
        stdout (str): Command stdout.
        stderr (str): Command stderr.

    Returns:
        MagicMock: Fabric-like result with ok=True.
    """
    res = MagicMock()
    res.ok = True
    res.exited = 0
    res.stdout = stdout
    res.stderr = stderr
    return res


def _fail(stderr: str = "boom", exited: int = 1) -> MagicMock:
    """Build a failed Fabric result mock.

    Args:
        stderr (str): Command stderr.
        exited (int): Exit code.

    Returns:
        MagicMock: Fabric-like result with ok=False.
    """
    res = MagicMock()
    res.ok = False
    res.exited = exited
    res.stdout = ""
    res.stderr = stderr
    return res


def test_discover_grid_home_uses_target_overrides() -> None:
    """Skip remote script when grid_home and oracle_sid are already set."""
    host = HostDiscovery(_target(grid_home="/u01/grid", oracle_sid="+ASM1"))
    conn = MagicMock()
    gh, sid = host.discover_grid_home_and_sid(conn)
    assert gh == "/u01/grid"
    assert sid == "+ASM1"
    conn.run.assert_not_called()


def test_discover_grid_home_raises_when_script_fails() -> None:
    """Raise DiscoveryError when the remote discovery script exits non-zero."""
    host = HostDiscovery(_target())
    conn = MagicMock()
    conn.run.return_value = _fail("permission denied")
    with pytest.raises(DiscoveryError, match="Grid home discovery script failed"):
        host.discover_grid_home_and_sid(conn)


def test_discover_grid_home_raises_when_empty() -> None:
    """Raise DiscoveryError when GRID_HOME cannot be resolved."""
    host = HostDiscovery(_target())
    conn = MagicMock()
    conn.run.return_value = _ok("GRID_HOME=\nASM_SID=+ASM\n")
    with pytest.raises(DiscoveryError, match="Could not automatically discover"):
        host.discover_grid_home_and_sid(conn)


def test_discover_grid_home_partial_override() -> None:
    """Prefer target.grid_home when remote returns only ASM_SID."""
    host = HostDiscovery(_target(grid_home="/override/grid"))
    conn = MagicMock()
    conn.run.return_value = _ok("GRID_HOME=/remote/grid\nASM_SID=+ASM2\n")
    gh, sid = host.discover_grid_home_and_sid(conn)
    assert gh == "/override/grid"
    assert sid == "+ASM2"


def test_discover_disk_groups_fallback_on_failure() -> None:
    """Return +DATA when asmcmd ls + fails."""
    host = HostDiscovery(_target())
    conn = MagicMock()
    conn.run.return_value = _fail("asmcmd: not found")
    assert host.discover_disk_groups(conn, "/u01/grid", "+ASM") == ["+DATA"]


def test_discover_disk_groups_adds_plus_and_uppercases() -> None:
    """Normalize disk group tokens to +UPPERCASE form."""
    host = HostDiscovery(_target())
    conn = MagicMock()
    conn.run.return_value = _ok("data/\nfra\n")
    assert host.discover_disk_groups(conn, "/u01/grid", "+ASM") == ["+DATA", "+FRA"]


def test_discover_disk_groups_empty_stdout_defaults() -> None:
    """Default to +DATA when asmcmd succeeds but lists nothing usable."""
    host = HostDiscovery(_target())
    conn = MagicMock()
    conn.run.return_value = _ok("\n\n")
    assert host.discover_disk_groups(conn, "/u01/grid", "+ASM") == ["+DATA"]


def test_get_database_home_and_sid_from_srvctl() -> None:
    """Parse Oracle home and SID from srvctl config database output."""
    host = HostDiscovery(_target())
    conn = MagicMock()
    conn.run.return_value = _ok(
        "Oracle home: /u01/app/oracle/product/19.0.0/dbhome_1\n"
        "Database instance: HOMELAB\n"
    )
    home, sid = host.get_database_home_and_sid(conn, "homelab", "/u01/grid")
    assert home == "/u01/app/oracle/product/19.0.0/dbhome_1"
    assert sid == "HOMELAB"


def test_get_database_home_and_sid_oratab_fallback() -> None:
    """Fall back to remote /etc/oratab when srvctl yields no home."""
    host = HostDiscovery(_target())
    conn = MagicMock()
    conn.run.side_effect = [
        _ok(""),  # srvctl empty
        _ok("homelab:/u01/dbhome:Y\n"),  # oratab awk
    ]
    home, sid = host.get_database_home_and_sid(conn, "homelab", "/u01/grid")
    assert home == "/u01/dbhome"
    assert sid == "homelab"
    assert conn.run.call_count == 2


def test_get_database_home_and_sid_oratab_runs_when_local_oratab_missing() -> None:
    """Query remote oratab even when /etc/oratab is absent on the app host."""
    host = HostDiscovery(_target())
    conn = MagicMock()
    conn.run.side_effect = [
        _ok(""),
        _ok("mydb:/u01/dbhome:Y\n"),
    ]
    home, sid = host.get_database_home_and_sid(conn, "mydb", "/u01/grid")
    assert home == "/u01/dbhome"
    assert sid == "mydb"


def test_get_database_home_and_sid_no_oratab() -> None:
    """Return empty home and db_name SID when remote oratab has no match."""
    host = HostDiscovery(_target())
    conn = MagicMock()
    conn.run.side_effect = [_fail("srvctl missing"), _ok("")]
    home, sid = host.get_database_home_and_sid(conn, "mydb", "/u01/grid")
    assert home == ""
    assert sid == "mydb"


def test_discover_databases_quotes_grid_home() -> None:
    """Quote grid_home so shell metacharacters cannot break the remote script."""
    host = HostDiscovery(_target())
    conn = MagicMock()
    conn.run.return_value = _ok("HOMELAB\n")
    host.discover_databases(conn, "/u01/grid; reboot")
    script = conn.run.call_args.args[0]
    assert shlex.quote("/u01/grid; reboot") in script
    assert shlex.quote("/u01/grid; reboot/bin/srvctl") in script


def test_collect_database_details_parses_rows() -> None:
    """Parse PARAM, GUID, and FILE rows from sqlplus discovery output."""
    host = HostDiscovery(_target())
    conn = MagicMock()
    guid = "49C96937E332EB45E0631A04010ABA14"
    conn.run.return_value = _ok(
        "\n".join(
            [
                "PARAM|db_create_file_dest=+DATA",
                f"GUID|PDB1|{guid}",
                "+DATA/HOMELAB/DATAFILE/SYSTEM.257.1|1|CDB$ROOT|DATAFILE",
                "FILE|+DATA/homelab/custom.dbf|3|PDB1|DATAFILE",
                "FILE|+DATA/homelab/temp01.dbf|3|PDB1|TEMPFILE",
                "noise line",
            ]
        )
    )
    params, pdbs, files = host.collect_database_details(
        conn, "homelab", "/u01/dbhome", "homelab"
    )
    assert params == {"db_create_file_dest": "+DATA"}
    assert pdbs == [("PDB1", guid)]
    assert files == [
        ("+DATA/homelab/custom.dbf", "3", "PDB1", "DATAFILE"),
        ("+DATA/homelab/temp01.dbf", "3", "PDB1", "TEMPFILE"),
    ]


def test_collect_database_details_sqlplus_failure() -> None:
    """Return empty collections when sqlplus exits non-zero."""
    host = HostDiscovery(_target())
    conn = MagicMock()
    conn.run.return_value = _fail("ORA-01034")
    params, pdbs, files = host.collect_database_details(
        conn, "homelab", "/u01/dbhome", "homelab"
    )
    assert params == {}
    assert pdbs == []
    assert files == []
