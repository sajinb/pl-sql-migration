# T-SQL to Spring Boot 3 Migration Tool

AI-powered agentic workflow that migrates T-SQL (SQL Server) stored procedures to Spring Boot 3 / Java 21 services.

Built with **Python**, using **sqlglot** for T-SQL parsing, **LangGraph** for pipeline orchestration, **LangChain** for LLM calls, and **Neo4j** for call graph analysis.

## Migration Scope

```
BEFORE:  Application --> SQL Server (tables + stored procedures)
AFTER:   Application --> Spring Boot 3 (Java services) --> SQL Server (same tables)
```

| Artifact | Migrated? | Details |
|---|---|---|
| **Tables** | No | Stay in SQL Server, no DDL changes |
| **Stored Procedures** | Yes | Business logic moves to Java `@Service` classes |
| **Temp Tables** | Yes | Replaced with `stg_` staging tables in same DB |
| **Table Access** | Yes | JPA `@Entity` + `JpaRepository` generated for all referenced tables |

## Tech Stack

| Layer | Technology | Purpose |
|---|---|---|
| T-SQL Parsing | `sqlglot` (TSQL dialect) | Parse procedures into AST, extract metadata |
| Orchestration | `langgraph` | Deterministic stateful DAG pipeline |
| LLM Calls | `langchain-anthropic` / `langchain-openai` | Code generation via Claude or GPT |
| Chunking | `langchain-text-splitters` | Split large procedures (2000+ lines) for LLM context |
| Call Graph | `neo4j` | Store/query procedure dependencies, topological sort |
| Data Models | `pydantic` | Type-safe state for LangGraph nodes |

## Architecture

```
                         LangGraph Pipeline
  +----------+  +----------+  +--------+  +---------+  +---------+
  |  PARSE   |->| ANALYZE  |->|  PLAN  |->| EXTRACT |->| PREPARE |
  | (sqlglot)|  | (Neo4j)  |  |(topo-  |  | SCHEMA  |  |(staging |
  |          |  |          |  | sort)  |  |(entities)|  | DDL)    |
  +----------+  +----------+  +--------+  +---------+  +----+----+
                                                             |
                                                             v
                                                      +-----------+
                                                      |  MIGRATE  |
                                                      | (per proc)|
                                                      +-----+-----+
                                                            |
                                              +-------------+-------------+
                                              v                           v
                                       +-----------+              +-----------+
                                       |Small Proc |              |Large Proc |
                                       |(1 LLM     |              |(Chunked   |
                                       | call)     |              | N calls)  |
                                       +-----+-----+              +-----+-----+
                                              |                           |
                                              +-------------+-------------+
                                                            v
                                                      +-----------+
                                                      | GENERATE  |
                                                      |(Java, DDL,|
                                                      | reports)  |
                                                      +-----------+
```

## Pipeline Nodes

| # | Node | Input | Output |
|---|------|-------|--------|
| 1 | **PARSE** | `.sql` files | `ProcedureMetadata` per procedure (params, temp tables, called procs, tables, logical blocks) |
| 2 | **ANALYZE** | Parsed metadata | Call graph in Neo4j + `EdgeCaseReport` (linked servers, dynamic SQL, variable calls) |
| 3 | **PLAN** | Neo4j call graph | Migration order (bottom-up: leaf procedures first) |
| 4 | **EXTRACT SCHEMA** | Referenced table names | JPA `@Entity` classes + `JpaRepository` interfaces (from live DB or DDL scripts) |
| 5 | **PREPARE** | Temp table metadata | Staging table DDLs with `batch_id` isolation |
| 6 | **MIGRATE** | Procedures in order | Java `@Service` code via LLM (single call or chunked for large procedures) |
| 7 | **GENERATE** | All migrated code | Output files: entities, repositories, services, DDLs, reports |

## Key Design Decisions

### 1. Temp Tables -> Staging Tables (NOT In-Memory)

Temp table data is **not** held in Java memory. Each `#TempTable` becomes a real staging table with a `batch_id` column for concurrent execution isolation. Cleanup happens in a `finally` block + a scheduled hourly safety net.

