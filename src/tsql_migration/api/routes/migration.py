"""Migration trigger and WebSocket log streaming endpoints."""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from sqlalchemy.ext.asyncio import AsyncSession

from tsql_migration.api.database import get_db
from tsql_migration.api.log_streamer import get_broadcaster
from tsql_migration.api.models import Project, ProjectResponse, ProjectStatus
from tsql_migration.api.runner import start_migration

router = APIRouter(prefix="/api/projects", tags=["migration"])


@router.post("/{project_id}/migrate", response_model=ProjectResponse)
async def trigger_migration(project_id: int, db: AsyncSession = Depends(get_db)):
    """Start the migration pipeline for a project."""
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    if project.status == ProjectStatus.MIGRATING:
        raise HTTPException(status_code=409, detail="Migration already running")

    if project.file_count == 0:
        raise HTTPException(status_code=400, detail="No SQL files uploaded")

    # Reset stages to pending for re-runs
    from sqlalchemy import select
    from tsql_migration.api.models import MigrationStage, StageStatus

    result = await db.execute(
        select(MigrationStage).where(MigrationStage.project_id == project_id)
    )
    for stage in result.scalars().all():
        stage.status = StageStatus.PENDING
        stage.started_at = None
        stage.completed_at = None
        stage.error_message = None

    project.status = ProjectStatus.MIGRATING
    project.error_message = None
    await db.commit()
    await db.refresh(project)

    # Launch pipeline in background
    await start_migration(project_id)

    return project


@router.websocket("/{project_id}/ws")
async def migration_logs_ws(websocket: WebSocket, project_id: int):
    """WebSocket endpoint that streams real-time migration logs."""
    await websocket.accept()

    broadcaster = get_broadcaster(project_id)
    sub_id, queue = broadcaster.subscribe()

    try:
        while True:
            entry = await queue.get()
            if entry is None:
                # End of stream
                await websocket.send_json({
                    "type": "complete",
                    "stage": "pipeline",
                    "message": "Stream ended",
                })
                break

            await websocket.send_json({
                "type": "log",
                "stage": entry.stage,
                "message": entry.message,
                "level": entry.level,
            })
    except WebSocketDisconnect:
        pass
    finally:
        broadcaster.unsubscribe(sub_id)
