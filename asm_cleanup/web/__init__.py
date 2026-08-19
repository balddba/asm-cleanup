"""FastAPI backend API and static web interface package.

Exposes REST endpoints for managing target configurations and executing automated
scans. Supports background task execution for discovery scans and serves the
single-page web app. Import `app` for uvicorn (`asm_cleanup.web:app`).
"""

from asm_cleanup.web.app import create_app
from asm_cleanup.web.deps import db_manager, get_db

app = create_app()

__all__ = [
    "app",
    "create_app",
    "db_manager",
    "get_db",
]