```
T-SQL:   CREATE TABLE #ProcessingLog (...)
         INSERT INTO #ProcessingLog ...
Java:    String batchId = UUID.randomUUID().toString();
         jdbcTemplate.update("INSERT INTO stg_processing_log (batch_id, ...) VALUES (?, ...)", batchId, ...);
         ... finally { cleanupService.cleanupBatch(batchId); }
```

### 2. Call Graph -> Neo4j (Bottom-Up Migration)

Procedures are migrated **after** their dependencies. When `sp_ProcessOrder` calls `sp_CalculateOrderTotal`, the latter is migrated first, and its Java service name is injected into the LLM prompt for the caller.

```
sp_GetCustomerDetails      (leaf, migrated 1st)
sp_CalculateOrderTotal     (leaf, migrated 2nd)
sp_ProcessOrder            (calls both, migrated 3rd)
sp_GenerateMonthlyReport   (calls ProcessOrder, migrated 4th)
```

### 3. Large Procedure Chunking (LLM Context Window)

Procedures exceeding a configurable threshold (default: 2000 lines) are split using SQL-aware separators (`BEGIN TRY`, `DECLARE`, `CREATE TABLE #`, etc.). Each chunk carries variable context and prior generated output to the next LLM call. This enables **LLM portability** across providers with different context limits.

### 4. Three Tough Scenarios

| Scenario | Detection | Handling |
|---|---|---|
| **Linked Servers** (4-part names) | Regex on `[Server].[DB].[Schema].[Object]` | Catalog in Neo4j as `(:LinkedServer)`, flag for human decision |
| **Dynamic SQL** (`EXEC(@sql)`) | 4-tier classification: simple, templated, concatenated, opaque | Simple/templated -> `JdbcTemplate`; concatenated -> query builder; opaque -> `// TODO` |
| **Variable Calls** (`EXEC @var`) | Backward variable tracing | Resolved -> direct call; conditional -> switch; dispatch table -> strategy pattern; opaque -> `// TODO` |

## Quick Start

### Prerequisites

- Python 3.12+
- Neo4j Community Edition (running on `bolt://localhost:7687`)
- Anthropic API key (or OpenAI)

### Install

```bash
pip install -e .

# For live SQL Server schema extraction:
pip install -e ".[db]"

# For development:
pip install -e ".[dev]"
```

### Configure

Edit `config.yaml` or use CLI flags:

```yaml
migration:
  sql_input_dir: "sql-input/procedures"
  ddl_input_dir: "sql-input/ddl"
  output_dir: "output"
  llm_provider: "anthropic"       # or "openai"
  llm_model: "claude-sonnet-4-6"  # or "gpt-4o"
  neo4j_uri: "bolt://localhost:7687"
  neo4j_database: ""              # dedicated DB (Enterprise/Aura); empty for Community
  neo4j_label_prefix: "Mig_"     # prefix all node labels to avoid collisions
  large_procedure_threshold: 2000
  staging_table_prefix: "stg_"
  base_package: "com.migration.generated"
```

#### Neo4j Database Isolation

If your Neo4j instance is shared with other applications, you have two options:

**Option A: Dedicated database (Enterprise/Aura)**
```yaml
neo4j_database: "tsql_migration"   # all queries scoped to this database
neo4j_label_prefix: ""             # no prefix needed with a dedicated DB
```
Create the database and user first:
```cypher
CREATE DATABASE tsql_migration;
CREATE USER tsql_migrator SET PASSWORD 'secure-password';
GRANT ALL ON DATABASE tsql_migration TO tsql_migrator;
```

**Option B: Namespaced labels (Community Edition — default)**
```yaml
neo4j_database: ""                 # uses the default database
neo4j_label_prefix: "Mig_"        # all labels prefixed: Mig_Procedure, Mig_Table, etc.
```
Only nodes with the `Mig_` prefix are created or deleted. Existing data is untouched.

Set environment variables:
```bash
export ANTHROPIC_API_KEY="sk-ant-..."
export NEO4J_PASSWORD="your-password"

# Optional: for live SQL Server schema extraction
export SOURCE_DB_CONNECTION_STRING="DRIVER={ODBC Driver 18 for SQL Server};SERVER=...;DATABASE=...;UID=...;PWD=..."
```

### Run

