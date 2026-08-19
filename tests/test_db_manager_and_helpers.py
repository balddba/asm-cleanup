"""Unit tests for DbManager sessions, migrations, and small module helpers."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.exc import SQLAlchemyError

from asm_cleanup.auth.settings import AuthSettings
from asm_cleanup.db.db_manager import DbManager
from asm_cleanup.services import (
    AliasEnricher,
    ConnectionFactory,
    ScanService,
    TargetMapper,
)
from asm_cleanup.transport.command_result import CommandResult
from asm_cleanup.transport.database_catalog import DatabaseCatalogCollector


def test_db_manager_session_rolls_back_on_error(tmp_path: Path) -> None:
    """Roll back and re-raise when the session body raises."""
    db = DbManager(f"sqlite:///{tmp_path / 'rollback.db'}")
    with pytest.raises(RuntimeError, match="boom"), db.session():
        raise RuntimeError("boom")
    db.engine.dispose()


def test_db_manager_run_migrations_skips_missing_ini(tmp_path: Path) -> None:
    """Skip migrations quietly when alembic.ini cannot be found."""
    db = DbManager(f"sqlite:///{tmp_path / 'mig.db'}")
    with patch("asm_cleanup.db.db_manager.Path") as mock_path_ctor:
        cfg_file = MagicMock()
        cfg_file.is_file.return_value = False
        cfg_file.absolute.return_value = tmp_path / "missing.ini"
        p_file = MagicMock()
        p_file.parent.parent.parent.__truediv__.return_value = cfg_file

        def path_factory(*args: object, **kwargs: object) -> object:
            """Return fake paths that report alembic.ini as missing.

            Args:
                *args (object): Path constructor args.
                **kwargs (object): Path constructor kwargs.

            Returns:
                object: Fake Path stand-in.
            """
            if args and str(args[0]).endswith("db_manager.py"):
                return p_file
            missing = MagicMock()
            missing.is_file.return_value = False
            missing.absolute.return_value = tmp_path / "alembic.ini"
            return missing

        mock_path_ctor.side_effect = path_factory
        db.run_migrations()
    db.engine.dispose()


def test_db_manager_run_migrations_upgrades() -> None:
    """Invoke alembic upgrade when alembic.ini exists."""
    db = DbManager("sqlite:///:memory:")
    with (
        patch("alembic.command.upgrade") as mock_upgrade,
        patch("alembic.config.Config") as mock_config_cls,
        patch("asm_cleanup.db.db_manager.Path") as mock_path_ctor,
    ):
        cfg_file = MagicMock()
        cfg_file.is_file.return_value = True
        p_file = MagicMock()
        p_file.parent.parent.parent.__truediv__.return_value = cfg_file

        def path_factory(*args: object, **kwargs: object) -> object:
            """Return fake package alembic.ini path.

            Args:
                *args (object): Path constructor args.
                **kwargs (object): Path constructor kwargs.

            Returns:
                object: Fake Path stand-in.
            """
            if args and str(args[0]).endswith("db_manager.py"):
                return p_file
            return MagicMock(is_file=MagicMock(return_value=False))

        mock_path_ctor.side_effect = path_factory
        mock_config_cls.return_value = MagicMock()
        db.run_migrations()
        mock_upgrade.assert_called_once_with(mock_config_cls.return_value, "head")
    db.engine.dispose()


def test_db_manager_run_migrations_reraises_alembic_errors() -> None:
    """Propagate Alembic upgrade failures after logging."""
    db = DbManager("sqlite:///:memory:")
    with (
        patch("alembic.command.upgrade", side_effect=SQLAlchemyError("fail")),
        patch("alembic.config.Config"),
        patch("asm_cleanup.db.db_manager.Path") as mock_path_ctor,
    ):
        cfg_file = MagicMock()
        cfg_file.is_file.return_value = True
        p_file = MagicMock()
        p_file.parent.parent.parent.__truediv__.return_value = cfg_file

        def path_factory(*args: object, **kwargs: object) -> object:
            """Return fake package alembic.ini path.

            Args:
                *args (object): Path constructor args.
                **kwargs (object): Path constructor kwargs.

            Returns:
                object: Fake Path stand-in.
            """
            if args and str(args[0]).endswith("db_manager.py"):
                return p_file
            return MagicMock(is_file=MagicMock(return_value=False))

        mock_path_ctor.side_effect = path_factory
        with pytest.raises(SQLAlchemyError):
            db.run_migrations()
    db.engine.dispose()


def test_auth_settings_from_env_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    """Validate missing vars and non-integer TTL for AuthSettings.from_env."""
    monkeypatch.delenv("ASM_CLEANUP_PASSWORD", raising=False)
    monkeypatch.delenv("ASM_CLEANUP_JWT_SECRET", raising=False)
    with pytest.raises(ValueError, match="ASM_CLEANUP_PASSWORD"):
        AuthSettings.from_env()

    monkeypatch.setenv("ASM_CLEANUP_PASSWORD", "pw")
    monkeypatch.setenv("ASM_CLEANUP_JWT_SECRET", "secret")
    monkeypatch.setenv("ASM_CLEANUP_JWT_TTL_SECONDS", "not-an-int")
    with pytest.raises(ValueError, match="must be an integer"):
        AuthSettings.from_env()

    monkeypatch.setenv("ASM_CLEANUP_PASSWORD", "test-password")
    monkeypatch.setenv("ASM_CLEANUP_JWT_SECRET", "test-jwt-secret-for-unit-tests-32b+")
    monkeypatch.setenv("ASM_CLEANUP_JWT_TTL_SECONDS", "3600")
    settings = AuthSettings.from_env()
    assert settings.jwt_ttl_seconds == 3600


def test_auth_settings_rejects_placeholder_and_short_secrets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Refuse .env.example placeholders and short JWT secrets."""
    monkeypatch.setenv("ASM_CLEANUP_PASSWORD", "change-me")
    monkeypatch.setenv("ASM_CLEANUP_JWT_SECRET", "test-jwt-secret-for-unit-tests-32b+")
    monkeypatch.setenv("ASM_CLEANUP_JWT_TTL_SECONDS", "86400")
    with pytest.raises(ValueError, match="placeholder"):
        AuthSettings.from_env()

    monkeypatch.setenv("ASM_CLEANUP_PASSWORD", "test-password")
    monkeypatch.setenv(
        "ASM_CLEANUP_JWT_SECRET",
        "change-me-to-a-long-random-string-at-least-32-bytes",
    )
    with pytest.raises(ValueError, match="placeholder"):
        AuthSettings.from_env()

    monkeypatch.setenv("ASM_CLEANUP_JWT_SECRET", "short-secret-not-32-bytes")
    with pytest.raises(ValueError, match="at least 32 bytes"):
        AuthSettings.from_env()


