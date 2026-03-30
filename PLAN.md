# T-SQL to Spring Boot 3 Migration Tool — Tech Stack & Architecture Plan

## Overview

An AI-powered agentic workflow tool (written in **Python**) that migrates T-SQL (SQL Server)
stored procedures to **Spring Boot 3 / Java 21** services.

---

## Tech Stack

| Layer | Technology | Version | Purpose |
|---|---|---|---|
| **Language** | Python | 3.12+ | Tool implementation language |
| **T-SQL Parsing** | sqlglot | latest | Parse T-SQL into AST with TSQL dialect support |
| **Orchestration** | LangGraph | 1.x (stable) | Deterministic stateful DAG for multi-step migration pipeline |
| **LLM Integration** | LangChain + langchain-anthropic | latest | ChatAnthropic for Claude API; swappable to OpenAI |
| **Chunking** | LangChain Text Splitters | latest | Split large (2000+ line) procedures for LLM context window limits |
| **Call Graph DB** | Neo4j Community Edition | 5.x | Store/query procedure call graphs, dependency analysis |
| **Neo4j Driver** | neo4j (Python) | 5.x | Cypher queries from Python |
| **Data Models** | Pydantic | 2.x | Type-safe state definitions for LangGraph nodes |
| **Output** | Spring Boot 3 / Java 21 | 3.4.x / 21 | Generated target code |

### Python Dependencies

```
sqlglot
langgraph
langchain
langchain-anthropic
langchain-openai        # for OpenAI portability
neo4j
pydantic
```

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                         LangGraph Pipeline                          │
│                   (Deterministic Stateful DAG)                      │
│                                                                     │
│  ┌───────────┐   ┌───────────┐   ┌───────────┐   ┌─────────────┐  │
│  │   PARSE   │──▶│  ANALYZE  │──▶│   PLAN    │──▶│   PREPARE   │  │
│  │  (sqlglot)│   │  (Neo4j)  │   │(top-sort) │   │(staging DDL)│  │
│  └───────────┘   └───────────┘   └───────────┘   └──────┬──────┘  │
│                                                          │         │
│                                                          ▼         │
│                                                   ┌─────────────┐  │
│                                                   │   MIGRATE   │  │
│                                                   │  (per proc) │  │
│                                                   └──────┬──────┘  │
│                                                          │         │
│                                          ┌───────────────┼───────┐ │
│                                          ▼               ▼       │ │
│                                   ┌────────────┐  ┌───────────┐  │ │
│                                   │ Small Proc │  │ Large Proc│  │ │
│                                   │ (1 LLM     │  │ (Chunked  │  │ │
│                                   │  call)     │  │  N LLM    │  │ │
│                                   │            │  │  calls)   │  │ │
│                                   └─────┬──────┘  └─────┬─────┘  │ │
│                                         │               │        │ │
│                                         └───────┬───────┘        │ │
│                                                 ▼                │ │
│                                          ┌────────────┐          │ │
│                                          │  GENERATE  │          │ │
│                                          │ (write Java│          │ │
│                                          │  + DDL)    │          │ │
│                                          └────────────┘          │ │
│                                                                   │ │
└─────────────────────────────────────────────────────────────────────┘
```

---

## LangGraph Pipeline Nodes

### Node 1: PARSE
- **Input**: Directory of `.sql` files
- **Tool**: `sqlglot` with TSQL dialect
- **Output**: List of `ProcedureMetadata` (parameters, temp tables, referenced tables, called procedures, logical blocks)
- **State update**: `procedures: dict[str, ProcedureMetadata]`

### Node 2: ANALYZE (Call Graph)
- **Input**: Parsed procedure metadata
- **Tool**: `neo4j` — create nodes and relationships
- **Neo4j Data Model**:
  - Nodes: `(:Procedure {name, schema, lineCount, paramCount})`
  - Nodes: `(:Table {name, schema})`
  - Nodes: `(:TempTable {name, stagingName})`
  - Relationships: `(Procedure)-[:CALLS]->(Procedure)`
  - Relationships: `(Procedure)-[:USES_TABLE {operation}]->(Table)`
  - Relationships: `(Procedure)-[:USES_TEMP_TABLE]->(TempTable)`
- **Cypher queries**:
  - Leaf detection: `MATCH (p:Procedure) WHERE NOT (p)-[:CALLS]->() RETURN p`
  - Cycle detection: `CALL apoc.nodes.cycles(nodes)`
  - Impact analysis: `MATCH (caller)-[:CALLS*]->(target) RETURN caller`
- **Output**: CallGraph stored in Neo4j
- **State update**: `call_graph: CallGraph`, `cycles: list`, `external_deps: list`

### Node 3: PLAN (Migration Order)
- **Input**: Call graph from Neo4j
- **Tool**: Neo4j GDS `gds.dag.topologicalSort` or custom Cypher
- **Logic**: Bottom-up — migrate leaf procedures first, then their callers
- **Output**: Ordered list of procedure names
- **State update**: `migration_order: list[str]`

### Node 4: PREPARE (Staging Tables)
- **Input**: Temp table metadata from all procedures
- **Logic**:
  - Each `#TempTable` → real `stg_` prefixed table with `batch_id` column
  - Generate DDL scripts
  - Generate cleanup service code
