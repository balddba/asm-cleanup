"""Tests for SQLite database models, automated target discovery, and FastAPI endpoints."""

import json
import os
from collections.abc import Generator
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

# Auth env must be set before importing/creating the app.
os.environ.setdefault("ASM_CLEANUP_PASSWORD", "test-password")
os.environ.setdefault(
    "ASM_CLEANUP_JWT_SECRET",
    "test-jwt-secret-for-unit-tests-32b+",
)
os.environ.setdefault("ASM_CLEANUP_JWT_TTL_SECONDS", "86400")

from asm_cleanup.db import Base, DbManager, Scan, ScanAlias, Target
from asm_cleanup.discovery import HostDiscovery, TargetDiscoveryRunner
from asm_cleanup.domain.alias_record import AliasRecord
from asm_cleanup.services.scan_service import WalkAliasOutcome
from asm_cleanup.web import app, get_db
from asm_cleanup.web.deps import get_ssh_key_store

# Use a temporary file-based SQLite database for testing database functions
TEST_DB_FILE = "test_asm_cleanup.db"
TEST_DATABASE_URL = f"sqlite:///{TEST_DB_FILE}"
TEST_PASSWORD = os.environ["ASM_CLEANUP_PASSWORD"]


def _auth_headers(client: TestClient) -> dict[str, str]:
    """Log in and return Authorization headers for subsequent API calls.

    Args:
        client (TestClient): FastAPI test client.

    Returns:
        dict[str, str]: Bearer Authorization header map.
    """
    res = client.post("/api/auth/login", json={"password": TEST_PASSWORD})
    assert res.status_code == 200
    token = res.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(name="test_db")
def fixture_test_db() -> Generator[DbManager]:
    """Fixture providing a clean database manager using a temporary file."""
    # Ensure any legacy test file is removed
    if os.path.exists(TEST_DB_FILE):
        try:
            os.unlink(TEST_DB_FILE)
        except OSError:
            pass

    db_mgr = DbManager(TEST_DATABASE_URL)
    Base.metadata.create_all(bind=db_mgr.engine)
    yield db_mgr
    Base.metadata.drop_all(bind=db_mgr.engine)
    db_mgr.engine.dispose()

    if os.path.exists(TEST_DB_FILE):
        try:
            os.unlink(TEST_DB_FILE)
        except OSError:
            pass


def test_database_crud_operations(test_db: DbManager) -> None:
    """Verify targets, scans, and aliases CRUD operations in SQLite."""
    with test_db.session() as session:
        # Create
        target = Target(
            name="test-target",
            host="127.0.0.1",
            user="oracle",
            destination_disk_group="+DATA",
        )
        session.add(target)
        session.commit()

        # Read
        t_db = session.query(Target).filter(Target.name == "test-target").first()
        assert t_db is not None
        assert t_db.host == "127.0.0.1"
        assert t_db.user == "oracle"

        # Add a Scan
        scan = Scan(target_id=t_db.id, status="pending")
        session.add(scan)
        session.commit()

        # Add an Alias
        alias = ScanAlias(
            scan_id=scan.id,
            database_name="mydb",
            container_name="PDB1",
            file_type="DATAFILE",
            source_path="+DATA/mydb/datafile/pdb1.dbf",
            target_path="+DATA/mydb/DATAFILE/PDB1.256.1",
        )
        session.add(alias)
        session.commit()

        # Query relationships
        assert len(t_db.scans) == 1
        assert t_db.scans[0].status == "pending"
        assert len(t_db.scans[0].aliases) == 1
        assert t_db.scans[0].aliases[0].container_name == "PDB1"


