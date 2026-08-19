"""FastAPI application factory and lifespan for the web UI and API."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from asm_cleanup.web.deps import db_manager
from asm_cleanup.web.routers import auth, scans, targets

static_path = Path(__file__).resolve().parent.parent / "static"


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Run database migrations on startup, then yield control to the app.

    Args:
        _app (FastAPI): FastAPI application instance (unused).

    Yields:
        None: Control while the application is serving requests.
    """
    from asm_cleanup.logging_config import configure_logging

    configure_logging()
    db_manager.run_migrations()
    yield


def create_app() -> FastAPI:
    """Build and configure the FastAPI application.

    Returns:
        FastAPI: Configured app with auth, target, and scan routes.
    """
    application = FastAPI(
        title="ASM Clean-Up",
        lifespan=lifespan,
    )

    application.include_router(auth.router)
    application.include_router(targets.router)
    application.include_router(scans.router)

    static_path.mkdir(parents=True, exist_ok=True)
    if static_path.is_dir():
        application.mount(
            "/static",
            StaticFiles(directory=str(static_path)),
            name="static",
        )

    @application.get("/", response_class=HTMLResponse)
    def serve_index() -> HTMLResponse:
        """Serve the single-page application frontend.

        Returns:
            HTMLResponse: Index HTML or a short placeholder page.
        """
        index_file = static_path / "index.html"
        if not index_file.is_file():
            return HTMLResponse(
                "<h1>ASM Clean-Up</h1><p>Frontend assets are being generated...</p>"
            )
        return HTMLResponse(index_file.read_text(encoding="utf-8"))

    @application.get("/{full_path:path}")
    def serve_spa_fallbacks(full_path: str) -> Any:
        """Fallback index route for client-side routing structures.

        Args:
            full_path (str): Requested path under the SPA.

        Returns:
            Any: Index file response for non-API paths.

        Raises:
            HTTPException: 404 for api/static prefixes or missing index.
        """
        if full_path.startswith(("api/", "static/")):
            raise HTTPException(status_code=404, detail="Endpoint not found")
        index_file = static_path / "index.html"
        if index_file.is_file():
            return FileResponse(str(index_file))
        raise HTTPException(status_code=404, detail="Not found")

    return application
