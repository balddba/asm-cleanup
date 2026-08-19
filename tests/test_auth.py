"""Tests for single-password JWT authentication on the web API."""

from __future__ import annotations

import os
from collections.abc import Generator
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

# Auth env must be set before importing the app (settings load on first request).
os.environ.setdefault("ASM_CLEANUP_PASSWORD", "test-password")
os.environ.setdefault(
    "ASM_CLEANUP_JWT_SECRET",
    "test-jwt-secret-for-unit-tests-32b+",
)
os.environ.setdefault("ASM_CLEANUP_JWT_TTL_SECONDS", "86400")

from asm_cleanup.db import Base, DbManager
from asm_cleanup.web import app, get_db

TEST_DB_FILE = "test_auth_asm_cleanup.db"
TEST_DATABASE_URL = f"sqlite:///{TEST_DB_FILE}"
TEST_PASSWORD = os.environ["ASM_CLEANUP_PASSWORD"]
TEST_JWT_SECRET = os.environ["ASM_CLEANUP_JWT_SECRET"]


@pytest.fixture(name="auth_client")
def fixture_auth_client() -> Generator[TestClient]:
    """Provide a TestClient with an isolated SQLite database.

    Yields:
        TestClient: FastAPI test client with get_db overridden.
    """
    if os.path.exists(TEST_DB_FILE):
        try:
            os.unlink(TEST_DB_FILE)
        except OSError:
            pass

    db_mgr = DbManager(TEST_DATABASE_URL)
    Base.metadata.create_all(bind=db_mgr.engine)

    def override_get_db() -> Generator[Session]:
        with db_mgr.session() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)
    yield client
    app.dependency_overrides.clear()
    Base.metadata.drop_all(bind=db_mgr.engine)
    db_mgr.engine.dispose()
    if os.path.exists(TEST_DB_FILE):
        try:
            os.unlink(TEST_DB_FILE)
        except OSError:
            pass


def _login(client: TestClient, password: str = TEST_PASSWORD) -> str:
    """Log in and return the access token.

    Args:
        client (TestClient): FastAPI test client.
        password (str): Password to submit.

    Returns:
        str: Bearer access token.
    """
    res = client.post("/api/auth/login", json={"password": password})
    assert res.status_code == 200
    return res.json()["access_token"]


def test_login_success(auth_client: TestClient) -> None:
    """Login with the correct password returns a bearer token."""
    res = auth_client.post(
        "/api/auth/login",
        json={"password": TEST_PASSWORD},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["token_type"] == "bearer"
    assert isinstance(body["access_token"], str)
    assert body["access_token"]


def test_login_wrong_password(auth_client: TestClient) -> None:
    """Login with an incorrect password returns 401."""
    res = auth_client.post(
        "/api/auth/login",
        json={"password": "wrong-password"},
    )
    assert res.status_code == 401


def test_list_targets_without_bearer(auth_client: TestClient) -> None:
    """Protected routes reject requests without an Authorization header."""
    res = auth_client.get("/api/targets")
    assert res.status_code == 401


def test_list_targets_with_valid_token(auth_client: TestClient) -> None:
    """Protected routes accept a valid bearer token."""
    token = _login(auth_client)
    res = auth_client.get(
        "/api/targets",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 200
    assert isinstance(res.json(), list)


def test_expired_token_returns_401(auth_client: TestClient) -> None:
    """An expired JWT is rejected with 401."""
    now = datetime.now(UTC)
    expired = jwt.encode(
        {
            "sub": "asm-cleanup",
            "iat": now - timedelta(hours=2),
            "exp": now - timedelta(hours=1),
        },
        TEST_JWT_SECRET,
        algorithm="HS256",
    )
    res = auth_client.get(
        "/api/targets",
        headers={"Authorization": f"Bearer {expired}"},
    )
    assert res.status_code == 401


def test_invalid_token_returns_401(auth_client: TestClient) -> None:
    """A malformed or wrongly signed JWT is rejected with 401."""
    res = auth_client.get(
        "/api/targets",
        headers={"Authorization": "Bearer not-a-valid-token"},
    )
    assert res.status_code == 401
