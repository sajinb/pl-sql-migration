# T-SQL to Spring Boot 3 Migration Tool — Tech Stack & Architecture Plan

## Overview

An AI-powered agentic workflow tool (written in **Python**) that migrates T-SQL (SQL Server)
stored procedures to **Spring Boot 3 / Java 21** services.

### Migration Scope

**What IS migrated**: Procedural logic (stored procedures) → Java `@Service` classes, `@Entity` classes, `JpaRepository` interfaces, DTOs.

**What is NOT migrated**: The database itself. Tables stay in SQL Server. Spring Boot connects to the same SQL Server instance.

```
BEFORE:  Application → SQL Server (tables + stored procedures)
AFTER:   Application → Spring Boot 3 (Java services) → SQL Server (same tables, no DDL changes)
```

| Artifact | Migrated? | Details |
|---|---|---|
| **Tables** | No — stay in SQL Server | No DDL changes to existing tables |
| **Stored Procedures** | Yes → Java `@Service` classes | Business logic moves to Java |
| **Temp Tables** | Yes → new `stg_` staging tables | Created in same SQL Server DB |
| **Table access from procedures** | Yes → JPA `@Entity` + `JpaRepository` | Entity classes generated for all referenced tables |

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
pyodbc                  # optional: live SQL Server schema extraction
```

---

## Architecture

```
┌──────────────────────────────────────────────────────────────────────────┐
│                          LangGraph Pipeline                              │
│                    (Deterministic Stateful DAG)                          │
│                                                                          │
│  ┌─────────┐  ┌─────────┐  ┌─────────┐  ┌──────────┐  ┌────────────┐  │
│  │  PARSE  │─▶│ ANALYZE │─▶│  PLAN   │─▶│ EXTRACT  │─▶│  PREPARE   │  │
│  │(sqlglot)│  │ (Neo4j) │  │(topo-   │  │ SCHEMA   │  │(staging    │  │
│  │         │  │         │  │ sort)   │  │(entities)│  │ DDL)       │  │
│  └─────────┘  └─────────┘  └─────────┘  └──────────┘  └─────┬──────┘  │
│                                                              │         │
│                                                              ▼         │
│                                                    ┌──────────────┐    │
│                                                    │   MIGRATE    │    │
│                                                    │ (per proc)   │    │
│                                                    └──────┬───────┘    │
│                                                           │            │
│                                           ┌───────────────┼──────┐     │
│                                           ▼               ▼      │     │
│                                    ┌────────────┐  ┌───────────┐ │     │
│                                    │ Small Proc │  │ Large Proc│ │     │
│                                    │ (1 LLM     │  │ (Chunked  │ │     │
│                                    │  call)     │  │  N LLM    │ │     │
│                                    │            │  │  calls)   │ │     │
│                                    └─────┬──────┘  └─────┬─────┘ │     │
│                                          │               │       │     │
│                                          └───────┬───────┘       │     │
│                                                  ▼               │     │
│                                           ┌────────────┐         │     │
│                                           │  GENERATE  │         │     │
│                                           │(Java files,│         │     │
│                                           │ entities,  │         │     │
│                                           │ DDL, tests)│         │     │
│                                           └────────────┘         │     │
│                                                                        │
└──────────────────────────────────────────────────────────────────────────┘
```

---

## LangGraph Pipeline Nodes

### Node 1: PARSE
- **Input**: Directory of `.sql` files
- **Tool**: `sqlglot` with TSQL dialect
- **Output**: List of `ProcedureMetadata` (parameters, temp tables, referenced tables, called procedures, logical blocks)
- **State update**: `procedures: dict[str, ProcedureMetadata]`

### Node 2: ANALYZE (Call Graph + Edge Cases)
- **Input**: Parsed procedure metadata
- **Tool**: `neo4j` — create nodes and relationships
- **Neo4j Data Model**:

  **Core Nodes**:
  - `(:Procedure {name, schema, lineCount, paramCount})`
  - `(:Table {name, schema})`
  - `(:TempTable {name, stagingName})`

  **Edge Case Nodes** (for tough scenarios):
  - `(:LinkedServer {name, database})` — 4-part name references
  - `(:DynamicCall {expression, tier, line})` — `EXEC(@sql)` / `sp_executesql`
  - `(:DispatchTable {name, column})` — table-driven procedure dispatch
  - `(:UnresolvedCall {variable, line, context})` — fully opaque variable calls

  **Core Relationships**:
  - `(Procedure)-[:CALLS {certainty: "resolved"}]->(Procedure)`
  - `(Procedure)-[:USES_TABLE {operation: "SELECT|INSERT|UPDATE|DELETE"}]->(Table)`
  - `(Procedure)-[:USES_TEMP_TABLE]->(TempTable)`

  **Edge Case Relationships**:
  - `(Procedure)-[:CALLS {type: "variable", certainty: "conditional", condition: "..."}]->(Procedure)` — resolved variable calls
  - `(Procedure)-[:CALLS_REMOTE {operation}]->(LinkedServer)` — linked server access
  - `(Procedure)-[:READS_REMOTE {operation}]->(LinkedServer)` — linked server reads
  - `(Procedure)-[:USES_DYNAMIC_SQL]->(DynamicCall)` — dynamic SQL usage
  - `(Procedure)-[:DYNAMIC_DISPATCH]->(DispatchTable)` — table-driven dispatch
  - `(Procedure)-[:UNRESOLVED_CALL]->(UnresolvedCall)` — opaque calls

- **Cypher queries**:
  - Leaf detection: `MATCH (p:Procedure) WHERE NOT (p)-[:CALLS]->() RETURN p`
  - Cycle detection: `CALL apoc.nodes.cycles(nodes)`
  - Impact analysis: `MATCH (caller)-[:CALLS*]->(target) RETURN caller`
  - Linked server report: `MATCH (p)-[:CALLS_REMOTE|READS_REMOTE]->(ls) RETURN p, ls`
  - Unresolved calls report: `MATCH (p)-[:UNRESOLVED_CALL|DYNAMIC_DISPATCH]->(u) RETURN p, u`
- **Output**: CallGraph stored in Neo4j
- **State update**: `call_graph_built: bool`, `cycles: list`, `external_deps: list`, `edge_case_report: EdgeCaseReport`

### Node 3: PLAN (Migration Order)
- **Input**: Call graph from Neo4j
- **Tool**: Neo4j GDS `gds.dag.topologicalSort` or custom Cypher
- **Logic**: Bottom-up — migrate leaf procedures first, then their callers
- **Output**: Ordered list of procedure names
- **State update**: `migration_order: list[str]`

### Node 4: EXTRACT SCHEMA (Entity + Repository Generation)
- **Input**: List of all referenced tables from parsed procedures
- **Schema Source** (two modes):
  - **Live DB** (preferred): Connect to SQL Server via `pyodbc`, query `INFORMATION_SCHEMA.COLUMNS`,
    `INFORMATION_SCHEMA.TABLE_CONSTRAINTS`, `INFORMATION_SCHEMA.KEY_COLUMN_USAGE`
  - **DDL scripts** (offline): Parse `CREATE TABLE` statements from provided `.sql` files via `sqlglot`
- **Output per table**:
  - JPA `@Entity` class with `@Table`, `@Column`, `@Id`, `@GeneratedValue`, relationships (`@ManyToOne`, `@OneToMany`)
  - `JpaRepository<EntityClass, IdType>` interface
  - Column type mapping: `INT→Integer`, `VARCHAR→String`, `DATETIME→LocalDateTime`, `DECIMAL→BigDecimal`,
    `BIT→Boolean`, `UNIQUEIDENTIFIER→UUID`, `MONEY→BigDecimal`, etc.
- **State update**: `entities: dict[str, EntityMetadata]`, `repositories: dict[str, str]`

### Node 5: PREPARE (Staging Tables)
- **Input**: Temp table metadata from all procedures
- **Logic**:
  - Each `#TempTable` → real `stg_` prefixed table with `batch_id` column
  - Generate DDL scripts
  - Generate cleanup service code