```bash
# Basic usage
tsql-migrate sql-input/procedures

# With options
tsql-migrate sql-input/procedures \
  --output output \
  --provider anthropic \
  --model claude-sonnet-4-6 \
  --threshold 2000 \
  --checkpoint   # enable resume-on-failure
```

### Output

```
output/
  entity/                    # JPA @Entity classes
    Customers.java
    Orders.java
    OrderItems.java
    ...
  repository/                # JpaRepository interfaces
    CustomersRepository.java
    OrdersRepository.java
    ...
  service/                   # Migrated @Service classes
    GetCustomerDetailsService.java
    CalculateOrderTotalService.java
    ProcessOrderService.java
    GenerateMonthlyReportService.java
  ddl/
    staging_tables.sql       # CREATE TABLE for staging tables
  cleanup/
    StagingTableCleanupService.java
    BatchIdGenerator.java
  migration_report.json      # Summary of migration results
  edge_case_report.json      # Linked servers, dynamic SQL, unresolved calls
```

## Sample Procedures

The `sql-input/procedures/` directory includes 5 sample procedures:

| File | Procedure | Features |
|------|-----------|----------|
| `01_sp_GetCustomerDetails.sql` | Leaf procedure | Simple SELECT, IF/ELSE |
| `02_sp_CalculateOrderTotal.sql` | Leaf with OUTPUT params | OUTPUT parameters, UPDATE, variable assignment |
| `03_sp_ProcessOrder.sql` | Mid-level | Calls 2 procs, temp table, TRY/CATCH, transaction |
| `04_sp_GenerateMonthlyReport.sql` | Top-level (large) | 3 temp tables, cursor, calls ProcessOrder |
| `05_sp_EdgeCases.sql` | Edge case demo | Linked server, dynamic SQL (3 tiers), variable calls (3 patterns) |

Table DDLs are in `sql-input/ddl/tables.sql` (11 tables).

## T-SQL to Java Mapping

| T-SQL | Spring Boot 3 / Java 21 |
|---|---|
| `CREATE PROCEDURE` | `@Service` class with method |
| `@Param INT` | `int param` method parameter |
| `@Param OUTPUT` | Return as record/DTO field |
| `#TempTable` | Staging table with `batch_id` + cleanup |
| `EXEC sp_Name` | `injectedService.methodName()` |
| `CURSOR...FETCH` | `jdbcTemplate.query()` + iteration |
| `BEGIN TRY...CATCH` | `try { } catch (Exception e) { }` |
| `BEGIN TRAN...COMMIT` | `@Transactional` |
| `RAISERROR` | `throw new RuntimeException()` / SLF4J |
| `SCOPE_IDENTITY()` | JPA `save()` return / `GeneratedKeyHolder` |
| `GETDATE()` | `LocalDateTime.now()` |
| `ISNULL(a, b)` | `Objects.requireNonNullElse(a, b)` |
| `@@ROWCOUNT` | `jdbcTemplate.update()` return value |

## Project Structure

```
src/tsql_migration/
  main.py                          # CLI entry point
  config.py                        # YAML config loading
  state.py                         # Pydantic state models
  parser/
    tsql_parser.py                 # sqlglot-based T-SQL parser
    edge_case_detector.py          # Linked servers, dynamic SQL, variable calls
  schema/
    schema_extractor.py            # Live DB schema via INFORMATION_SCHEMA
    ddl_parser.py                  # Offline DDL parsing via sqlglot
    entity_generator.py            # JPA @Entity + JpaRepository generation
  graph/
    neo4j_client.py                # Neo4j operations and Cypher queries
  chunker/
    sql_chunker.py                 # SQL-aware text splitting
  pipeline/
    graph.py                       # LangGraph DAG definition
    nodes/
      parse_node.py                # Node 1: Parse T-SQL files
      analyze_node.py              # Node 2: Build call graph + detect edge cases
      plan_node.py                 # Node 3: Topological sort for migration order
      extract_schema_node.py       # Node 4: Generate entities from DB/DDL
      prepare_node.py              # Node 5: Generate staging table DDLs
      migrate_node.py              # Node 6: LLM-powered code migration
      generate_node.py             # Node 7: Write output files
    prompts/
      system_prompt.py             # LLM system and user prompts
      chunk_prompt.py              # Edge case prompt fragments
  output/
    java_writer.py                 # Java file writing utility
```