def test_discovery_runner_methods(test_db: DbManager) -> None:
    """Verify HostDiscovery methods execute successfully with mock remote command outputs."""
    mock_conn = MagicMock()

    with test_db.session() as session:
        target = Target(
            name="test-target",
            host="127.0.0.1",
            user="oracle",
            destination_disk_group="+DATA",
        )
        session.add(target)
        session.commit()

        host = HostDiscovery(target)

        # 1. Test Grid Home discovery
        mock_res_grid = MagicMock()
        mock_res_grid.ok = True
        mock_res_grid.stdout = "GRID_HOME=/u01/app/grid\nASM_SID=+ASM"
        mock_conn.run.return_value = mock_res_grid

        gh, sid = host.discover_grid_home_and_sid(mock_conn)
        assert gh == "/u01/app/grid"
        assert sid == "+ASM"

        # 2. Test disk groups discovery
        mock_res_dg = MagicMock()
        mock_res_dg.ok = True
        mock_res_dg.stdout = "DATA/\nFRA/\n"
        mock_conn.run.return_value = mock_res_dg

        dgs = host.discover_disk_groups(mock_conn, gh, sid)
        assert dgs == ["+DATA", "+FRA"]

        # 3. Test database discovery - srvctl success case
        mock_res_db_srvctl = MagicMock()
        mock_res_db_srvctl.ok = True
        mock_res_db_srvctl.stdout = "homelab\nkeytest\n"
        mock_conn.run.return_value = mock_res_db_srvctl

        dbs = host.discover_databases(mock_conn, gh)
        assert list(dbs.keys()) == ["homelab", "keytest"]

        # 4. Test database discovery - srvctl fails (ok is False) but does not crash
        mock_res_db_fail = MagicMock()
        mock_res_db_fail.ok = False
        mock_res_db_fail.exited = 1
        mock_res_db_fail.stderr = "srvctl: command not found"
        mock_conn.run.return_value = mock_res_db_fail

        dbs = host.discover_databases(mock_conn, gh)
        assert dbs == {}


def test_web_endpoints(test_db: DbManager) -> None:
    """Test target creation, fetching, and scan queuing via FastAPI routes."""

    # Override app dependency
    def override_get_db() -> Generator[Session]:
        with test_db.session() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)
    headers = _auth_headers(client)

    # 1. Create a target with pasted SSH key material
    payload = {
        "name": "test-web-target",
        "host": "10.0.0.1",
        "user": "grid",
        "destination_disk_group": "+DATA",
        "ssh_key_content": "-----BEGIN OPENSSH PRIVATE KEY-----\nfake\n-----END OPENSSH PRIVATE KEY-----",
    }
    res_post = client.post("/api/targets", json=payload, headers=headers)
    assert res_post.status_code == 201
    assert "id" in res_post.json()
    target_id = res_post.json()["id"]

    # 2. Get targets list — key content must not be returned
    res_get = client.get("/api/targets", headers=headers)
    assert res_get.status_code == 200
    targets = res_get.json()
    assert len(targets) > 0
    assert targets[0]["name"] == "test-web-target"
    assert "ssh_key_content" not in targets[0]
    assert targets[0]["has_ssh_key"] is True
    assert targets[0]["move_online"] is False

    # 3. Update without key content must preserve stored material
    res_put = client.put(
        f"/api/targets/{target_id}",
        json={
            "name": "test-web-target",
            "host": "10.0.0.1",
            "user": "grid",
            "destination_disk_group": "+DATA",
            "move_online": True,
            "ssh_key_content": None,
        },
        headers=headers,
    )
    assert res_put.status_code == 200
    with test_db.session() as session:
        stored = session.query(Target).filter(Target.id == target_id).one()
        assert stored.ssh_key_content is None
        assert stored.move_online is True
    assert get_ssh_key_store().has(target_id) is True

    res_get_after = client.get("/api/targets", headers=headers)
    assert res_get_after.json()[0]["move_online"] is True

    # 4. Trigger discovery scan (mocking background executor)
    with patch("asm_cleanup.web.routers.scans.run_discovery_async") as mock_async_run:
        res_scan = client.post(
            f"/api/targets/{target_id}/scan",
            headers=headers,
        )
        assert res_scan.status_code == 200
        payload = res_scan.json()
        assert payload["status"] == "pending"
        assert payload["progress_message"] == "Queued - waiting to start..."
        mock_async_run.assert_called_once()

    # 5. Second concurrent scan for the same target is rejected
    res_conflict = client.post(
        f"/api/targets/{target_id}/scan",
        headers=headers,
    )
    assert res_conflict.status_code == 409

    # Reset dependency override
    app.dependency_overrides.clear()


