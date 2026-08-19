"""Tests for the fictional documentation demo database builder."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from asm_cleanup.db import (
    DEFAULT_DEMO_DB_PATH,
    DbManager,
    ProductionDatabaseError,
    Scan,
    ScanAlias,
    Target,
    assert_not_production_database,
    build_demo_database,
)


def test_assert_not_production_database_rejects_prod_basename() -> None:
    """Refuse paths whose basename is the production SQLite filename."""
    with pytest.raises(ProductionDatabaseError, match="production database"):
        assert_not_production_database(Path("/tmp/asm_cleanup.db"))


def test_assert_not_production_database_allows_demo_basename() -> None:
    """Allow the committed demo database filename."""
    assert_not_production_database(Path("docs/demo/asm_cleanup_demo.db"))


def test_build_demo_database_refuses_production_path(tmp_path: Path) -> None:
    """build_demo_database must not write to asm_cleanup.db."""
    with pytest.raises(ProductionDatabaseError):
        build_demo_database(tmp_path / "asm_cleanup.db")


def test_build_demo_database_populates_fictional_rows(tmp_path: Path) -> None:
    """Builder writes baldba.com targets, completed scan, aliases, and SQL."""
    dest = tmp_path / "asm_cleanup_demo.db"
    result = build_demo_database(dest)
    assert result == dest.resolve()
    assert dest.is_file()

    db = DbManager(f"sqlite:///{dest}")
    with db.session() as session:
        targets = session.query(Target).order_by(Target.name).all()
        assert [t.name for t in targets] == ["lab-asm", "prod-grid-01"]
        assert targets[0].host == "lab-asm.baldba.com"
        assert targets[1].host == "grid.prod.baldba.com"

        completed = list(session.query(Scan).filter(Scan.status == "completed").all())
        assert len(completed) == 2
        assert all(s.error_message is None for s in completed)
        assert session.query(Scan).filter(Scan.status == "failed").count() == 0

        prod_scan = next(
            s for s in completed if s.generated_sql and "SALESCDB" in s.generated_sql
        )
        by_db = json.loads(prod_scan.generated_sql or "{}")
        assert set(by_db) == {"salescdb", "hrcdb"}
        assert "ALTER DATABASE MOVE DATAFILE" in by_db["salescdb"]
        assert "ALTER DATABASE MOVE DATAFILE" in by_db["hrcdb"]

        disk_groups = json.loads(prod_scan.disk_groups or "[]")
        assert disk_groups == ["+DATA", "+FRA"]
        databases = json.loads(prod_scan.databases or "{}")
        assert set(databases) == {"salescdb", "hrcdb"}
        assert databases["salescdb"]["pdb_count"] == 1
        assert databases["hrcdb"]["pdb_count"] == 1

        aliases = (
            session.query(ScanAlias).filter(ScanAlias.scan_id == prod_scan.id).all()
        )
        assert len(aliases) >= 8
        assert {a.database_name for a in aliases} == {"salescdb", "hrcdb"}
    db.engine.dispose()


def test_committed_demo_database_smoke() -> None:
    """Committed docs/demo DB contains expected screenshot seed rows."""
    path = Path(DEFAULT_DEMO_DB_PATH)
    if not path.is_file():
        pytest.skip(f"committed demo DB missing at {path}")

    db = DbManager(f"sqlite:///{path.resolve()}")
    with db.session() as session:
        assert session.query(Target).count() == 2
        assert session.query(Scan).filter(Scan.status == "completed").count() == 2
        assert session.query(Scan).filter(Scan.status == "failed").count() == 0
        assert session.query(ScanAlias).count() >= 8
    db.engine.dispose()
