# SQL to Spring Boot 3 Migration Tool

AI-powered agentic workflow that migrates **T-SQL (SQL Server)** and **Oracle PL/SQL** stored procedures to Spring Boot 3 / Java 21 services.

Built with **Python**, using **sqlglot** for SQL parsing, **LangGraph** for pipeline orchestration, **LangChain** for LLM calls, and **Neo4j** for call graph analysis.

## Migration Scope

```
BEFORE:  Application --> SQL Server / Oracle  (tables + stored procedures)
AFTER:   Application --> Spring Boot 3 (Java services) --> SQL Server / Oracle (same tables)
```

| Artifact | Migrated? | Details |
|---|---|---|
| **Tables** | No | Stay in the source database, no DDL changes |
| **Stored Procedures** | Yes | Business logic moves to Java `@Service` classes |
| **Temp Tables / GTTs** | Yes | T-SQL `#temp` and Oracle GTTs → `stg_` staging tables with `batch_id` isolation |
| **PL/SQL Packages** | Yes | Each procedure/function in a package body is unwrapped and migrated individually |
| **Table Access** | Yes | JPA `@Entity` + `JpaRepository` generated for all referenced tables |

## Tech Stack

| Layer | Technology | Purpose |
|---|---|---|
| SQL Parsing | `sqlglot` (TSQL + Oracle dialects) | Parse procedures/packages into AST, extract metadata |
| Orchestration | `langgraph` | Deterministic stateful DAG pipeline |
| LLM Calls | `langchain-anthropic` / `langchain-openai` | Code generation via Claude or GPT |
| Chunking | `langchain-text-splitters` | Split large procedures (2000+ lines) for LLM context |
| Call Graph | `neo4j` (or LadybugDB embedded) | Store/query procedure dependencies, topological sort |
| Data Models | `pydantic` | Type-safe state for LangGraph nodes |

## Architecture

```
                         LangGraph Pipeline
  +----------+  +----------+  +--------+  +---------+  +---------+
  |  PARSE   |->| ANALYZE  |->|  PLAN  |->| EXTRACT |->| PREPARE |
  | (sqlglot)|  | (Neo4j / |  |(topo-  |  | SCHEMA  |  |(staging |
  |          |  | Ladybug) |  | sort)  |  |(entities)|  | DDL)    |
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
                                                      | VALIDATE  |
                                                      |(structural|
                                                      |+ LLM opt) |
                                                      +-----+-----+
                                                            |
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
| 2 | **ANALYZE** | Parsed metadata | Call graph in Neo4j/LadybugDB + `EdgeCaseReport` (linked servers, dynamic SQL, variable calls) |
| 3 | **PLAN** | Call graph | Migration order (bottom-up: leaf procedures first) |
| 4 | **EXTRACT SCHEMA** | Referenced table names | JPA `@Entity` classes + `JpaRepository` interfaces (from live DB or DDL scripts) |
| 5 | **PREPARE** | Temp table metadata | Staging table DDLs with `batch_id` isolation |
| 6 | **MIGRATE** | Procedures in order | Java `@Service` code via LLM (single call or chunked for large procedures) |
| 7 | **VALIDATE** | Generated service code | `ValidationResult` per procedure — structural checks + optional LLM review |
| 8 | **GENERATE** | All migrated + validated code | Output files: entities, repositories, services, DDLs, reports |

## Key Design Decisions

### 1. Temp Tables / GTTs -> Staging Tables (NOT In-Memory)

Temp table data is **not** held in Java memory. Each `#TempTable` (T-SQL) or Global Temporary Table (Oracle) becomes a real staging table with a `batch_id` column for concurrent execution isolation. Cleanup happens in a `finally` block + a scheduled hourly safety net.