- **Output**: Staging table DDLs + cleanup service Java code
- **State update**: `staging_ddls: list[str]`, `temp_table_mapping: dict`

### Node 6: MIGRATE (Per Procedure — Conditional Edge)
- **Input**: One procedure at a time (in migration order)
- **Conditional routing**:
  - **Small procedure** (< threshold lines) → single LLM call
  - **Large procedure** (≥ threshold lines) → chunked LLM calls
- **LLM prompt includes**:
  - Procedure SQL
  - Temp table → staging table mapping
  - Entity class signatures (from EXTRACT SCHEMA) for referenced tables
  - Already-migrated dependency service names (from prior iterations)
  - Edge case context (linked servers, dynamic SQL, variable calls) with classification
- **State update**: `migrated: dict[str, MigratedProcedure]`

### Node 6a: CHUNK (Large Procedure Sub-flow)
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
  - Entity class signatures for referenced tables
  - Previously generated Java code from prior chunks
- **Sequential LLM calls**: Chunk N output feeds into Chunk N+1 context
- **Final step**: Compose all chunk outputs into single service class

### Node 7: GENERATE (Write Output)
- **Input**: All migrated procedure code + entities + staging DDLs
- **Output files**:
  ```
  output/
  ├── entity/             # JPA @Entity classes (one per referenced table)
  ├── repository/         # JpaRepository interfaces
  ├── service/            # Spring Boot @Service classes (migrated procedures)
  ├── dto/                # DTOs and Java records
  ├── ddl/
  │   └── staging_tables.sql
  ├── cleanup/
  │   ├── StagingTableCleanupService.java
  │   └── BatchIdGenerator.java
  ├── migration_report.json
  └── edge_case_report.json   # Linked servers, dynamic SQL, unresolved calls
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
    edge_case_report: EdgeCaseReport = EdgeCaseReport()  # linked servers, dynamic SQL, variable calls

    # After PLAN
    migration_order: list[str] = []

    # After EXTRACT SCHEMA
    entities: dict[str, EntityMetadata] = {}       # table_name -> entity metadata
    repositories: dict[str, str] = {}              # table_name -> repository code
    schema_source: str = "none"                    # "live_db" | "ddl_scripts" | "none"

    # After PREPARE
    staging_ddls: list[str] = []
    temp_table_mapping: dict[str, str] = {}  # #TempName -> stg_name

    # After MIGRATE (accumulates per procedure)
    migrated_procedures: dict[str, MigratedProcedure] = {}
    current_procedure: Optional[str] = None
    failed_procedures: dict[str, str] = {}  # name -> error

    # After GENERATE
    output_files: list[str] = []


class EdgeCaseReport(BaseModel):
    linked_servers: list[LinkedServerRef] = []       # 4-part name references
    dynamic_sql_calls: list[DynamicSqlRef] = []      # EXEC(@sql) / sp_executesql
    variable_calls: list[VariableCallRef] = []       # EXEC @procName
    unresolved_count: int = 0                        # total items needing human review


class LinkedServerRef(BaseModel):
    procedure: str
    server: str
    database: str
    remote_object: str
    operation: str       # SELECT, EXEC, INSERT, etc.
    line: int


class DynamicSqlRef(BaseModel):
    procedure: str
    tier: str            # simple, templated, concatenated, opaque
    expression: str
    line: int
    resolved_sql: Optional[str] = None   # for simple/templated tiers


class VariableCallRef(BaseModel):
    procedure: str
    variable: str
    certainty: str       # resolved, conditional, dispatch_table, opaque
    resolved_targets: list[str] = []     # procedure names if resolvable
    condition: Optional[str] = None      # IF condition for conditional calls
    line: int


class EntityMetadata(BaseModel):
    table_name: str
    schema_name: str
    entity_class_name: str
    columns: list[ColumnMetadata]
    primary_key: list[str]
    foreign_keys: list[ForeignKeyMetadata] = []


class ColumnMetadata(BaseModel):
    name: str
    sql_type: str
    java_type: str
    nullable: bool
    is_primary_key: bool
    is_auto_increment: bool = False
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

### 6. Schema Extraction (Entity Generation)
- Every table referenced in SELECT/INSERT/UPDATE/DELETE across all procedures gets a JPA `@Entity` class
- Two modes: **live DB connection** (`pyodbc` → `INFORMATION_SCHEMA`) or **DDL script parsing** (`sqlglot`)
- Live DB is preferred (full column types, constraints, PKs, FKs) — DDL scripts as offline fallback

---

## Handling the 3 Toughest Scenarios

### Scenario 1: Linked Servers (4-Part Names)

```sql
-- Example
SELECT * FROM [ServerA].[RemoteDB].[dbo].[Customers]
EXEC [ServerA].[RemoteDB].[dbo].[sp_GetData] @Id
```

**Problem**: No direct Spring Boot equivalent for cross-server queries.

**Detection**: `sqlglot` parses multi-part names into `Table`/`Column` expressions with `catalog` and `db` properties. When these exceed 2 parts → linked server reference.

**Strategy**: Detect, catalog in Neo4j as `(:LinkedServer)`, flag for **human decision**.

| Migration Option | When to Use |
|---|---|
| Replace with REST/gRPC call to remote service | Remote DB is also being modernized |
| Replace with a second `DataSource` bean | Still need direct DB access to remote server |
| Keep as raw JDBC with 4-part name | SQL Server remains the runtime DB (lift-and-shift) |
| Flag as `// TODO: LINKED_SERVER` | Decision needs human input |

