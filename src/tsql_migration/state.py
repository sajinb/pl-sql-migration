"""Pydantic state models for LangGraph pipeline."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

from tsql_migration.config import MigrationConfig


# ---------------------------------------------------------------------------
# Procedure metadata (output of PARSE node)
# ---------------------------------------------------------------------------

class ParameterMetadata(BaseModel):
    name: str
    sql_type: str
    direction: str = "IN"  # IN, OUT, INOUT
    default_value: Optional[str] = None


class TempTableColumn(BaseModel):
    name: str
    sql_type: str
    nullable: bool = True


class TempTableUsage(BaseModel):
    original_name: str       # e.g. #TempOrders
    staging_table_name: str  # e.g. stg_temp_orders
    columns: list[TempTableColumn] = []
    create_statement: str = ""
    is_global_temp: bool = False


class SqlBlock(BaseModel):
    block_id: str
    block_type: str  # DECLARATION, TEMP_TABLE_CREATE, CURSOR_LOOP, IF_ELSE, etc.
    content: str
    start_line: int
    end_line: int
    input_variables: list[str] = []
    output_variables: list[str] = []


class ProcedureMetadata(BaseModel):
    procedure_name: str
    schema_name: str = "dbo"
    parameters: list[ParameterMetadata] = []
    temp_tables: list[TempTableUsage] = []
    called_procedures: list[str] = []
    referenced_tables: list[str] = []
    logical_blocks: list[SqlBlock] = []
    raw_sql: str = ""
    total_line_count: int = 0
    source_file: str = ""


# ---------------------------------------------------------------------------
# Edge case models (output of ANALYZE node)
# ---------------------------------------------------------------------------

class LinkedServerRef(BaseModel):
    procedure: str
    server: str
    database: str
    remote_object: str
    operation: str       # SELECT, EXEC, INSERT, etc.
    line: int = 0


class DynamicSqlRef(BaseModel):
    procedure: str
    tier: str            # simple, templated, concatenated, opaque
    expression: str
    line: int = 0
    resolved_sql: Optional[str] = None


class VariableCallRef(BaseModel):
    procedure: str
    variable: str
    certainty: str       # resolved, conditional, dispatch_table, opaque
    resolved_targets: list[str] = []
    condition: Optional[str] = None
    line: int = 0


class EdgeCaseReport(BaseModel):
    linked_servers: list[LinkedServerRef] = []
    dynamic_sql_calls: list[DynamicSqlRef] = []
    variable_calls: list[VariableCallRef] = []
    unresolved_count: int = 0


# ---------------------------------------------------------------------------
# Schema / Entity models (output of EXTRACT SCHEMA node)
# ---------------------------------------------------------------------------

class ForeignKeyMetadata(BaseModel):
    column: str
    referenced_table: str
    referenced_column: str


class ColumnMetadata(BaseModel):
    name: str
    sql_type: str
    java_type: str = ""
    nullable: bool = True
    is_primary_key: bool = False
    is_auto_increment: bool = False
    max_length: Optional[int] = None


class EntityMetadata(BaseModel):
    table_name: str
    schema_name: str = "dbo"
    entity_class_name: str = ""
    columns: list[ColumnMetadata] = []
    primary_key: list[str] = []
    foreign_keys: list[ForeignKeyMetadata] = []


# ---------------------------------------------------------------------------
# Validation models (output of VALIDATE node)
# ---------------------------------------------------------------------------

class ValidationIssue(BaseModel):
    severity: str   # "error" | "warning" | "info"
    code: str       # e.g. "MISSING_SERVICE_ANNOTATION", "UNBALANCED_BRACES"
    message: str


class ValidationResult(BaseModel):
    procedure_name: str
    service_name: str
    passed: bool          # True if no errors (warnings are allowed)
    issues: list[ValidationIssue] = []
    todo_count: int = 0   # number of // TODO markers left by the LLM
    llm_review: str = ""  # raw LLM review text when validate_llm_review is enabled


# ---------------------------------------------------------------------------
# Migrated procedure (output of MIGRATE node)
# ---------------------------------------------------------------------------

class MigratedProcedure(BaseModel):
    procedure_name: str
    service_name: str
    method_name: str
    service_code: str = ""
    repository_code: str = ""
    dto_code: str = ""
    staging_table_ddls: list[str] = []


# ---------------------------------------------------------------------------
# Main LangGraph state
# ---------------------------------------------------------------------------

class MigrationState(BaseModel):
    """Root state object that flows through all LangGraph nodes."""

    # Input
    sql_input_dir: str = ""
    config: MigrationConfig = Field(default_factory=MigrationConfig)

    # After PARSE
    procedures: dict[str, ProcedureMetadata] = {}

    # After ANALYZE
    call_graph_built: bool = False
    cycles: list[list[str]] = []
    external_dependencies: list[str] = []
    edge_case_report: EdgeCaseReport = Field(default_factory=EdgeCaseReport)

    # After PLAN
    migration_order: list[str] = []

    # After EXTRACT SCHEMA
    entities: dict[str, EntityMetadata] = {}
    repositories: dict[str, str] = {}
    schema_source: str = "none"  # "live_db" | "ddl_scripts" | "none"

    # After PREPARE
    staging_ddls: list[str] = []
    temp_table_mapping: dict[str, str] = {}

    # After MIGRATE
    migrated_procedures: dict[str, MigratedProcedure] = {}
    current_procedure: Optional[str] = None
    failed_procedures: dict[str, str] = {}

    # After VALIDATE
    validation_results: dict[str, ValidationResult] = {}

    # After GENERATE
    output_files: list[str] = []