def test_services_lazy_exports() -> None:
    """Resolve lazy package exports and reject unknown names."""
    assert AliasEnricher is not None
    assert ScanService is not None
    assert TargetMapper is not None
    assert ConnectionFactory is not None
    from asm_cleanup import services

    with pytest.raises(AttributeError):
        _ = services.DoesNotExist


def test_database_catalog_edge_cases() -> None:
    """Cover empty-home, partition fallback, and prefixed GUID edge cases."""
    with (
        patch.object(
            DatabaseCatalogCollector,
            "extract_srvctl_home_and_sid",
            return_value=(None, "X"),
        ),
        patch("asm_cleanup.transport.database_catalog._ORACLE_HOME_RE") as mock_home_re,
    ):
        mock_home_re.search.return_value = MagicMock()
        with pytest.raises(ValueError, match="empty Oracle home"):
            DatabaseCatalogCollector.parse_srvctl_database_config(
                "Oracle home: /x\n",
                database="x",
            )

    assert DatabaseCatalogCollector.parse_name_guid_row("---") is None
    assert DatabaseCatalogCollector.parse_name_guid_row("noguids") is None
    assert DatabaseCatalogCollector.parse_name_guid_row("|AABB") is None
    assert DatabaseCatalogCollector.parse_name_guid_row("") is None
    guid = "A" * 32
    assert DatabaseCatalogCollector.parse_name_guid_row(f"PDB1|{guid}") == (
        "PDB1",
        guid,
    )
    assert DatabaseCatalogCollector.parse_name_guid_row(f"  PDB2 | {guid}  ") == (
        "PDB2",
        guid,
    )
    assert DatabaseCatalogCollector.parse_name_guid_row(f"|{guid}") is None
    assert DatabaseCatalogCollector.parse_name_guid_row("PDB3|not-a-guid") is None

    assert DatabaseCatalogCollector.parse_prefixed_guid_row("PARAM|x=y") is None
    assert DatabaseCatalogCollector.parse_prefixed_guid_row("GUID|only") is None
    assert DatabaseCatalogCollector.parse_prefixed_guid_row("GUID||hex") is None
    assert DatabaseCatalogCollector.parse_prefixed_guid_row("GUID|name|") is None
    assert DatabaseCatalogCollector.parse_prefixed_guid_row(f"GUID|PDB1|{guid}") == (
        "PDB1",
        guid,
    )


def test_command_result_lines_and_non_string_validator() -> None:
    """Exercise lines property and non-string ANSI validator passthrough."""
    empty = CommandResult(argv=["x"], stdout="")
    assert empty.lines == []
    result = CommandResult(argv=["x"], stdout="a\nb\n")
    assert result.lines == ["a", "b"]
    assert CommandResult._strip_ansi_fields(123) == 123
    assert CommandResult._strip_ansi_fields("plain") == "plain"