**Neo4j**:
```cypher
CREATE (ls:LinkedServer {name: "ServerA", database: "RemoteDB"})
CREATE (p)-[:CALLS_REMOTE {operation: "EXEC", line: 42}]->(ls)
CREATE (p)-[:READS_REMOTE {operation: "SELECT", line: 15}]->(ls)
```

### Scenario 2: Dynamic SQL (`EXEC(@sql)` / `sp_executesql`)

```sql
-- Tier 1: Simple (resolvable)
EXEC('SELECT * FROM Orders WHERE Status = ''Active''')

-- Tier 2: Templated (mostly resolvable)
EXEC sp_executesql N'SELECT * FROM Orders WHERE Id = @id', N'@id INT', @id = @orderId

-- Tier 3: Concatenated (partially resolvable)
SET @sql = 'SELECT * FROM ' + @tableName + ' WHERE Status = ''' + @status + ''''
EXEC(@sql)

-- Tier 4: Opaque (not resolvable statically)
EXEC(@sql)  -- @sql comes from parameter or table lookup
```

**Detection**: In the `sqlglot` AST, `execute_statement` with an expression argument (not a procedure name) indicates dynamic SQL.

**Strategy**: Multi-tier classification and handling.

| Tier | Pattern | Automation Level | Approach |
|---|---|---|---|
| **Simple** | `EXEC('literal SQL')` | Fully automated | Parse literal string with `sqlglot` → `JdbcTemplate` |
| **Templated** | `sp_executesql @template, @params` | Mostly automated | Parse template string → parameterized `JdbcTemplate` |
| **Concatenated** | `@sql = 'SELECT ' + @col + ...` | Partially automated | Backward trace variable assignments, reconstruct possible values → query builder skeleton |
| **Opaque** | `EXEC(@sql)` (unknown source) | Detection only | Flag as `// TODO: DYNAMIC_SQL` with context |

