"""Authenticated scan trigger and history routes."""

from __future__ import annotations

import json
from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from loguru import logger
from sqlalchemy.orm import Session

from asm_cleanup.db import DbManager, Scan, ScanAlias, Target
from asm_cleanup.discovery import TargetDiscoveryRunner
from asm_cleanup.sql.move_sql_emitter import parse_generated_sql_by_database
from asm_cleanup.web.deps import get_db, require_auth

router = APIRouter(tags=["scans"], dependencies=[Depends(require_auth)])


def run_discovery_async(target_id: int, scan_id: int) -> None:
    """Execute target scan runner asynchronously in the background.

    Args:
        target_id (int): ID of Target to scan.
        scan_id (int): ID of Scan tracking record.
    """
    db_manager_thread = DbManager()
    with db_manager_thread.session() as session:
        target = session.query(Target).filter(Target.id == target_id).first()
        scan = session.query(Scan).filter(Scan.id == scan_id).first()
        if not target or not scan:
            logger.error(
                "missing target_id={} or scan_id={} for async execution",
                target_id,
                scan_id,
            )
            return

        runner = TargetDiscoveryRunner(session, target, scan)
        runner.run()


@router.post("/api/targets/{target_id}/scan")
def trigger_scan(
    target_id: int,
    background_tasks: BackgroundTasks,
    db: Annotated[Session, Depends(get_db)],
) -> dict[str, Any]:
    """Trigger an automated configuration and ASM scan for a target.

    Args:
        target_id (int): Target primary key.
        background_tasks (BackgroundTasks): FastAPI background task queue.
        db (Session): Injected database session.

    Returns:
        dict[str, Any]: Queued scan id and status.

    Raises:
        HTTPException: 404 when the target is missing; 409 when a scan is active.
    """
    target = db.query(Target).filter(Target.id == target_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="Target connection not found.")

    active = (
        db.query(Scan)
        .filter(
            Scan.target_id == target_id,
            Scan.status.in_(("pending", "running")),
        )
        .first()
    )
    if active:
        raise HTTPException(
            status_code=409,
            detail="A scan is already pending or running for this target.",
        )

    scan = Scan(
        target_id=target.id,
        status="pending",
        progress_message="Queued - waiting to start...",
    )
    db.add(scan)
    db.commit()
    db.refresh(scan)

    background_tasks.add_task(run_discovery_async, target.id, scan.id)
    return {
        "scan_id": scan.id,
        "status": "pending",
        "progress_message": scan.progress_message,
        "message": "Scan queued successfully",
    }


@router.get("/api/scans")
def list_scans(
    db: Annotated[Session, Depends(get_db)],
) -> list[dict[str, Any]]:
    """Get list of all executed scans across targets.

    Args:
        db (Session): Injected database session.

    Returns:
        list[dict[str, Any]]: Scan summary rows ordered by created_at descending.
    """
    scans = db.query(Scan).order_by(Scan.created_at.desc()).all()
    return [
        {
            "id": s.id,
            "target_id": s.target_id,
            "target_name": s.target.name if s.target else "Deleted Target",
            "status": s.status,
            "progress_message": s.progress_message,
            "error_message": s.error_message,
            "created_at": s.created_at.isoformat(),
        }
        for s in scans
    ]


@router.get("/api/scans/{scan_id}")
def get_scan_details(
    scan_id: int,
    db: Annotated[Session, Depends(get_db)],
) -> dict[str, Any]:
    """Retrieve details of a specific scan, including configuration and SQL.

    Args:
        scan_id (int): Scan primary key.
        db (Session): Injected database session.

    Returns:
        dict[str, Any]: Scan metadata, aliases, and generated SQL.

    Raises:
        HTTPException: 404 when the scan is missing.
    """
    s = db.query(Scan).filter(Scan.id == scan_id).first()
    if not s:
        raise HTTPException(status_code=404, detail="Scan not found.")

    aliases = db.query(ScanAlias).filter(ScanAlias.scan_id == s.id).all()
    alias_list = [
        {
            "id": a.id,
            "database_name": a.database_name,
            "container_name": a.container_name,
            "file_type": a.file_type,
            "source_path": a.source_path,
            "target_path": a.target_path,
            "pdb_guid": a.pdb_guid,
        }
        for a in aliases
    ]

    return {
        "id": s.id,
        "target_id": s.target_id,
        "target_name": s.target.name if s.target else "Deleted Target",
        "status": s.status,
        "progress_message": s.progress_message,
        "error_message": s.error_message,
        "grid_home": s.grid_home,
        "disk_groups": json.loads(s.disk_groups) if s.disk_groups else [],
        "databases": json.loads(s.databases) if s.databases else {},
        "generated_sql": s.generated_sql,
        "generated_sql_by_database": parse_generated_sql_by_database(s.generated_sql),
        "created_at": s.created_at.isoformat(),
        "aliases": alias_list,
    }
