"""Pipeline runner that executes migration in a background thread with log streaming."""

from __future__ import annotations

import asyncio
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import func, select
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
    LogLevel,
    MigrationLog,
    MigrationRun,
    MigrationStage,
    PIPELINE_STAGES,
    Project,
    ProjectStatus,
    RunStatus,
    StageStatus,
)
from tsql_migration.config import load_config
from tsql_migration.state import MigrationState

_executor = ThreadPoolExecutor(max_workers=2)


async def start_migration(project_id: int, run_id: int) -> None:
    """Launch the migration pipeline in a background thread."""
    loop = asyncio.get_event_loop()
    broadcaster = get_broadcaster(run_id)
    broadcaster.set_loop(loop)

    loop.run_in_executor(_executor, _run_pipeline_sync, project_id, run_id, broadcaster)


def _run_pipeline_sync(project_id: int, run_id: int, broadcaster: LogBroadcaster) -> None:
    """Synchronous pipeline execution — runs in a thread."""
    loop = asyncio.new_event_loop()

    try:
        loop.run_until_complete(_run_pipeline(project_id, run_id, broadcaster))
    except Exception as e:
        broadcaster.emit(LogEntry(stage="pipeline", message=f"Fatal error: {e}", level="error"))
        loop.run_until_complete(_mark_run_failed(run_id, str(e)))
        loop.run_until_complete(_mark_project_failed(project_id, str(e)))
    finally:
        broadcaster.close_all()
        remove_broadcaster(run_id)
        loop.close()


async def _run_pipeline(project_id: int, run_id: int, broadcaster: LogBroadcaster) -> None:
    """Execute the 7-stage pipeline with per-stage status updates and log persistence."""

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
        msg = "No SQL files found"
        await _emit_and_persist(run_id, broadcaster, "pipeline", msg, "error")
        await _mark_run_failed(run_id, msg)
        await _mark_project_failed(project_id, msg)
        return

    # Build initial state
    initial_state = MigrationState(sql_input_dir=sql_input_dir, config=config)

    stage_holder = ["pipeline"]
    stage_names = [s[0] for s in PIPELINE_STAGES]

    await _emit_and_persist(run_id, broadcaster, "pipeline", "Migration started")

    with capture_stdout(broadcaster, stage_holder, run_id):
        current_state = initial_state.model_dump()

        for stage_name, stage_label in PIPELINE_STAGES:
            stage_holder[0] = stage_name
            await _emit_and_persist(
                run_id, broadcaster, stage_name, f"Starting stage: {stage_label}"
            )
            await _update_stage(run_id, stage_name, StageStatus.RUNNING)

            try:
                node_fn = _get_node_function(stage_name)
                state_obj = MigrationState(**current_state)
                result = node_fn(state_obj)

                if result:
                    current_state.update(result)

                await _update_stage(run_id, stage_name, StageStatus.COMPLETED)
                await _emit_and_persist(
                    run_id, broadcaster, stage_name, f"Stage completed: {stage_label}"
                )

            except Exception as e:
                error_msg = f"{stage_label} failed: {e}\n{traceback.format_exc()}"
                await _emit_and_persist(run_id, broadcaster, stage_name, error_msg, "error")
                await _update_stage(run_id, stage_name, StageStatus.FAILED, str(e))

                # Mark remaining stages as skipped
                idx = stage_names.index(stage_name)
                for remaining in stage_names[idx + 1:]:
                    await _update_stage(run_id, remaining, StageStatus.SKIPPED)

                await _mark_run_failed(run_id, str(e))
                await _mark_project_failed(project_id, str(e))
                return

    # Success — update run and project
    async with async_session() as db:
        run = await db.get(MigrationRun, run_id)
        if run:
            run.status = RunStatus.COMPLETED
            run.completed_at = datetime.now(timezone.utc)
            # Extract summary from final state
            run.procedures_migrated = len(current_state.get("migrated_procedures", {}))
            run.procedures_failed = len(current_state.get("failed_procedures", {}))
            run.files_generated = len(current_state.get("output_files", []))
            await db.commit()

        project = await db.get(Project, project_id)
        if project:
            project.status = ProjectStatus.COMPLETED
            project.error_message = None
            await db.commit()

    await _emit_and_persist(run_id, broadcaster, "pipeline", "Migration completed successfully!")


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


async def _emit_and_persist(
    run_id: int,
    broadcaster: LogBroadcaster,
    stage: str,
    message: str,
    level: str = "info",
) -> None:
    """Emit log to WebSocket subscribers AND persist to migration_logs table."""
    broadcaster.emit(LogEntry(stage=stage, message=message, level=level))
    async with async_session() as db:
        log = MigrationLog(
            run_id=run_id,
            stage=stage,
            level=LogLevel(level),
            message=message,
        )
        db.add(log)
        await db.commit()


async def _update_stage(
    run_id: int,
    stage_name: str,
    status: StageStatus,
    error_message: str | None = None,
) -> None:
    """Update a stage's status in the database."""
    async with async_session() as db:
        result = await db.execute(
            select(MigrationStage).where(
                MigrationStage.run_id == run_id,
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


async def _mark_run_failed(run_id: int, error: str) -> None:
    async with async_session() as db:
        run = await db.get(MigrationRun, run_id)
        if run:
            run.status = RunStatus.FAILED
            run.error_message = error
            run.completed_at = datetime.now(timezone.utc)
            await db.commit()


async def _mark_project_failed(project_id: int, error: str) -> None:
    async with async_session() as db:
        project = await db.get(Project, project_id)
        if project:
            project.status = ProjectStatus.FAILED
            project.error_message = error
            await db.commit()