**LLM prompt guidance per tier**:
- **Simple/Templated** → convert to `JdbcTemplate` with parameterized queries (also eliminates SQL injection)
- **Concatenated** → convert to Criteria API or `JdbcTemplate` with query builder pattern
- **Opaque** → generate `// TODO: DYNAMIC_SQL` skeleton with original SQL and context

**Neo4j**:
```cypher
CREATE (dc:DynamicCall {expression: "@sql", tier: "concatenated", line: 27})
CREATE (p)-[:USES_DYNAMIC_SQL]->(dc)
```

### Scenario 3: Calls via Variables (`EXEC @procName`)

```sql
-- Pattern A: Literal assignment (resolvable)
SET @procName = 'sp_ProcessOrder'
EXEC @procName @OrderId

-- Pattern B: Conditional assignment (partially resolvable)
IF @type = 1
    SET @procName = 'sp_ProcessOrder'
ELSE
    SET @procName = 'sp_CancelOrder'
EXEC @procName @OrderId

-- Pattern C: Table-driven dispatch (query DB to resolve)
SELECT @procName = ProcedureName FROM DispatchTable WHERE ActionType = @action
EXEC @procName @param1

-- Pattern D: Parameter (not resolvable)
CREATE PROCEDURE sp_Router @procToCall VARCHAR(100) AS
EXEC @procToCall
```

**Detection**: In the `sqlglot` AST, `EXEC` with a `LOCAL_ID` node (variable) instead of a procedure name.

**Strategy**: Backward variable tracing + Neo4j uncertain edges.

| Pattern | Resolvable? | Approach |
|---|---|---|
| Single literal `SET @var = 'literal'` | Yes | Trace assignment → resolve → add `CALLS` edge |
| Conditional `IF...SET...ELSE SET` | Partially | Add `CALLS` edge to each target with `{certainty: "conditional"}` |
| Table lookup `SELECT @var = col FROM tbl` | Optional | Query live DB dispatch table for all possible values |
| Parameter | No | Flag as `(:UnresolvedCall)` for human review |

**Backward tracing algorithm**:
1. Find `EXEC @variable` in AST
2. Walk backward through preceding statements
3. Collect all `SET @variable = ...` and `SELECT @variable = ...` assignments
4. Classify based on assignment pattern
5. For resolvable patterns, add procedure call edges to Neo4j

**Neo4j**:
```cypher
-- Resolved variable call
(A)-[:CALLS {type: "variable", certainty: "resolved"}]->(B)

-- Conditional variable call
(A)-[:CALLS {type: "variable", certainty: "conditional", condition: "IF @type = 1"}]->(B)
(A)-[:CALLS {type: "variable", certainty: "conditional", condition: "ELSE"}]->(C)

-- Table-driven dispatch
(A)-[:DYNAMIC_DISPATCH {table: "DispatchTable", column: "ProcedureName"}]->(:DispatchTable)

-- Fully opaque
(A)-[:UNRESOLVED_CALL {variable: "@procName", line: 42}]->(:UnresolvedCall)
```

