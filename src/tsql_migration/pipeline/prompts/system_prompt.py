"""System and user prompts for the LLM migration calls."""

SYSTEM_PROMPT = """\
You are an expert T-SQL to Spring Boot 3 / Java 21 migration assistant.
Convert the given T-SQL stored procedure code to equivalent Spring Boot service code.

RULES:
1. Use Spring Boot 3.x with Java 21 features (records, pattern matching, sealed classes where appropriate).
2. The database is SQL Server — tables stay as-is. Generate code that connects to the same SQL Server.
3. Temp tables (#name) have been replaced with staging tables in the same SQL Server database.
   - Always generate a unique batchId (UUID.randomUUID().toString()) at the start of the service method.
   - Pass batchId when inserting into staging tables.
   - Clean up staging tables in a finally block using stagingTableCleanupService.cleanupBatch(batchId).
4. For procedure calls (EXEC sp_Name), call the corresponding migrated Spring @Service method.
   The caller's service class should have the callee service injected via constructor injection.
5. Use @Transactional for transaction blocks (BEGIN TRAN...COMMIT/ROLLBACK).
6. Use JdbcTemplate for complex queries. Use JPA repositories for simple CRUD when entity classes are available.
7. SQL type mapping:
   INT -> Integer, BIGINT -> Long, VARCHAR/NVARCHAR -> String,
   DATETIME/DATETIME2 -> LocalDateTime, DATE -> LocalDate,
   DECIMAL/NUMERIC/MONEY -> BigDecimal, BIT -> Boolean,
   UNIQUEIDENTIFIER -> UUID, FLOAT -> Double
8. Convert cursors to jdbcTemplate.query() with RowMapper or stream processing.
9. Convert PRINT/RAISERROR to SLF4J logging (log.info/log.error).
10. Convert TRY...CATCH to Java try-catch with proper exception handling.
11. Return OUTPUT parameters as fields in a result record/DTO.
12. Use constructor injection (not @Autowired).

OUTPUT FORMAT:
Return ONLY valid Java code. Include:
- Service class with @Service and @Transactional annotations as needed
- Any needed DTO/record classes (as inner classes or separate)
- Do NOT include package or import statements — those will be added automatically
- Use SLF4J Logger: private static final Logger log = LoggerFactory.getLogger(ClassName.class);
"""

CHUNK_FIRST_PROMPT = """\
This is the FIRST chunk of a large stored procedure.
Generate the class declaration, field declarations, constructor injection, and the start of the main method.
Do NOT close the method or class — the next chunk will continue.
"""

CHUNK_MIDDLE_PROMPT = """\
This is a MIDDLE chunk of a large stored procedure.
Continue the method body from where the previous chunk left off.
Do NOT add class declaration or close the method/class.
"""

CHUNK_LAST_PROMPT = """\
This is the LAST chunk of a large stored procedure.
Complete the method body, add any return statement, close the method, and close the class.
Include the finally block for staging table cleanup if applicable.
"""

CHUNK_ONLY_PROMPT = """\
This is the complete stored procedure (not chunked).
Generate the full service class with all methods.
"""


def build_migration_prompt(
    sql_content: str,
    procedure_name: str,
    temp_table_mapping: dict[str, str],
    dependency_services: dict[str, str],
    entity_signatures: dict[str, str],
    edge_case_context: str,
    chunk_position: str = "only",
    previous_chunk_output: str = "",
) -> str:
    """Build the full user prompt for a migration LLM call."""
    parts: list[str] = []

    # Chunk position instruction
    if chunk_position == "first":
        parts.append(CHUNK_FIRST_PROMPT)
    elif chunk_position == "middle":
        parts.append(CHUNK_MIDDLE_PROMPT)
    elif chunk_position == "last":
        parts.append(CHUNK_LAST_PROMPT)
    else:
        parts.append(CHUNK_ONLY_PROMPT)

    parts.append(f"\nProcedure: {procedure_name}\n")

    # Dependency context
    if dependency_services:
        parts.append("ALREADY MIGRATED DEPENDENCIES (inject these services and call their methods):")
        for proc_name, service_info in dependency_services.items():
            parts.append(f"  EXEC {proc_name} → {service_info}")
        parts.append("")

    # Temp table mapping
    if temp_table_mapping:
        parts.append("TEMP TABLE → STAGING TABLE MAPPING:")
        for orig, staging in temp_table_mapping.items():
            parts.append(f"  {orig} → {staging}")
        parts.append("  (All staging tables have a batch_id VARCHAR(36) column)")
        parts.append("")

    # Entity signatures
    if entity_signatures:
        parts.append("AVAILABLE JPA ENTITIES:")
        for table, sig in entity_signatures.items():
            parts.append(f"  {sig}")
        parts.append("")

    # Edge case context
    if edge_case_context:
        parts.append("SPECIAL HANDLING NOTES:")
        parts.append(edge_case_context)
        parts.append("")

    # Previous chunk output
    if previous_chunk_output:
        parts.append("PREVIOUSLY GENERATED JAVA CODE (continue from here):")
        parts.append(previous_chunk_output)
        parts.append("")

    # The SQL to migrate
    parts.append("T-SQL TO MIGRATE:")
    parts.append(sql_content)

    return "\n".join(parts)
