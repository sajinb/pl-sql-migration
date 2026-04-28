"""Configuration loading from YAML file."""

from pathlib import Path

import yaml
from pydantic import BaseModel, Field


class SourceDbConfig(BaseModel):
    enabled: bool = False
    connection_string_env: str = "SOURCE_DB_CONNECTION_STRING"


class MigrationConfig(BaseModel):
    sql_input_dir: str = "sql-input/procedures"
    ddl_input_dir: str = "sql-input/ddl"
    output_dir: str = "output"

    # LLM
    llm_provider: str = "anthropic"
    llm_model: str = "claude-sonnet-4-6"
    llm_api_key_env: str = "ANTHROPIC_API_KEY"

    # Chunking
    large_procedure_threshold: int = 2000
    chunk_size_tokens: int = 8000
    chunk_overlap_tokens: int = 500

    # Neo4j
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password_env: str = "NEO4J_PASSWORD"
    neo4j_database: str = ""  # dedicated database name (Enterprise/Aura); leave empty for Community
    neo4j_label_prefix: str = "Mig_"  # prefix for all node labels to avoid collisions with existing data

    # Source SQL Server
    source_db: SourceDbConfig = Field(default_factory=SourceDbConfig)

    # Staging tables
    staging_table_prefix: str = "stg_"

    # Output
    base_package: str = "com.migration.generated"
    use_jdbc_template: bool = False
    generate_tests: bool = True

    # Validation
    validate_llm_review: bool = False  # send generated code back to LLM for review (slower, uses tokens)

    # LangGraph
    checkpointer: str = "sqlite"
    checkpoint_db: str = ".migration_checkpoints.db"


def load_config(config_path: str = "config.yaml") -> MigrationConfig:
    """Load migration config from YAML file."""
    path = Path(config_path)
    if not path.exists():
        return MigrationConfig()

    with open(path) as f:
        raw = yaml.safe_load(f)

    migration_section = raw.get("migration", raw)
    return MigrationConfig(**migration_section)