### Edge Case Automation Summary

| Scenario | Automation Level | Action |
|---|---|---|
| Normal `EXEC sp_Name` | Fully automated | Resolve → migrate |
| 4-part linked server | Detected, needs human decision | Catalog → report → human picks strategy |
| `EXEC('literal SQL')` | Fully automated | Parse inner SQL → `JdbcTemplate` |
| `sp_executesql @template` | Mostly automated | Parse template → parameterized `JdbcTemplate` |
| Concatenated dynamic SQL | Partially automated | Reconstruct → query builder skeleton |
| Opaque `EXEC(@sql)` | Detection only | Flag → `// TODO` with context |
| `EXEC @var` (literal assignment) | Fully automated | Trace → resolve → add edge |
| `EXEC @var` (conditional) | Partially automated | Add multiple edges → report |
| `EXEC @var` (from dispatch table) | Optional (needs DB) | Query dispatch table → resolve |
| `EXEC @var` (parameter) | Detection only | Flag → `// TODO` |

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
  ddl_input_dir: "sql-input/ddl"      # optional: CREATE TABLE scripts for offline schema extraction
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

  # Source SQL Server (optional: for live schema extraction)
  source_db:
    enabled: false                   # set true to connect to source SQL Server
    connection_string_env: "SOURCE_DB_CONNECTION_STRING"
    # Example: "DRIVER={ODBC Driver 18 for SQL Server};SERVER=myserver;DATABASE=mydb;UID=user;PWD=pass"

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
│   ├── procedures/          # Place stored procedure .sql files here
│   └── ddl/                 # Optional: CREATE TABLE .sql files for offline schema extraction
├── output/                  # Generated Spring Boot code
├── src/
│   └── tsql_migration/
│       ├── __init__.py
│       ├── main.py                  # CLI entry point
│       ├── config.py                # Configuration loading
│       ├── state.py                 # Pydantic state models (MigrationState, EdgeCaseReport, etc.)
│       ├── parser/
│       │   ├── __init__.py
│       │   ├── tsql_parser.py       # sqlglot-based T-SQL parser
│       │   └── edge_case_detector.py # Linked servers, dynamic SQL, variable call detection
│       ├── schema/
│       │   ├── __init__.py
│       │   ├── schema_extractor.py  # Live DB schema extraction (INFORMATION_SCHEMA via pyodbc)
│       │   ├── ddl_parser.py        # Offline DDL script parsing (sqlglot)
│       │   └── entity_generator.py  # JPA @Entity + JpaRepository code generation
│       ├── graph/
│       │   ├── __init__.py
│       │   └── neo4j_client.py      # Neo4j operations (nodes, relationships, queries)
│       ├── chunker/
│       │   ├── __init__.py
│       │   └── sql_chunker.py       # SQL-aware text splitting for large procedures
│       ├── pipeline/
│       │   ├── __init__.py
│       │   ├── graph.py             # LangGraph DAG definition
│       │   ├── nodes/
│       │   │   ├── parse_node.py
│       │   │   ├── analyze_node.py
│       │   │   ├── plan_node.py
│       │   │   ├── extract_schema_node.py  # NEW: entity + repository generation
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
    ├── test_edge_cases.py
    ├── test_schema_extractor.py
    ├── test_chunker.py
    ├── test_graph.py
    └── test_pipeline.py
```

---

## Next Steps

1. [ ] Set up Python project with `pyproject.toml` and dependencies
2. [ ] Implement `tsql_parser.py` using sqlglot (AST parsing, metadata extraction)
3. [ ] Implement `edge_case_detector.py` (linked servers, dynamic SQL, variable calls)
4. [ ] Implement `neo4j_client.py` with full data model (core + edge case nodes/relationships)
5. [ ] Implement `schema_extractor.py` (live DB) and `ddl_parser.py` (offline) + `entity_generator.py`
6. [ ] Implement `sql_chunker.py` with SQL-aware text splitting
7. [ ] Define LangGraph pipeline in `graph.py` with all 7 nodes
8. [ ] Implement each pipeline node (parse, analyze, plan, extract_schema, prepare, migrate, generate)
9. [ ] Add sample T-SQL procedures for testing (including edge cases)
10. [ ] End-to-end test with sample procedures
