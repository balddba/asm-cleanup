"""Authenticated CRUD routes for target connection profiles."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from asm_cleanup.auth.ssh_key_store import SshKeyStore
from asm_cleanup.db import Target
from asm_cleanup.schemas.target_base import TargetBase, TargetResponse
from asm_cleanup.web.deps import get_db, get_ssh_key_store, require_auth
from asm_cleanup.web.serializers import target_to_response

router = APIRouter(
    prefix="/api/targets",
    tags=["targets"],
    dependencies=[Depends(require_auth)],
)


@router.get("", response_model=list[TargetResponse])
def list_targets(
    db: Annotated[Session, Depends(get_db)],
    store: Annotated[SshKeyStore, Depends(get_ssh_key_store)],
) -> list[TargetResponse]:
    """Retrieve all saved target connection profiles.

    Private key paste content is never returned; use has_ssh_key instead.

    Args:
        db (Session): Injected database session.
        store (SshKeyStore): Encrypted store for pasted SSH keys.

    Returns:
        list[TargetResponse]: Public target profiles ordered by name.
    """
    targets = db.query(Target).order_by(Target.name).all()
    return [target_to_response(t, store) for t in targets]


@router.post("", status_code=201)
def create_target(
    payload: TargetBase,
    db: Annotated[Session, Depends(get_db)],
    store: Annotated[SshKeyStore, Depends(get_ssh_key_store)],
) -> dict[str, Any]:
    """Create a new target configuration profile.

    Args:
        payload (TargetBase): Target fields from the request body.
        db (Session): Injected database session.
        store (SshKeyStore): Encrypted store for pasted SSH keys.

    Returns:
        dict[str, Any]: Created target id and confirmation message.

    Raises:
        HTTPException: 400 when the target name already exists.
    """
    existing = db.query(Target).filter(Target.name == payload.name).first()
    if existing:
        raise HTTPException(
            status_code=400,
            detail=f"Target with name '{payload.name}' already exists.",
        )

    t = Target(
        name=payload.name,
        host=payload.host,
        user=payload.user,
        ssh_key_path=payload.ssh_key_path,
        ssh_key_content=None,
        grid_home=payload.grid_home,
        oracle_sid=payload.oracle_sid,
        destination_disk_group=payload.destination_disk_group,
        move_online=payload.move_online,
    )
    db.add(t)
    db.commit()
    db.refresh(t)
    pasted = (payload.ssh_key_content or "").strip()
    if pasted:
        store.set(int(t.id), pasted)
    return {"id": t.id, "message": "Target created successfully"}


@router.put("/{target_id}")
def update_target(
    target_id: int,
    payload: TargetBase,
    db: Annotated[Session, Depends(get_db)],
    store: Annotated[SshKeyStore, Depends(get_ssh_key_store)],
) -> dict[str, Any]:
    """Update connection details of an existing target profile.

    Args:
        target_id (int): Target primary key.
        payload (TargetBase): Updated target fields.
        db (Session): Injected database session.
        store (SshKeyStore): Encrypted store for pasted SSH keys.

    Returns:
        dict[str, Any]: Confirmation message.

    Raises:
        HTTPException: 404 when the target is missing; 400 on name conflict.
    """
    t = db.query(Target).filter(Target.id == target_id).first()
    if not t:
        raise HTTPException(status_code=404, detail="Target profile not found.")

    existing = (
        db.query(Target)
        .filter(Target.name == payload.name, Target.id != target_id)
        .first()
    )
    if existing:
        raise HTTPException(
            status_code=400,
            detail=f"Another target with name '{payload.name}' already exists.",
        )

    t.name = payload.name
    t.host = payload.host
    t.user = payload.user
    t.ssh_key_path = payload.ssh_key_path
    # Keep stored key material when the client omits or blanks the field
    # (list/get never return ssh_key_content, so edit forms start empty).
    pasted = (payload.ssh_key_content or "").strip()
    if pasted:
        store.set(int(t.id), pasted)
        t.ssh_key_content = None
    t.grid_home = payload.grid_home
    t.oracle_sid = payload.oracle_sid
    t.destination_disk_group = payload.destination_disk_group
    t.move_online = payload.move_online
    db.commit()
    return {"message": "Target updated successfully"}


@router.delete("/{target_id}")
def delete_target(
    target_id: int,
    db: Annotated[Session, Depends(get_db)],
    store: Annotated[SshKeyStore, Depends(get_ssh_key_store)],
) -> dict[str, Any]:
    """Delete a target configuration profile.

    Args:
        target_id (int): Target primary key.
        db (Session): Injected database session.
        store (SshKeyStore): Encrypted store for pasted SSH keys.

    Returns:
        dict[str, Any]: Confirmation message.

    Raises:
        HTTPException: 404 when the target is missing.
    """
    t = db.query(Target).filter(Target.id == target_id).first()
    if not t:
        raise HTTPException(status_code=404, detail="Target profile not found.")
    store.delete(int(t.id))
    db.delete(t)
    db.commit()
    return {"message": "Target profile deleted"}