def test_discovery_runner_run_finds_non_omf_files(test_db: DbManager) -> None:
    """Verify target discovery pipeline successfully discovers and targets non-OMF database files."""
    mock_conn = MagicMock()

    with test_db.session() as session:
        target = Target(
            name="test-target-non-omf",
            host="127.0.0.1",
            user="oracle",
            destination_disk_group="+DATA",
        )
        session.add(target)
        session.commit()

        scan = Scan(target_id=target.id, status="pending")
        session.add(scan)
        session.commit()

        runner = TargetDiscoveryRunner(session, target, scan)
        service = runner._service

        @contextmanager
        def fake_ssh() -> Generator[MagicMock]:
            yield mock_conn

        service._ssh_connection = fake_ssh  # type: ignore[method-assign]

        # Mock host discovery helpers to avoid SSH execution and return structured data
        service._host.discover_grid_home_and_sid = MagicMock(
            return_value=("/u01/app/grid", "+ASM")
        )
        service._host.discover_disk_groups = MagicMock(return_value=["+DATA", "+RECO"])
        service._host.discover_databases = MagicMock(return_value={"homelab": {}})
        service._host.get_database_home_and_sid = MagicMock(
            return_value=(
                "/u01/app/oracle/product/19.0.0/dbhome_1",
                "homelab",
            )
        )

        db_params = {"db_create_file_dest": "+DATA"}
        db_pdbs = [("PDB1", "49C96937E332EB45E0631A04010ABA14")]
        db_files = [
            (
                "+DATA/HOMELAB/DATAFILE/SYSTEM.257.108482931",
                "1",
                "CDB$ROOT",
                "DATAFILE",
            ),
            ("+DATA/homelab/my_custom_datafile.dbf", "3", "PDB1", "DATAFILE"),
        ]
        service._host.collect_database_details = MagicMock(
            return_value=(db_params, db_pdbs, db_files)
        )
        service._walk_asm_aliases = MagicMock(
            return_value=WalkAliasOutcome(
                records=[], paths_attempted=1, failed_paths=[]
            )
        )

        runner.run()

        # Reload scan
        session.refresh(scan)
        assert scan.status == "completed"

        # Verify database metadata contains the list of PDBs
        db_meta = json.loads(scan.databases)
        assert "homelab" in db_meta
        assert db_meta["homelab"]["pdb_count"] == 1
        assert db_meta["homelab"]["pdbs"] == [
            {"name": "PDB1", "guid": "49C96937E332EB45E0631A04010ABA14"}
        ]

        # Verify ScanAlias record was created for the non-OMF datafile
        aliases = session.query(ScanAlias).filter(ScanAlias.scan_id == scan.id).all()
        assert len(aliases) == 1
        assert aliases[0].source_path == "+DATA/homelab/my_custom_datafile.dbf"
        assert aliases[0].target_path == "+DATA"
        assert aliases[0].container_name == "PDB1"
        assert aliases[0].database_name == "homelab"
        assert aliases[0].file_type == "DATAFILE"

        # Verify generated SQL targets the non-OMF datafile
        assert (
            "ALTER DATABASE MOVE DATAFILE '+DATA/homelab/my_custom_datafile.dbf' TO '+DATA';"
            in scan.generated_sql
        )


