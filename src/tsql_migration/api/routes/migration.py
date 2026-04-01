"""Migration trigger, run details, and WebSocket log streaming endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from tsql_migration.api.database import get_db
from tsql_migration.api.log_streamer import get_broadcaster
from tsql_migration.api.models import (
    LogResponse,
    MigrationLog,
    MigrationRun,
    MigrationStage,
    PIPELINE_STAGES,
    Project,
    ProjectResponse,
    ProjectStatus,
    RunDetailResponse,
    RunResponse,
    RunStatus,
    StageResponse,
    StageStatus,
)
from tsql_migration.api.runner import start_migration

router = APIRouter(prefix="/api/projects", tags=["migration"])


@router.post("/{project_id}/migrate", response_model=RunResponse)
async def trigger_migration(project_id: int, db: AsyncSession = Depends(get_db)):
    """Start a new migration run for a project."""
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    if project.status == ProjectStatus.MIGRATING:
        raise HTTPException(status_code=409, detail="Migration already running")

    if project.file_count == 0:
        raise HTTPException(status_code=400, detail="No SQL files uploaded")

    # Determine run number
    result = await db.execute(
        select(func.coalesce(func.max(MigrationRun.run_number), 0))
        .where(MigrationRun.project_id == project_id)
    )
    max_run = result.scalar()
    run_number = max_run + 1

    # Create run
    run = MigrationRun(
        project_id=project_id,
        run_number=run_number,
        status=RunStatus.RUNNING,
    )
    db.add(run)
    await db.flush()

    # Create stage records for this run
    for order, (stage_name, _) in enumerate(PIPELINE_STAGES):
        stage = MigrationStage(
            run_id=run.id,
            stage_name=stage_name,
            stage_order=order,
            status=StageStatus.PENDING,
        )
        db.add(stage)

    project.status = ProjectStatus.MIGRATING
    project.error_message = None
    await db.commit()
    await db.refresh(run)

    # Launch pipeline in background
    await start_migration(project_id, run.id)

    return run


@router.get("/{project_id}/runs/{run_id}", response_model=RunDetailResponse)
async def get_run(project_id: int, run_id: int, db: AsyncSession = Depends(get_db)):
    """Get a specific run with its stages."""
    run = await db.get(MigrationRun, run_id)
    if not run or run.project_id != project_id:
        raise HTTPException(status_code=404, detail="Run not found")

    result = await db.execute(
        select(MigrationStage)
        .where(MigrationStage.run_id == run_id)
        .order_by(MigrationStage.stage_order)
    )
    stages = result.scalars().all()

    return RunDetailResponse(
        run=RunResponse.model_validate(run),
        stages=[StageResponse.model_validate(s) for s in stages],
    )


@router.get("/{project_id}/runs/{run_id}/logs", response_model=list[LogResponse])
async def get_run_logs(
    project_id: int,
    run_id: int,
    stage: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    """Get persisted logs for a run. Optionally filter by stage."""
    run = await db.get(MigrationRun, run_id)
    if not run or run.project_id != project_id:
        raise HTTPException(status_code=404, detail="Run not found")

    query = (
        select(MigrationLog)
        .where(MigrationLog.run_id == run_id)
        .order_by(MigrationLog.id)
    )
    if stage:
        query = query.where(MigrationLog.stage == stage)

    result = await db.execute(query)
    return [LogResponse.model_validate(log) for log in result.scalars().all()]


@router.websocket("/{project_id}/runs/{run_id}/ws")
async def migration_logs_ws(websocket: WebSocket, project_id: int, run_id: int):
    """WebSocket endpoint that streams real-time migration logs for a run."""
    await websocket.accept()

    broadcaster = get_broadcaster(run_id)
    sub_id, queue = broadcaster.subscribe()

    try:
        while True:
            entry = await queue.get()
            if entry is None:
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
