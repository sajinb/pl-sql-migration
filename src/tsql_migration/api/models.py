"""SQLAlchemy models and Pydantic schemas for the API."""

from __future__ import annotations

import enum
from datetime import datetime
from typing import Optional

from pydantic import BaseModel
from sqlalchemy import DateTime, Enum, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from tsql_migration.api.database import Base


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class ProjectStatus(str, enum.Enum):
    CREATED = "created"
    UPLOADING = "uploading"
    READY = "ready"
    MIGRATING = "migrating"
    COMPLETED = "completed"
    FAILED = "failed"


class StageStatus(str, enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


# ---------------------------------------------------------------------------
# SQLAlchemy ORM models
# ---------------------------------------------------------------------------

class Project(Base):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[ProjectStatus] = mapped_column(
        Enum(ProjectStatus), default=ProjectStatus.CREATED
    )
    sql_input_dir: Mapped[str] = mapped_column(String(500), default="")
    output_dir: Mapped[str] = mapped_column(String(500), default="")
    file_count: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class MigrationStage(Base):
    __tablename__ = "migration_stages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    stage_name: Mapped[str] = mapped_column(String(50), nullable=False)
    stage_order: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[StageStatus] = mapped_column(
        Enum(StageStatus), default=StageStatus.PENDING
    )
    started_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


# ---------------------------------------------------------------------------
# Pydantic schemas (API request/response)
# ---------------------------------------------------------------------------

PIPELINE_STAGES = [
    ("parse", "Parse T-SQL Files"),
    ("analyze", "Analyze Call Graph"),
    ("plan", "Plan Migration Order"),
    ("extract_schema", "Extract Schema"),
    ("prepare", "Prepare Staging Tables"),
    ("migrate", "Migrate to Java"),
    ("generate", "Generate Output Files"),
]


class ProjectCreate(BaseModel):
    name: str
    description: str = ""


class ProjectResponse(BaseModel):
    id: int
    name: str
    description: str
    status: ProjectStatus
    file_count: int
    error_message: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class StageResponse(BaseModel):
    id: int
    project_id: int
    stage_name: str
    stage_order: int
    status: StageStatus
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    error_message: Optional[str] = None

    model_config = {"from_attributes": True}


class ProjectDetailResponse(BaseModel):
    project: ProjectResponse
    stages: list[StageResponse]
