"""Tests for simplified CLI argument parsing and exit codes."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from asm_cleanup.cli import _require_web_extra, build_parser, cmd_db, cmd_web, main


def test_build_parser_requires_subcommand() -> None:
    """Reject invocations without a subcommand."""
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args([])


def test_build_parser_parses_web_args() -> None:
    """Parse web subcommand flags."""
    parser = build_parser()
    args = parser.parse_args(["web", "--host", "0.0.0.0", "--port", "9000", "--reload"])
    assert args.command == "web"
    assert args.host == "0.0.0.0"
    assert args.port == 9000
    assert args.reload is True


def test_build_parser_parses_run_args() -> None:
    """Parse run subcommand target parameter."""
    parser = build_parser()
    args = parser.parse_args(["run", "production-db"])
    assert args.command == "run"
    assert args.target_name == "production-db"


def test_build_parser_parses_db_args() -> None:
    """Parse db subcommand upgrade action."""
    parser = build_parser()
    args = parser.parse_args(["db", "upgrade"])
    assert args.command == "db"
    assert args.db_action == "upgrade"


def test_build_parser_parses_db_build_demo_args() -> None:
    """Parse db build-demo with optional output path."""
    parser = build_parser()
    args = parser.parse_args(
        ["db", "build-demo", "--output", "/tmp/asm_cleanup_demo.db"]
    )
    assert args.command == "db"
    assert args.db_action == "build-demo"
    assert args.output == "/tmp/asm_cleanup_demo.db"


def test_cmd_db_build_demo_writes_file(tmp_path: Path) -> None:
    """db build-demo writes a demo SQLite file and returns 0."""
    dest = tmp_path / "asm_cleanup_demo.db"
    args = MagicMock(db_action="build-demo", output=str(dest))
    assert cmd_db(args) == 0
    assert dest.is_file()


def test_cmd_db_build_demo_refuses_production_path(tmp_path: Path) -> None:
    """db build-demo exits 1 when pointed at asm_cleanup.db."""
    args = MagicMock(db_action="build-demo", output=str(tmp_path / "asm_cleanup.db"))
    assert cmd_db(args) == 1


@patch("asm_cleanup.db.DbManager")
def test_run_command_exits_1_when_target_missing(
    mock_db_manager_cls: MagicMock,
) -> None:
    """Verify run subcommand exits with status code 1 if target not found in DB."""
    mock_db = MagicMock()
    mock_db_manager_cls.return_value = mock_db

    mock_session = MagicMock()
    mock_db.session.return_value.__enter__.return_value = mock_session
    mock_session.query.return_value.filter.return_value.first.return_value = None

    with pytest.raises(SystemExit) as exc:
        main(["run", "missing-target"])

    assert exc.value.code == 1


@patch("asm_cleanup.discovery.TargetDiscoveryRunner")
@patch("asm_cleanup.db.Scan")
@patch("asm_cleanup.db.DbManager")
def test_run_command_success(
    mock_db_manager_cls: MagicMock,
    mock_scan_cls: MagicMock,
    mock_runner_cls: MagicMock,
) -> None:
    """Verify run subcommand succeeds and exits 0 on completed scan."""
    mock_db = MagicMock()
    mock_db_manager_cls.return_value = mock_db

    mock_session = MagicMock()
    mock_db.session.return_value.__enter__.return_value = mock_session

    mock_target = MagicMock()
    mock_target.name = "prod-db"
    mock_session.query.return_value.filter.return_value.first.return_value = mock_target

    mock_scan = MagicMock()
    mock_scan.status = "completed"
    mock_scan.grid_home = "/u01/app/grid"
    mock_scan.generated_sql = "ALTER DATABASE MOVE DATAFILE..."
    mock_scan.databases = None
    mock_scan_cls.return_value = mock_scan

    mock_runner = MagicMock()
    mock_runner_cls.return_value = mock_runner
    mock_session.refresh = lambda _obj: None

    with pytest.raises(SystemExit) as exc:
        main(["run", "prod-db"])

    assert exc.value.code == 0
    mock_runner.run.assert_called_once()


@patch("asm_cleanup.discovery.TargetDiscoveryRunner")
@patch("asm_cleanup.db.Scan")
@patch("asm_cleanup.db.DbManager")
def test_run_command_prints_pdb_counts(
    mock_db_manager_cls: MagicMock,
    mock_scan_cls: MagicMock,
    mock_runner_cls: MagicMock,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Print per-CDB PDB counts when scan.databases JSON is present."""
    mock_db = MagicMock()
    mock_db_manager_cls.return_value = mock_db
    mock_session = MagicMock()
    mock_db.session.return_value.__enter__.return_value = mock_session
    mock_target = MagicMock()
    mock_target.name = "prod-db"
    mock_session.query.return_value.filter.return_value.first.return_value = mock_target

    mock_scan = MagicMock()
    mock_scan.status = "completed"
    mock_scan.grid_home = "/u01/app/grid"
    mock_scan.generated_sql = "-- none"
    mock_scan.databases = json.dumps({"homelab": {"pdb_count": 2}})
    mock_scan_cls.return_value = mock_scan
    mock_runner_cls.return_value = MagicMock()
    mock_session.refresh = lambda _obj: None

    with pytest.raises(SystemExit) as exc:
        main(["run", "prod-db"])
    assert exc.value.code == 0
    assert "CDB HOMELAB PDBs  : 2" in capsys.readouterr().out


