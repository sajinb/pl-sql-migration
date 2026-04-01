"""Project CRUD and file upload endpoints."""

from __future__ import annotations

import shutil
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tsql_migration.api.database import get_db
from tsql_migration.api.models import (
    MigrationLog,
    MigrationRun,
    MigrationStage,
    Project,
    ProjectCreate,
    ProjectDetailResponse,
    ProjectResponse,
    ProjectStatus,
    RunResponse,
)

router = APIRouter(prefix="/api/projects", tags=["projects"])

UPLOAD_BASE = Path("projects")


@router.post("", response_model=ProjectResponse, status_code=201)
async def create_project(body: ProjectCreate, db: AsyncSession = Depends(get_db)):
    """Create a new migration project."""
    project = Project(name=body.name, description=body.description)
    db.add(project)
    await db.flush()

    # Create upload directory
    project_dir = UPLOAD_BASE / str(project.id)
    sql_dir = project_dir / "sql-input" / "procedures"
    ddl_dir = project_dir / "sql-input" / "ddl"
    output_dir = project_dir / "output"
    sql_dir.mkdir(parents=True, exist_ok=True)
    ddl_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    project.sql_input_dir = str(sql_dir)
    project.output_dir = str(output_dir)

    await db.commit()
    await db.refresh(project)
    return project


@router.get("", response_model=list[ProjectResponse])
async def list_projects(db: AsyncSession = Depends(get_db)):
    """List all projects."""
    result = await db.execute(select(Project).order_by(Project.created_at.desc()))
    return result.scalars().all()


@router.get("/{project_id}", response_model=ProjectDetailResponse)
async def get_project(project_id: int, db: AsyncSession = Depends(get_db)):
    """Get project with all runs."""
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    result = await db.execute(
        select(MigrationRun)
        .where(MigrationRun.project_id == project_id)
        .order_by(MigrationRun.run_number.desc())
    )
    runs = result.scalars().all()

    return ProjectDetailResponse(
        project=ProjectResponse.model_validate(project),
        runs=[RunResponse.model_validate(r) for r in runs],
    )


@router.delete("/{project_id}", status_code=204)
async def delete_project(project_id: int, db: AsyncSession = Depends(get_db)):
    """Delete a project and all its runs, stages, logs, and files."""
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    if project.status == ProjectStatus.MIGRATING:
        raise HTTPException(status_code=409, detail="Cannot delete while migration is running")

    # Delete files
    project_dir = UPLOAD_BASE / str(project_id)
    if project_dir.exists():
        shutil.rmtree(project_dir)

    # Cascade delete: logs -> stages -> runs -> project
    runs_result = await db.execute(
        select(MigrationRun).where(MigrationRun.project_id == project_id)
    )
    for run in runs_result.scalars().all():
        # Delete logs for this run
        logs_result = await db.execute(
            select(MigrationLog).where(MigrationLog.run_id == run.id)
        )
        for log in logs_result.scalars().all():
            await db.delete(log)
        # Delete stages for this run
        stages_result = await db.execute(
            select(MigrationStage).where(MigrationStage.run_id == run.id)
        )
        for stage in stages_result.scalars().all():
            await db.delete(stage)
        await db.delete(run)

    await db.delete(project)
    await db.commit()


@router.post("/{project_id}/upload", response_model=ProjectResponse)
async def upload_files(
    project_id: int,
    files: list[UploadFile],
    db: AsyncSession = Depends(get_db),
):
    """Upload SQL files to a project."""
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    if project.status == ProjectStatus.MIGRATING:
        raise HTTPException(status_code=409, detail="Cannot upload while migration is running")

    sql_dir = Path(project.sql_input_dir)
    sql_dir.mkdir(parents=True, exist_ok=True)

    for file in files:
        if not file.filename:
            continue
        filename = Path(file.filename).name
        if not filename.lower().endswith(".sql"):
            continue
        dest = sql_dir / filename
        content = await file.read()
        dest.write_bytes(content)

    project.file_count = len(list(sql_dir.glob("*.sql")))
    project.status = ProjectStatus.READY if project.file_count > 0 else ProjectStatus.CREATED
    await db.commit()
    await db.refresh(project)
    return project


@router.get("/{project_id}/files")
async def list_files(project_id: int, db: AsyncSession = Depends(get_db)):
    """List uploaded SQL files for a project."""
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    sql_dir = Path(project.sql_input_dir)
    if not sql_dir.exists():
        return []

    files = []
    for f in sorted(sql_dir.glob("*.sql")):
        stat = f.stat()
        files.append({
            "name": f.name,
            "size": stat.st_size,
            "lines": f.read_text(errors="replace").count("\n") + 1,
        })
    return files
