# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

### Backend (Python)

```bash
# Install (editable)
pip install -e .
pip install -e ".[dev]"       # includes pytest
pip install -e ".[db]"        # adds pyodbc for live SQL Server schema extraction

# Run CLI migration
tsql-migrate sql-input/procedures
tsql-migrate sql-input/procedures --output output --provider anthropic --model claude-sonnet-4-6 --checkpoint

# Run API server (port 8001)
tsql-migrate-server
# or directly:
uvicorn tsql_migration.api.app:app --host 0.0.0.0 --port 8001 --reload

# Run tests
pytest
pytest tests/test_specific_file.py::test_function_name
```

### Frontend (React + Vite, port 5174)

```bash
cd frontend
npm install
npm run dev       # starts on port 5174
npm run build     # TypeScript compile + Vite build
npm run lint      # ESLint
```

### Required environment variables

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
export NEO4J_PASSWORD="your-password"
# Optional, for live SQL Server schema extraction:
export SOURCE_DB_CONNECTION_STRING="DRIVER={ODBC Driver 18 for SQL Server};..."
```

Neo4j must be running at `bolt://localhost:7687` before starting a migration.

## Architecture

### Two entry points

1. **CLI** (`tsql-migrate`): `src/tsql_migration/main.py` — runs the pipeline directly in the terminal.
2. **API + UI**: `tsql-migrate-server` starts the FastAPI backend (`src/tsql_migration/api/`) on port 8001; the React frontend (`frontend/`) talks to it.

### LangGraph pipeline (`src/tsql_migration/pipeline/`)

A linear 7-node DAG defined in `pipeline/graph.py`. The shared state object (`MigrationState` in `state.py`) flows through all nodes:

```
PARSE → ANALYZE → PLAN → EXTRACT_SCHEMA → PREPARE → MIGRATE → GENERATE
```

Each node is a standalone function in `pipeline/nodes/`. Nodes read from and write to `MigrationState` fields — the mapping is documented in `state.py` via comments (`# After PARSE`, `# After ANALYZE`, etc.).

### Key state fields

| After node | Key fields |
|---|---|
| PARSE | `procedures: dict[str, ProcedureMetadata]` |
| ANALYZE | `call_graph_built`, `edge_case_report` |
| PLAN | `migration_order: list[str]` (topological sort) |
| EXTRACT_SCHEMA | `entities`, `repositories`, `schema_source` |
| PREPARE | `staging_ddls`, `temp_table_mapping` |
| MIGRATE | `migrated_procedures: dict[str, MigratedProcedure]`, `failed_procedures` |
| GENERATE | `output_files: list[str]` |

### Neo4j isolation (`neo4j_label_prefix`)

All Neo4j node labels are prefixed with `Mig_` (configurable) to avoid colliding with existing data in shared Community Edition instances. The `Neo4jClient` in `graph/neo4j_client.py` applies this prefix on every Cypher query. Do not hardcode label names like `Procedure` — always use the client methods.

### API layer (`src/tsql_migration/api/`)

- `app.py` — FastAPI app with CORS configured to allow any `localhost:<port>`
- `database.py` — SQLAlchemy async setup (SQLite by default) for persisting project/run state
- `routes/projects.py`, `routes/migration.py` — REST endpoints
- `runner.py` — runs the LangGraph pipeline in a background thread, streaming logs via `log_streamer.py`
- `models.py` — SQLAlchemy ORM models for API persistence

### Large procedure chunking (`src/tsql_migration/chunker/sql_chunker.py`)

Procedures over `large_procedure_threshold` lines (default 2000) are split into chunks using SQL-aware separators. Each chunk is sent to the LLM as a separate call, carrying forward variable context and previously generated code. This is how LLM context limits are handled without truncating SQL.

### Config (`config.yaml` / `src/tsql_migration/config.py`)

All pipeline behavior is controlled via `config.yaml`. CLI flags override YAML values. `MigrationConfig` (Pydantic) is the typed representation. LLM provider, model, Neo4j connection, staging table prefix, and base Java package are all in config — do not hardcode them.

### T-SQL → Java conventions

- `#TempTable` → staging table `stg_<name>` with `batch_id UUID` column (not in-memory)
- `EXEC sp_Name` → injected `@Service` bean call, service name resolved from `migrated_procedures`
- Procedures are migrated **bottom-up** (leaf dependencies first) so service names are known before callers are processed
- LLM prompts are in `pipeline/prompts/system_prompt.py` and `pipeline/prompts/chunk_prompt.py`

### Sample SQL

`sql-input/procedures/` has 5 reference procedures covering: simple selects, OUTPUT params, temp tables, TRY/CATCH, cursors, linked servers, and dynamic SQL. `sql-input/ddl/tables.sql` has 11 table DDLs used for schema extraction testing.