- **Output**: Staging table DDLs + cleanup service Java code
- **State update**: `staging_ddls: list[str]`, `temp_table_mapping: dict`

### Node 5: MIGRATE (Per Procedure — Conditional Edge)
- **Input**: One procedure at a time (in migration order)
- **Conditional routing**:
  - **Small procedure** (< threshold lines) → single LLM call
  - **Large procedure** (≥ threshold lines) → chunked LLM calls
- **LLM prompt includes**:
  - Procedure SQL
  - Temp table → staging table mapping
  - Already-migrated dependency service names (from prior iterations)
- **State update**: `migrated: dict[str, MigratedProcedure]`

### Node 5a: CHUNK (Large Procedure Sub-flow)
- **Tool**: LangChain `RecursiveCharacterTextSplitter` with SQL-aware separators
- **Separators** (in order of priority):
  ```
  "\nBEGIN TRY", "\nBEGIN CATCH", "\nBEGIN TRANSACTION",
  "\nDECLARE ", "\nCREATE TABLE #",
  "\nWHILE ", "\nIF ",
  "\nINSERT ", "\nUPDATE ", "\nDELETE ", "\nMERGE ",
  "\nEXEC ", "\nSELECT ",
  ";\n", "\nEND", "\n\n", "\n"
  ```
- **Each chunk carries**:
  - Variable context (inputs/outputs between chunks)
  - Temp table mappings
  - Previously generated Java code from prior chunks
- **Sequential LLM calls**: Chunk N output feeds into Chunk N+1 context
- **Final step**: Compose all chunk outputs into single service class

### Node 6: GENERATE (Write Output)
- **Input**: All migrated procedure code + staging DDLs
- **Output files**:
  ```
  output/
  ├── services/           # Spring Boot @Service classes
  ├── repositories/       # JPA repositories or JdbcTemplate DAOs
  ├── dto/                # DTOs and records
  ├── ddl/
  │   └── staging_tables.sql
  ├── cleanup/
  │   ├── StagingTableCleanupService.java
  │   └── BatchIdGenerator.java
  └── migration_report.json
  ```

---

## LangGraph State Schema

```python
from pydantic import BaseModel
from typing import Optional

class MigrationState(BaseModel):
    # Input
    sql_input_dir: str
    config: MigrationConfig

    # After PARSE
    procedures: dict[str, ProcedureMetadata] = {}

    # After ANALYZE
    call_graph_built: bool = False
    cycles: list[list[str]] = []
    external_dependencies: list[str] = []

    # After PLAN
    migration_order: list[str] = []

    # After PREPARE
    staging_ddls: list[str] = []
    temp_table_mapping: dict[str, str] = {}  # #TempName -> stg_name

    # After MIGRATE (accumulates per procedure)
    migrated_procedures: dict[str, MigratedProcedure] = {}
    current_procedure: Optional[str] = None
    failed_procedures: dict[str, str] = {}  # name -> error

    # After GENERATE
    output_files: list[str] = []
```

---

## Key Design Decisions

### 1. Temp Tables → Staging Tables (Database, NOT In-Memory)
- Each `#TempTable` becomes a real database table with `stg_` prefix
- `batch_id` column (UUID) isolates concurrent executions
- Cleanup in `finally` block via `StagingTableCleanupService.cleanupBatch(batchId)`
- Scheduled hourly cleanup as safety net for orphaned data

### 2. Call Graph → Neo4j (Bottom-Up Migration Order)
- Stored in Neo4j for rich querying (impact analysis, cycle detection)
- Topological sort determines migration order — leaf procedures first
- Circular dependencies detected and reported as warnings
- External dependencies (called but not in source) tracked separately

### 3. Large Procedure Chunking (LLM Context Window)
- Procedures > 2000 lines are split using SQL-aware text splitters
- Chunks are processed sequentially — each chunk receives prior output as context
- Enables **LLM portability** — works with both Claude (200K) and OpenAI (128K/32K)
- Chunk size configurable per LLM model