```
T-SQL:   CREATE TABLE #ProcessingLog (...)
         INSERT INTO #ProcessingLog ...

Oracle:  CREATE GLOBAL TEMPORARY TABLE gtt_processing_log (...) ON COMMIT DELETE ROWS;
         INSERT INTO gtt_processing_log ...

Java:    String batchId = UUID.randomUUID().toString();
         jdbcTemplate.update("INSERT INTO stg_processing_log (batch_id, ...) VALUES (?, ...)", batchId, ...);
         ... finally { cleanupService.cleanupBatch(batchId); }
```

### 2. Call Graph -> Neo4j (Bottom-Up Migration)

Procedures are migrated **after** their dependencies. When `sp_ProcessOrder` calls `sp_CalculateOrderTotal`, the latter is migrated first, and its Java service name is injected into the LLM prompt for the caller. The same applies to Oracle package-qualified calls (`pkg.proc_name`).

```
sp_GetCustomerDetails      (leaf, migrated 1st)
sp_CalculateOrderTotal     (leaf, migrated 2nd)
sp_ProcessOrder            (calls both, migrated 3rd)
sp_GenerateMonthlyReport   (calls ProcessOrder, migrated 4th)
```

### 3. Oracle Package Body Unwrapping

Oracle procedures often live inside package bodies. The parser unwraps each `PROCEDURE` and `FUNCTION` within a package body into its own `ProcedureMetadata`, named `<package>.<procedure>`. A state machine tracks `BEGIN`/`END` nesting (ignoring `END IF`, `END LOOP`, `END CASE`) to correctly identify procedure boundaries.

```
CREATE OR REPLACE PACKAGE BODY pkg_orders AS
    PROCEDURE get_order(...)  →  ProcedureMetadata("pkg_orders.get_order")
    FUNCTION  calc_total(...) →  ProcedureMetadata("pkg_orders.calc_total")
END pkg_orders;
```

### 4. Large Procedure Chunking (LLM Context Window)

Procedures exceeding a configurable threshold (default: 2000 lines) are split using dialect-aware separators. T-SQL uses `BEGIN TRY`, `DECLARE`, `CREATE TABLE #`, etc. Oracle uses `EXCEPTION`, `EXECUTE IMMEDIATE`, `END LOOP`, `END IF`, etc. Each chunk carries variable context and prior generated output to the next LLM call.

### 5. Tough Scenarios — T-SQL vs Oracle

| Scenario | T-SQL | Oracle | Handling |
|---|---|---|---|
| **Remote object access** | Linked servers (`[Server].[DB].[Schema].[Obj]`) | Database links (`table@link_name`) | Catalog in graph, `// TODO: LINKED_SERVER` / `DATABASE_LINK`, placeholder method |
| **Dynamic SQL** | `EXEC(@sql)` / `sp_executesql` | `EXECUTE IMMEDIATE` / `DBMS_SQL` | 4-tier: simple → `JdbcTemplate`; templated → parameterized; concatenated → query builder; opaque → `// TODO` |
| **Dynamic dispatch** | `EXEC @var` | `EXECUTE IMMEDIATE 'BEGIN ' \|\| v_proc \|\| '(); END;'` | Backward variable tracing: resolved → direct call; conditional → switch; dispatch table → strategy pattern; opaque → `// TODO` |

## Quick Start

### Prerequisites

- Python 3.12+
- Neo4j Community Edition (running on `bolt://localhost:7687`) — or use LadybugDB (embedded, no server)
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
  sql_dialect: "tsql"             # "tsql" for SQL Server | "oracle" for Oracle PL/SQL
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
  validate_llm_review: false    # send generated code back to LLM for review (slower)
```

For Oracle, also set:
```yaml
migration:
  sql_dialect: "oracle"