def test_get_scan_details_endpoint(test_db: DbManager) -> None:
    """Verify that scan details API returns databases with their PDB lists."""

    # Override app dependency
    def override_get_db() -> Generator[Session]:
        with test_db.session() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)
    headers = _auth_headers(client)

    with test_db.session() as session:
        # Create target
        target = Target(name="test-target", host="127.0.0.1", user="oracle")
        session.add(target)
        session.commit()

        # Create scan with database metadata including PDBs
        db_meta = {
            "mydb": {
                "oracle_home": "/u01/app/oracle/product/19.0.0/dbhome_1",
                "oracle_sid": "mydb",
                "parameters": {"db_create_file_dest": "+DATA"},
                "pdb_count": 2,
                "pdbs": [
                    {"name": "PDB1", "guid": "GUID1"},
                    {"name": "PDB2", "guid": "GUID2"},
                ],
            }
        }
        scan = Scan(
            target_id=target.id,
            status="completed",
            databases=json.dumps(db_meta),
        )
        session.add(scan)
        session.commit()
        scan_id = scan.id

    # Retrieve scan details
    res = client.get(f"/api/scans/{scan_id}", headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert data["id"] == scan_id
    assert "progress_message" in data
    assert data["progress_message"] is None
    assert "mydb" in data["databases"]
    assert data["databases"]["mydb"]["pdbs"] == [
        {"name": "PDB1", "guid": "GUID1"},
        {"name": "PDB2", "guid": "GUID2"},
    ]

    app.dependency_overrides.clear()


def test_web_target_and_scan_error_paths(test_db: DbManager) -> None:
    """Cover duplicate create, missing update/delete/scan, list scans, and SPA routes."""

    def override_get_db() -> Generator[Session]:
        with test_db.session() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)
    headers = _auth_headers(client)

    payload = {
        "name": "dup-target",
        "host": "10.0.0.2",
        "user": "grid",
        "destination_disk_group": "+DATA",
    }
    assert client.post("/api/targets", json=payload, headers=headers).status_code == 201
    assert client.post("/api/targets", json=payload, headers=headers).status_code == 400

    assert (
        client.put("/api/targets/9999", json=payload, headers=headers).status_code
        == 404
    )
    assert client.delete("/api/targets/9999", headers=headers).status_code == 404
    assert client.post("/api/targets/9999/scan", headers=headers).status_code == 404
    assert client.get("/api/scans/9999", headers=headers).status_code == 404

    # Name conflict on update
    second = {
        "name": "other-target",
        "host": "10.0.0.3",
        "user": "grid",
        "destination_disk_group": "+DATA",
    }
    created = client.post("/api/targets", json=second, headers=headers)
    other_id = created.json()["id"]
    conflict = {**second, "name": "dup-target"}
    assert (
        client.put(
            f"/api/targets/{other_id}", json=conflict, headers=headers
        ).status_code
        == 400
    )

    # Update with new key content
    with_key = {
        **second,
        "ssh_key_content": "-----BEGIN OPENSSH PRIVATE KEY-----\nnew\n-----END OPENSSH PRIVATE KEY-----",
    }
    assert (
        client.put(
            f"/api/targets/{other_id}", json=with_key, headers=headers
        ).status_code
        == 200
    )

    with test_db.session() as session:
        target = session.query(Target).filter(Target.name == "dup-target").one()
        scan = Scan(target_id=target.id, status="completed")
        session.add(scan)
        session.commit()
        target_id = target.id

    listed = client.get("/api/scans", headers=headers)
    assert listed.status_code == 200
    assert any(row["target_name"] == "dup-target" for row in listed.json())

    # Delete target
    assert (
        client.delete(f"/api/targets/{target_id}", headers=headers).status_code == 200
    )
    assert client.delete(f"/api/targets/{other_id}", headers=headers).status_code == 200

    # SPA index and fallback routes
    index = client.get("/")
    assert index.status_code == 200
    assert "html" in index.headers.get("content-type", "").lower() or index.text

    spa = client.get("/some/client/route")
    assert spa.status_code == 200

    assert client.get("/api/missing").status_code == 404

    app.dependency_overrides.clear()