### 4. LLM Portability (Claude ↔ OpenAI)
- LangChain abstracts the LLM provider
- `langchain-anthropic` for Claude, `langchain-openai` for GPT
- Same prompts, same pipeline — just swap the LLM config
- Chunk size auto-adjusts based on selected model's context window

### 5. LangGraph Checkpointing
- State persisted at each node via SQLite checkpointer (dev) or Postgres (prod)
- Resume from any failed step without re-processing
- Enables human-in-the-loop review before GENERATE step

---

## T-SQL to Java Mapping Reference

| T-SQL Construct | Spring Boot 3 / Java 21 Equivalent |
|---|---|
| `CREATE PROCEDURE` | `@Service` class with method |
| `@Param INT` | `int param` method parameter |
| `@Param OUTPUT` | Return as record/DTO field |
| `#TempTable` | Staging table with `batch_id` + cleanup |
| `EXEC sp_Name` | `injectedService.methodName()` |
| `CURSOR...FETCH` | `jdbcTemplate.query()` + stream/iteration |
| `BEGIN TRY...CATCH` | `try { } catch (Exception e) { }` |
| `BEGIN TRAN...COMMIT` | `@Transactional` |
| `RAISERROR` | `throw new RuntimeException()` / SLF4J log |
| `SCOPE_IDENTITY()` | JPA `save()` return / `GeneratedKeyHolder` |
| `GETDATE()` | `LocalDateTime.now()` |
| `ISNULL(a, b)` | `Objects.requireNonNullElse(a, b)` |
| `@@ROWCOUNT` | `jdbcTemplate.update()` return value |
| `@@FETCH_STATUS` | Iterator `hasNext()` |

---

## Configuration

```yaml
migration:
  sql_input_dir: "sql-input/procedures"
  output_dir: "output"

  # LLM
  llm_provider: "anthropic"         # or "openai"
  llm_model: "claude-sonnet-4-6"    # or "gpt-4o"
  llm_api_key_env: "ANTHROPIC_API_KEY"

  # Chunking
  large_procedure_threshold: 2000   # lines
  chunk_size_tokens: 8000           # per LLM call
  chunk_overlap_tokens: 500         # overlap between chunks

  # Neo4j
  neo4j_uri: "bolt://localhost:7687"
  neo4j_user: "neo4j"
  neo4j_password_env: "NEO4J_PASSWORD"

  # Staging tables
  staging_table_prefix: "stg_"

  # Output
  base_package: "com.migration.generated"
  use_jdbc_template: false          # false = JPA, true = JdbcTemplate
  generate_tests: true

  # LangGraph
  checkpointer: "sqlite"           # or "postgres"
  checkpoint_db: ".migration_checkpoints.db"
```

---

## Project Structure (Python Tool)

```
pl-sql-migration/
├── pyproject.toml
├── README.md
├── config.yaml
├── sql-input/
│   └── procedures/          # Place .sql files here
├── output/                  # Generated Spring Boot code
├── src/
│   └── tsql_migration/
│       ├── __init__.py
│       ├── main.py                  # CLI entry point
│       ├── config.py                # Configuration loading
│       ├── state.py                 # Pydantic state models
│       ├── parser/
│       │   ├── __init__.py
│       │   └── tsql_parser.py       # sqlglot-based T-SQL parser
│       ├── graph/
│       │   ├── __init__.py
│       │   └── neo4j_client.py      # Neo4j operations
│       ├── chunker/
│       │   ├── __init__.py
│       │   └── sql_chunker.py       # SQL-aware text splitting
│       ├── pipeline/
│       │   ├── __init__.py
│       │   ├── graph.py             # LangGraph DAG definition
│       │   ├── nodes/
│       │   │   ├── parse_node.py
│       │   │   ├── analyze_node.py
│       │   │   ├── plan_node.py
│       │   │   ├── prepare_node.py
│       │   │   ├── migrate_node.py
│       │   │   └── generate_node.py
│       │   └── prompts/
│       │       ├── system_prompt.py
│       │       └── chunk_prompt.py
│       └── output/
│           ├── __init__.py
│           └── java_writer.py       # Write generated Java files
└── tests/
    ├── test_parser.py
    ├── test_chunker.py
    ├── test_graph.py
    └── test_pipeline.py
```

---

## Next Steps

1. [ ] Set up Python project with `pyproject.toml` and dependencies
2. [ ] Implement `tsql_parser.py` using sqlglot
3. [ ] Implement `neo4j_client.py` with data model and Cypher queries
4. [ ] Implement `sql_chunker.py` with SQL-aware text splitting
5. [ ] Define LangGraph pipeline in `graph.py` with all nodes
6. [ ] Implement each pipeline node
7. [ ] Add sample T-SQL procedures for testing
8. [ ] End-to-end test with sample procedures