```
Oracle `.pkb` (package body) and `.prc` files are parsed in addition to `.sql`.

#### Graph Database: Neo4j or LadybugDB

The call graph can be stored in **Neo4j** (default, requires a running server) or **LadybugDB** (embedded, no server — stores in a local directory).

| | Neo4j | LadybugDB |
|---|---|---|
| Setup | Separate server process | `pip install real-ladybug`, no server |
| Storage | Remote (bolt://) | Local directory (`.ladybug_db/`) |
| Isolation | Database/label prefix | Per-project directory |
| Query language | Cypher | openCypher (same syntax) |

LadybugDB support is implemented in `graph/ladybug_client.py` and can be wired in as a drop-in replacement for the Neo4j client.

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
  migration_report.json      # Summary of migration results + validation pass/fail counts
  edge_case_report.json      # Linked servers, dynamic SQL, unresolved calls
  validation_report.json     # Per-procedure validation issues and TODO marker counts
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

## SQL to Java Mapping

### T-SQL

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

### Oracle PL/SQL

| Oracle PL/SQL | Spring Boot 3 / Java 21 |
|---|---|
| `CREATE OR REPLACE PROCEDURE` | `@Service` class with method |
| `CREATE OR REPLACE PACKAGE BODY` | One `@Service` per procedure/function (unwrapped) |
| `p_param IN NUMBER` | `int param` / `long param` / `BigDecimal param` |
| `p_param OUT VARCHAR2` | Return as record/DTO field |
| `p_param IN OUT type` | Input parameter + returned in result record |
| `GLOBAL TEMPORARY TABLE` / `TYPE t IS TABLE OF` | Staging table with `batch_id` + cleanup |
| `pkg_name.proc_name(args)` | `injectedService.methodName(args)` |
| `FOR rec IN cursor LOOP` | `jdbcTemplate.query()` + iteration |
| `SYS_REFCURSOR OUT` | `List<Map<String,Object>>` via `queryForList()` |
| `EXCEPTION WHEN NO_DATA_FOUND` | `catch (EmptyResultDataAccessException e)` |
| `EXCEPTION WHEN OTHERS` | `catch (Exception e)` |
| `RAISE_APPLICATION_ERROR` | `throw new RuntimeException(msg)` |
| `COMMIT` (implicit) | `@Transactional` |
| `EXECUTE IMMEDIATE 'sql'` | `jdbcTemplate.execute()` / `queryForObject()` |
| `EXECUTE IMMEDIATE v_sql USING b1, b2` | Parameterized `JdbcTemplate` query |
| `DBMS_OUTPUT.PUT_LINE(msg)` | `log.info(msg)` |
| `SYSDATE` | `LocalDate.now()` |
| `NVL(a, b)` | `Objects.requireNonNullElse(a, b)` |
| `DECODE(expr, v, r, def)` | `switch` expression / ternary |
| `SQL%ROWCOUNT` | `jdbcTemplate.update()` return value |
| `\|\|` (string concat) | `+` in Java |
| `%ROWTYPE` | Generated Java record/DTO |

## Project Structure

```
src/tsql_migration/
  main.py                          # CLI entry point
  config.py                        # YAML config loading
  state.py                         # Pydantic state models
  parser/
    tsql_parser.py                 # sqlglot-based T-SQL parser
    edge_case_detector.py          # T-SQL: linked servers, dynamic SQL, variable calls
    oracle_parser.py               # Oracle PL/SQL parser + package body unwrapper
    oracle_edge_case_detector.py   # Oracle: database links, EXECUTE IMMEDIATE, dynamic dispatch
  schema/
    schema_extractor.py            # Live DB schema via INFORMATION_SCHEMA
    ddl_parser.py                  # Offline DDL parsing via sqlglot
    entity_generator.py            # JPA @Entity + JpaRepository generation
  graph/
    neo4j_client.py                # Neo4j operations and Cypher queries
    ladybug_client.py              # LadybugDB embedded alternative (drop-in replacement)
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
      validate_node.py             # Node 7: Structural + optional LLM review of generated Java
      generate_node.py             # Node 8: Write output files
    prompts/
      system_prompt.py             # T-SQL LLM system and user prompts
      chunk_prompt.py              # T-SQL edge case prompt fragments
      oracle_system_prompt.py      # Oracle LLM system prompt + edge case context builder
  output/
    java_writer.py                 # Java file writing utility
```