def test_run_discovery_async_missing_rows(test_db: DbManager) -> None:
    """Return early when async discovery cannot load target or scan rows."""
    from asm_cleanup.web.routers.scans import run_discovery_async

    with patch("asm_cleanup.web.routers.scans.DbManager") as mock_cls:
        mock_db = MagicMock()
        mock_cls.return_value = mock_db
        mock_session = MagicMock()
        mock_db.session.return_value.__enter__.return_value = mock_session
        mock_session.query.return_value.filter.return_value.first.return_value = None
        run_discovery_async(1, 2)


def test_discovery_runner_retains_dictionary_case_for_aliases(
    test_db: DbManager,
) -> None:
    """Verify that TargetDiscoveryRunner uses the case-sensitive dictionary casing for matched aliases.

    If an alias is walked with mixed/lowercase path components, but the database catalogs
    it as uppercase, the emitter must output SQL using the database dictionary's casing.
    """
    mock_conn = MagicMock()

    with test_db.session() as session:
        target = Target(
            name="test-target-case",
            host="127.0.0.1",
            user="oracle",
            destination_disk_group="+DATA",
        )
        session.add(target)
        session.commit()

        scan = Scan(target_id=target.id, status="pending")
        session.add(scan)
        session.commit()

        runner = TargetDiscoveryRunner(session, target, scan)
        service = runner._service

        @contextmanager
        def fake_ssh() -> Generator[MagicMock]:
            yield mock_conn

        service._ssh_connection = fake_ssh  # type: ignore[method-assign]

        # Mock host discovery helpers to avoid SSH execution and return structured data
        service._host.discover_grid_home_and_sid = MagicMock(
            return_value=("/u01/app/grid", "+ASM")
        )
        service._host.discover_disk_groups = MagicMock(return_value=["+DATA"])
        service._host.discover_databases = MagicMock(return_value={"homelab": {}})
        service._host.get_database_home_and_sid = MagicMock(
            return_value=(
                "/u01/app/oracle/product/19.0.0/dbhome_1",
                "homelab",
            )
        )

        db_params = {"db_create_file_dest": "+DATA"}
        db_pdbs = [("TESTDB1", "5061D3DDBF80C747E0631A04010AB48B")]
        # Note the uppercase HOMELAB in db_files
        db_files = [
            (
                "+DATA/HOMELAB/5061D3DDBF80C747E0631A04010AB48B/DATAFILE/junk2.dbf",
                "3",
                "TESTDB1",
                "DATAFILE",
            ),
        ]
        service._host.collect_database_details = MagicMock(
            return_value=(db_params, db_pdbs, db_files)
        )
        # The walked alias returns lowercase homelab in the source path
        service._walk_asm_aliases = MagicMock(
            return_value=WalkAliasOutcome(
                records=[
                    AliasRecord(
                        file_type="DATAFILE",
                        source_path=(
                            "+DATA/homelab/5061D3DDBF80C747E0631A04010AB48B/"
                            "DATAFILE/junk2.dbf"
                        ),
                        target_path=(
                            "+DATA/HOMELAB/5061D3DDBF80C747E0631A04010AB48B/"
                            "DATAFILE/junk2.dbf"
                        ),
                        pdb_guid="5061D3DDBF80C747E0631A04010AB48B",
                        disk_group="+DATA",
                    )
                ],
                paths_attempted=1,
                failed_paths=[],
            )
        )

        runner.run()

        # Reload scan
        session.refresh(scan)
        assert scan.status == "completed"

        # Verify ScanAlias record uses the uppercase HOMELAB path
        aliases = session.query(ScanAlias).filter(ScanAlias.scan_id == scan.id).all()
        assert len(aliases) == 1
        assert (
            aliases[0].source_path
            == "+DATA/HOMELAB/5061D3DDBF80C747E0631A04010AB48B/DATAFILE/junk2.dbf"
        )

        # Verify generated SQL targets the uppercase HOMELAB path
        assert (
            "ALTER DATABASE MOVE DATAFILE '+DATA/HOMELAB/5061D3DDBF80C747E0631A04010AB48B/DATAFILE/junk2.dbf' TO '+DATA';"
            in scan.generated_sql
        )