@patch("asm_cleanup.discovery.TargetDiscoveryRunner")
@patch("asm_cleanup.db.Scan")
@patch("asm_cleanup.db.DbManager")
def test_run_command_exits_1_on_failed_scan(
    mock_db_manager_cls: MagicMock,
    mock_scan_cls: MagicMock,
    mock_runner_cls: MagicMock,
) -> None:
    """Exit 1 when the discovery runner marks the scan failed."""
    mock_db = MagicMock()
    mock_db_manager_cls.return_value = mock_db
    mock_session = MagicMock()
    mock_db.session.return_value.__enter__.return_value = mock_session
    mock_session.query.return_value.filter.return_value.first.return_value = MagicMock(
        name="t"
    )
    mock_scan = MagicMock()
    mock_scan.status = "failed"
    mock_scan.error_message = "ssh timeout"
    mock_scan_cls.return_value = mock_scan
    mock_runner_cls.return_value = MagicMock()
    mock_session.refresh = lambda _obj: None

    with pytest.raises(SystemExit) as exc:
        main(["run", "prod-db"])
    assert exc.value.code == 1


def test_cmd_web_returns_1_when_auth_settings_invalid() -> None:
    """Refuse to start the web server when auth env is incomplete."""
    args = MagicMock(host="127.0.0.1", port=8000, reload=False, debug=False)
    with patch(
        "asm_cleanup.auth.AuthSettings.from_env", side_effect=ValueError("missing")
    ):
        assert cmd_web(args) == 1


def test_cmd_web_starts_uvicorn() -> None:
    """Configure logging and hand off to uvicorn.run on success."""
    args = MagicMock(host="0.0.0.0", port=9000, reload=True, debug=True)
    with (
        patch("asm_cleanup.auth.AuthSettings.from_env"),
        patch("asm_cleanup.auth.ssh_key_store.ssh_key_store_from_env"),
        patch("asm_cleanup.cli.configure_logging") as mock_log,
        patch("uvicorn.run") as mock_run,
    ):
        assert cmd_web(args) == 0
    mock_log.assert_called_once_with(debug=True)
    mock_run.assert_called_once()


def test_cmd_db_upgrade_runs_migrations() -> None:
    """db upgrade delegates to DbManager.run_migrations."""
    args = MagicMock(db_action="upgrade")
    with patch("asm_cleanup.db.DbManager") as mock_cls:
        mock_cls.return_value = MagicMock()
        assert cmd_db(args) == 0
        mock_cls.return_value.run_migrations.assert_called_once()


def test_cmd_db_unknown_action_returns_1() -> None:
    """Reject unsupported db actions with exit code 1."""
    args = MagicMock(db_action="downgrade")
    with patch("asm_cleanup.db.DbManager"):
        assert cmd_db(args) == 1


def test_main_fatal_error_exits_1() -> None:
    """Catch unexpected exceptions from subcommands and exit 1."""
    with patch("asm_cleanup.cli.build_parser") as mock_parser:
        args = MagicMock()
        args.func.side_effect = RuntimeError("boom")
        mock_parser.return_value.parse_args.return_value = args
        with pytest.raises(SystemExit) as exc:
            main([])
        assert exc.value.code == 1


@patch("asm_cleanup.discovery.TargetDiscoveryRunner")
@patch("asm_cleanup.db.Scan")
@patch("asm_cleanup.db.DbManager")
def test_run_command_tolerates_invalid_databases_json(
    mock_db_manager_cls: MagicMock,
    mock_scan_cls: MagicMock,
    mock_runner_cls: MagicMock,
) -> None:
    """Ignore invalid scan.databases JSON when printing the success report."""
    mock_db = MagicMock()
    mock_db_manager_cls.return_value = mock_db
    mock_session = MagicMock()
    mock_db.session.return_value.__enter__.return_value = mock_session
    mock_target = MagicMock()
    mock_target.name = "prod-db"
    mock_session.query.return_value.filter.return_value.first.return_value = mock_target
    mock_scan = MagicMock()
    mock_scan.status = "completed"
    mock_scan.grid_home = "/u01/app/grid"
    mock_scan.generated_sql = "-- none"
    mock_scan.databases = "{not-json"
    mock_scan_cls.return_value = mock_scan
    mock_runner_cls.return_value = MagicMock()
    mock_session.refresh = lambda _obj: None

    with pytest.raises(SystemExit) as exc:
        main(["run", "prod-db"])
    assert exc.value.code == 0


def test_discovery_package_rejects_unknown_export() -> None:
    """Raise AttributeError for unknown discovery package attributes."""
    from asm_cleanup import discovery

    with pytest.raises(AttributeError):
        _ = discovery.DoesNotExist


def test_require_web_extra_exits_when_import_fails() -> None:
    """Exit 1 with an install hint when web extras are missing."""
    import builtins

    real_import = builtins.__import__

    def fake_import(name: str, *args: object, **kwargs: object) -> object:
        """Raise ImportError for fastapi to simulate missing web extra.

        Args:
            name (str): Module name.
            *args (object): Positional import args.
            **kwargs (object): Keyword import args.

        Returns:
            object: Imported module for non-fastapi names.

        Raises:
            ImportError: When name is fastapi.
        """
        if name == "fastapi":
            raise ImportError("no fastapi")
        return real_import(name, *args, **kwargs)  # type: ignore[arg-type]

    with patch("builtins.__import__", side_effect=fake_import):
        with pytest.raises(SystemExit) as exc:
            _require_web_extra("web")
        assert exc.value.code == 1
