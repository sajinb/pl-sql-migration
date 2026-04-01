"""Pipeline runner that executes migration in a background thread with log streaming."""

from __future__ import annotations

import asyncio
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tsql_migration.api.database import async_session
from tsql_migration.api.log_streamer import (
    LogBroadcaster,
    LogEntry,
    capture_stdout,
    get_broadcaster,
    remove_broadcaster,
)
from tsql_migration.api.models import (
    MigrationStage,
    PIPELINE_STAGES,
    Project,
    ProjectStatus,
    StageStatus,
)
from tsql_migration.config import load_config, MigrationConfig
from tsql_migration.pipeline.graph import compile_pipeline
from tsql_migration.state import MigrationState

_executor = ThreadPoolExecutor(max_workers=2)


async def start_migration(project_id: int) -> None:
    """Launch the migration pipeline in a background thread."""
    loop = asyncio.get_event_loop()
    broadcaster = get_broadcaster(project_id)
    broadcaster.set_loop(loop)

    # Run the blocking pipeline in a thread
    loop.run_in_executor(_executor, _run_pipeline_sync, project_id, broadcaster)


def _run_pipeline_sync(project_id: int, broadcaster: LogBroadcaster) -> None:
    """Synchronous pipeline execution — runs in a thread."""
    # We need a new event loop for async DB calls from this thread
    loop = asyncio.new_event_loop()

    try:
        loop.run_until_complete(_run_pipeline(project_id, broadcaster))
    except Exception as e:
        broadcaster.emit(LogEntry(stage="pipeline", message=f"Fatal error: {e}", level="error"))
        loop.run_until_complete(_mark_project_failed(project_id, str(e)))
    finally:
        broadcaster.close_all()
        remove_broadcaster(project_id)
        loop.close()


async def _run_pipeline(project_id: int, broadcaster: LogBroadcaster) -> None:
    """Execute the 7-stage pipeline with per-stage status updates."""
    async with async_session() as db:
        project = await db.get(Project, project_id)
        if not project:
            return

        project.status = ProjectStatus.MIGRATING
        await db.commit()

    # Load config
    config = load_config("config.yaml")
    sql_input_dir = str(Path("projects") / str(project_id) / "sql-input" / "procedures")
    output_dir = str(Path("projects") / str(project_id) / "output")
    config = config.model_copy(update={
        "sql_input_dir": sql_input_dir,
        "output_dir": output_dir,
    })

    # Validate input
    sql_dir = Path(sql_input_dir)
    if not sql_dir.exists() or not list(sql_dir.glob("*.sql")):
        await _mark_project_failed(project_id, "No SQL files found")
        broadcaster.emit(LogEntry(stage="pipeline", message="No SQL files found", level="error"))
        return

    # Build initial state
    initial_state = MigrationState(
        sql_input_dir=sql_input_dir,
        config=config,
    )

    # Compile the pipeline
    graph = compile_pipeline()

    # We run the pipeline node-by-node to track stage progress
    stage_holder = ["pipeline"]

    stage_names = [s[0] for s in PIPELINE_STAGES]

    broadcaster.emit(LogEntry(stage="pipeline", message="Migration started"))

    with capture_stdout(broadcaster, stage_holder):
        current_state = initial_state.model_dump()

        for stage_name, stage_label in PIPELINE_STAGES:
            stage_holder[0] = stage_name
            broadcaster.emit(LogEntry(
                stage=stage_name,
                message=f"Starting stage: {stage_label}",
            ))

            # Mark stage running
            await _update_stage(project_id, stage_name, StageStatus.RUNNING)

            try:
                # Import and call the node function directly
                node_fn = _get_node_function(stage_name)
                state_obj = MigrationState(**current_state)
                result = node_fn(state_obj)

                # Merge result into current state
                if result:
                    current_state.update(result)

                await _update_stage(project_id, stage_name, StageStatus.COMPLETED)
                broadcaster.emit(LogEntry(
                    stage=stage_name,
                    message=f"Stage completed: {stage_label}",
                ))

            except Exception as e:
                error_msg = f"{stage_label} failed: {e}\n{traceback.format_exc()}"
                broadcaster.emit(LogEntry(stage=stage_name, message=error_msg, level="error"))
                await _update_stage(project_id, stage_name, StageStatus.FAILED, str(e))

                # Mark remaining stages as skipped
                idx = stage_names.index(stage_name)
                for remaining in stage_names[idx + 1:]:
                    await _update_stage(project_id, remaining, StageStatus.SKIPPED)

                await _mark_project_failed(project_id, str(e))
                return

    # All stages completed
    async with async_session() as db:
        project = await db.get(Project, project_id)
        if project:
            project.status = ProjectStatus.COMPLETED
            await db.commit()

    broadcaster.emit(LogEntry(stage="pipeline", message="Migration completed successfully!"))


def _get_node_function(stage_name: str):
    """Import and return the node function for a stage."""
    from tsql_migration.pipeline.nodes.parse_node import parse_node
    from tsql_migration.pipeline.nodes.analyze_node import analyze_node
    from tsql_migration.pipeline.nodes.plan_node import plan_node
    from tsql_migration.pipeline.nodes.extract_schema_node import extract_schema_node
    from tsql_migration.pipeline.nodes.prepare_node import prepare_node
    from tsql_migration.pipeline.nodes.migrate_node import migrate_node
    from tsql_migration.pipeline.nodes.generate_node import generate_node

    return {
        "parse": parse_node,
        "analyze": analyze_node,
        "plan": plan_node,
        "extract_schema": extract_schema_node,
        "prepare": prepare_node,
        "migrate": migrate_node,
        "generate": generate_node,
    }[stage_name]


async def _update_stage(
    project_id: int,
    stage_name: str,
    status: StageStatus,
    error_message: str | None = None,
) -> None:
    """Update a stage's status in the database."""
    async with async_session() as db:
        result = await db.execute(
            select(MigrationStage).where(
                MigrationStage.project_id == project_id,
                MigrationStage.stage_name == stage_name,
            )
        )
        stage = result.scalar_one_or_none()
        if stage:
            stage.status = status
            if status == StageStatus.RUNNING:
                stage.started_at = datetime.now(timezone.utc)
            elif status in (StageStatus.COMPLETED, StageStatus.FAILED):
                stage.completed_at = datetime.now(timezone.utc)
            if error_message:
                stage.error_message = error_message
            await db.commit()


async def _mark_project_failed(project_id: int, error: str) -> None:
    async with async_session() as db:
        project = await db.get(Project, project_id)
        if project:
            project.status = ProjectStatus.FAILED
            project.error_message = error
            await db.commit()
