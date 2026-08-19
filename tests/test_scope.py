"""Tests for WalkScopeResolver path expansion and discovery."""

import pytest

from asm_cleanup.config.scope_config import ScopeConfig
from asm_cleanup.pipeline.walk_scope_resolver import WalkScopeResolver
from asm_cleanup.transport.command_result import CommandResult
from asm_cleanup.transport.fake_asm_cmd_port import FakeAsmCmdPort


def _scope(**kwargs: object) -> ScopeConfig:
    """Build a ScopeConfig with optional field overrides.

    Args:
        **kwargs (object): ScopeConfig field overrides.

    Returns:
        ScopeConfig: Validated scope.
    """
    return ScopeConfig.model_validate(kwargs)


def _ok(argv: list[str], stdout: str = "") -> CommandResult:
    """Build a successful CommandResult.

    Args:
        argv (list[str]): Command argv.
        stdout (str): Stdout text.

    Returns:
        CommandResult: Successful result.
    """
    return CommandResult(argv=argv, stdout=stdout, stderr="", exit_code=0)


def test_discover_disk_groups_via_port() -> None:
    """Discover disk groups when scope.disk_groups is empty."""
    port = FakeAsmCmdPort(
        {
            ("asmcmd", "ls", "+"): _ok(["asmcmd", "ls", "+"], "+DATA\n+FRA\n"),
        }
    )
    scope = _scope(databases=["MYDB"])
    resolver = WalkScopeResolver(scope, port)
    assert resolver.disk_groups() == ["+DATA", "+FRA"]
    assert resolver.resolve_paths() == ["+DATA/MYDB", "+FRA/MYDB"]


def test_discover_disk_groups_requires_port() -> None:
    """Raise when discovery is needed but no port is available."""
    scope = _scope(databases=["MYDB"])
    resolver = WalkScopeResolver(scope)
    with pytest.raises(RuntimeError, match="disk group discovery requires"):
        resolver.disk_groups()


def test_default_asm_path_when_asm_path_omitted() -> None:
    """Use default_asm_path instead of disk_groups × databases expansion."""
    scope = _scope(
        disk_groups=["+DATA", "+FRA"],
        databases=["MYDB"],
        default_asm_path="+DATA/MYDB",
    )
    resolver = WalkScopeResolver(scope)
    assert resolver.resolve_paths() == ["+DATA/MYDB"]


def test_explicit_asm_path_overrides_scope() -> None:
    """Honor an explicit ASM path argument."""
    scope = _scope(
        disk_groups=["+DATA"],
        databases=["MYDB"],
    )
    resolver = WalkScopeResolver(scope)
    assert resolver.resolve_paths("+data/mydb") == ["+DATA/mydb"]


def test_database_filter_restricts_scope() -> None:
    """Apply CLI database filter to scope.databases."""
    scope = _scope(
        disk_groups=["+DATA"],
        databases=["MYDB", "OTHERDB"],
    )
    resolver = WalkScopeResolver(scope, database_filter=["MYDB"])
    assert resolver.databases() == ["MYDB"]
    assert resolver.resolve_paths() == ["+DATA/MYDB"]


def test_database_filter_rejects_unknown_names() -> None:
    """Reject database names not listed in scope."""
    scope = _scope(databases=["MYDB"])
    resolver = WalkScopeResolver(scope, database_filter=["UNKNOWN"])
    with pytest.raises(ValueError, match="Databases not defined"):
        resolver.databases()


def test_resolve_paths_raises_when_nothing_to_walk() -> None:
    """Fail when databases are empty after filtering."""
    scope = _scope(disk_groups=["+DATA"], databases=[])
    resolver = WalkScopeResolver(scope)
    with pytest.raises(ValueError, match="No ASM paths to walk"):
        resolver.resolve_paths()
