"""Database engine, sessions, and schema migrations."""

from __future__ import annotations

import os
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

from loguru import logger
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

DEFAULT_DB_FILE = "asm_cleanup.db"
DEFAULT_DATABASE_URL = f"sqlite:///{DEFAULT_DB_FILE}"


class DbManager:
    """Manages the database engine, sessions, and schema migrations.

    Attributes:
        database_url (str): Connection string for SQLAlchemy engine.
        engine: SQLAlchemy Engine instance.
        session_factory: Session maker utility.
    """

    def __init__(self, database_url: str | None = None) -> None:
        """Initialize connection engine.

        Args:
            database_url (str | None): Optional SQLAlchemy connection string.
        """
        self.database_url = database_url or os.getenv(
            "DATABASE_URL", DEFAULT_DATABASE_URL
        )
        logger.debug("initializing engine with database_url={}", self.database_url)
        self.engine = create_engine(
            self.database_url,
            connect_args=(
                {"check_same_thread": False}
                if self.database_url.startswith("sqlite")
                else {}
            ),
        )
        self.session_factory = sessionmaker(bind=self.engine)

    @contextmanager
    def session(self) -> Generator[Session]:
        """Provide a transactional session block.

        Yields:
            Session: SQLAlchemy connection session.
        """
        session = self.session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def run_migrations(self) -> None:
        """Programmatically trigger Alembic schema upgrades to head."""
        logger.info("running database migrations upgrade to head")
        from alembic import command
        from alembic.config import Config

        config_path = Path(__file__).parent.parent.parent / "alembic.ini"
        if not config_path.is_file():
            config_path = Path("alembic.ini")

        if not config_path.is_file():
            logger.warning(
                "alembic.ini not found at {!r}; skip migrations",
                str(config_path.absolute()),
            )
            return

        alembic_cfg = Config(str(config_path))
        alembic_cfg.set_main_option("sqlalchemy.url", self.database_url)
        try:
            command.upgrade(alembic_cfg, "head")
            logger.info("database migrations completed successfully")
        except Exception as exc:
            logger.error("database migrations failed: {}", exc)
            raise
