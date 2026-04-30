"""System and user prompts for Oracle PL/SQL → Spring Boot 3 / Java 21 migration."""

ORACLE_SYSTEM_PROMPT = """\
You are an expert Oracle PL/SQL to Spring Boot 3 / Java 21 migration assistant.
Convert the given Oracle PL/SQL procedure or function to an equivalent Spring Boot @Service class.

RULES:

1. Use Spring Boot 3.x with Java 21. Use records, pattern matching, and sealed classes where appropriate.

2. The source database is Oracle. The generated service connects to the same Oracle database.
   Use JPA repositories for simple CRUD when entity classes are provided.
   Use JdbcTemplate for complex queries, bulk operations, and anything referencing Oracle-specific SQL.

3. Oracle type mapping:
   NUMBER / INTEGER / INT         → Integer or Long (NUMBER with scale → BigDecimal)
   NUMBER(p,s) with s>0           → BigDecimal
   VARCHAR2 / NVARCHAR2 / CHAR    → String
   DATE                           → LocalDate  (Oracle DATE has no time component by convention)
   TIMESTAMP / TIMESTAMP(n)       → LocalDateTime
   CLOB / NCLOB                   → String  (or InputStream for large values)
   BLOB / RAW                     → byte[]
   BOOLEAN (PL/SQL only)          → boolean
   BINARY_INTEGER / PLS_INTEGER   → int
   SYS_REFCURSOR                  → List<Map<String,Object>> via JdbcTemplate.queryForList()
   %ROWTYPE                       → generate a corresponding Java record/DTO
   %TYPE                          → use the Java type of the referenced column from the entity

4. Parameter directions:
   IN param      → method parameter
   OUT param     → include as a field in a result record/DTO; return from the method
   IN OUT param  → include in both input and result record

5. Variable declarations (PL/SQL DECLARE section):
   v_var NUMBER := 0   → int vVar = 0;
   v_var VARCHAR2(100) → String vVar = null;
   PL/SQL collections (TYPE t IS TABLE OF ...)  → List<T> in Java

6. Global Temporary Tables (GTTs) and PL/SQL collections used as temp result sets:
   - Treat the same as T-SQL #TempTable: map to a staging table with a batch_id column.
   - Generate a unique batchId at the start: String batchId = UUID.randomUUID().toString();
   - Pass batchId on every INSERT into the staging table.
   - Clean up in a finally block: stagingTableCleanupService.cleanupBatch(batchId);

7. Procedure calls:
   pkg_name.proc_name(args)  → injectedService.methodName(args)
   CALL proc_name(args)      → injectedService.methodName(args)
   Always inject the callee service via constructor injection.

8. Dynamic SQL (EXECUTE IMMEDIATE):
   EXECUTE IMMEDIATE 'literal sql'              → jdbcTemplate.execute() / queryForObject()
   EXECUTE IMMEDIATE v_sql USING bind1, bind2  → parameterized JdbcTemplate query
   EXECUTE IMMEDIATE 'prefix' || v_val          → query builder / Criteria API
   EXECUTE IMMEDIATE v_sql (opaque)             → // TODO: DYNAMIC_SQL with skeleton JdbcTemplate call
   DBMS_SQL package                             → // TODO: DBMS_SQL — manual conversion required

9. Exception handling:
   EXCEPTION WHEN NO_DATA_FOUND THEN  → catch (EmptyResultDataAccessException e)
   EXCEPTION WHEN DUP_VAL_ON_INDEX    → catch (DataIntegrityViolationException e)
   EXCEPTION WHEN OTHERS THEN         → catch (Exception e)
   RAISE_APPLICATION_ERROR(num, msg)  → throw new RuntimeException(msg)
   RAISE                              → throw  (re-throw)

10. Transactions:
    Implicit Oracle transaction commit  → @Transactional on the method
    COMMIT                              → handled by @Transactional
    ROLLBACK                            → TransactionAspectSupport.currentTransactionStatus().setRollbackOnly()
                                          or let exception propagate

11. Cursors:
    CURSOR c IS SELECT ... / FOR rec IN cursor LOOP  → jdbcTemplate.query() with RowMapper, or stream
    OPEN / FETCH / CLOSE                              → jdbcTemplate.query() + iteration
    SYS_REFCURSOR OUT parameter                       → return List<Map<String,Object>>

12. Oracle built-in function mapping:
    SYSDATE                    → LocalDate.now() / LocalDateTime.now()
    SYSTIMESTAMP               → LocalDateTime.now()
    NVL(a, b)                  → Objects.requireNonNullElse(a, b)
    NVL2(a, b, c)              → a != null ? b : c
    DECODE(expr, v1,r1, def)   → switch expression or ternary
    COALESCE(a, b)             → Optional chaining or Objects.requireNonNullElse
    TRUNC(date)                → date.toLocalDate()
    TO_DATE('str','fmt')       → LocalDate.parse() with DateTimeFormatter
    TO_CHAR(val, 'fmt')        → String.format() or DateTimeFormatter.format()
    SUBSTR(s, pos, len)        → s.substring(pos-1, pos-1+len)
    INSTR(s, sub)              → s.indexOf(sub) + 1
    LENGTH(s)                  → s.length()
    TRIM(s)                    → s.trim()
    UPPER(s) / LOWER(s)        → s.toUpperCase() / s.toLowerCase()
    LPAD(s,n,c) / RPAD(s,n,c) → String.format("%ns", ...) or custom helper
    REPLACE(s, old, new)       → s.replace(old, new)
    TO_NUMBER(s)               → Integer.parseInt(s) / Long.parseLong(s)
    SQL%ROWCOUNT               → jdbcTemplate.update() return value
    SQL%FOUND                  → rowCount > 0
    SQL%NOTFOUND               → rowCount == 0
    ROWNUM                     → use FETCH FIRST N ROWS ONLY in SQL or Pageable in JPA
    || (string concat)         → + in Java

13. String concatenation: Oracle uses || — convert to Java + or String.format().

14. Logging:
    DBMS_OUTPUT.PUT_LINE(msg)  → log.info(msg)
    Use SLF4J: private static final Logger log = LoggerFactory.getLogger(ClassName.class);

15. Database links (table@link_name):
    Generate a // TODO: DATABASE_LINK comment with the original reference.
    Use a placeholder method call like remoteDataService.fetchFromRemote(...)

16. Package-level constants and types:
    If the procedure references package-level declarations not visible in the extracted code,
    generate a // TODO: PACKAGE_CONSTANT or // TODO: PACKAGE_TYPE comment.

17. Use constructor injection (never @Autowired field injection).

OUTPUT FORMAT:
Return ONLY valid Java code. Include:
- @Service class with @Transactional annotations as needed
- Any DTO/record classes as inner classes
- Do NOT include package or import statements — those will be added automatically
- Use SLF4J Logger: private static final Logger log = LoggerFactory.getLogger(ClassName.class);
"""

# ---------------------------------------------------------------------------
# Chunk position instructions (same structure as T-SQL prompts)
# ---------------------------------------------------------------------------

_CHUNK_FIRST = """\
This is the FIRST chunk of a large PL/SQL procedure.
Generate the class declaration, field declarations, constructor injection, and the start of the main method.
Do NOT close the method or class — the next chunk will continue.
"""

_CHUNK_MIDDLE = """\
This is a MIDDLE chunk of a large PL/SQL procedure.
Continue the method body from where the previous chunk left off.
Do NOT add class declaration or close the method/class.
"""

_CHUNK_LAST = """\
This is the LAST chunk of a large PL/SQL procedure.
Complete the method body, add any return statement, close the method, and close the class.
Include the finally block for staging table cleanup if applicable.
"""

_CHUNK_ONLY = """\
This is the complete PL/SQL procedure (not chunked).
Generate the full @Service class with all methods.
"""


def build_oracle_migration_prompt(
    sql_content: str,
    procedure_name: str,
    temp_table_mapping: dict[str, str],
    dependency_services: dict[str, str],
    entity_signatures: dict[str, str],
    edge_case_context: str,
    chunk_position: str = "only",
    previous_chunk_output: str = "",
) -> str:
    """Build the full user prompt for an Oracle PL/SQL migration LLM call."""
    parts: list[str] = []

    if chunk_position == "first":
        parts.append(_CHUNK_FIRST)
    elif chunk_position == "middle":
        parts.append(_CHUNK_MIDDLE)
    elif chunk_position == "last":
        parts.append(_CHUNK_LAST)
    else:
        parts.append(_CHUNK_ONLY)

    parts.append(f"\nProcedure: {procedure_name}\n")

    if dependency_services:
        parts.append("ALREADY MIGRATED DEPENDENCIES (inject and call these services):")
        for proc_name, service_info in dependency_services.items():
            parts.append(f"  {proc_name} → {service_info}")
        parts.append("")

    if temp_table_mapping:
        parts.append("GTT / COLLECTION → STAGING TABLE MAPPING:")
        for orig, staging in temp_table_mapping.items():
            parts.append(f"  {orig} → {staging}")
        parts.append("  (All staging tables have a batch_id VARCHAR(36) column)")
        parts.append("")

    if entity_signatures:
        parts.append("AVAILABLE JPA ENTITIES:")
        for table, sig in entity_signatures.items():
            parts.append(f"  {sig}")
        parts.append("")

    if edge_case_context:
        parts.append("SPECIAL HANDLING NOTES:")
        parts.append(edge_case_context)
        parts.append("")

    if previous_chunk_output:
        parts.append("PREVIOUSLY GENERATED JAVA CODE (continue from here):")
        parts.append(previous_chunk_output)
        parts.append("")

    parts.append("PL/SQL TO MIGRATE:")
    parts.append(sql_content)

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Oracle-specific edge case context builder
# ---------------------------------------------------------------------------

def build_oracle_edge_case_context(
    procedure_name: str,
    linked_servers: list,
    dynamic_sql_calls: list,
    variable_calls: list,
) -> str:
    """Build a string describing Oracle edge cases relevant to *procedure_name*."""
    parts: list[str] = []

    proc_links = [r for r in linked_servers if r.procedure == procedure_name]
    proc_dynamic = [r for r in dynamic_sql_calls if r.procedure == procedure_name]
    proc_variable = [r for r in variable_calls if r.procedure == procedure_name]

    if not proc_links and not proc_dynamic and not proc_variable:
        return ""

    if proc_links:
        parts.append("DATABASE LINK REFERENCES (table@link_name):")
        for ref in proc_links:
            parts.append(
                f"  Line {ref.line}: {ref.operation} {ref.remote_object}@{ref.server}"
            )
            parts.append(
                "  → Generate a // TODO: DATABASE_LINK comment with the original reference."
            )
            parts.append(
                "  → Use a placeholder method call: remoteDataService.fetchFromRemote(...)"
            )
        parts.append("")

    if proc_dynamic:
        parts.append("DYNAMIC SQL (EXECUTE IMMEDIATE):")
        for ref in proc_dynamic:
            parts.append(f"  Line {ref.line}: Tier={ref.tier}, Expression: {ref.expression}")
            if ref.tier == "simple" and ref.resolved_sql:
                parts.append(f"  → Resolved SQL: {ref.resolved_sql}")
                parts.append("  → Convert to a parameterized JdbcTemplate query.")
            elif ref.tier == "templated":
                if ref.resolved_sql:
                    parts.append(f"  → Template: {ref.resolved_sql}")
                parts.append("  → Convert to a JdbcTemplate query with named parameters (USING binds → ?)")
            elif ref.tier == "concatenated":
                parts.append("  → Concatenated dynamic SQL (|| operators).")
                parts.append("  → Convert to a query builder pattern.")
                parts.append("  → IMPORTANT: Parameterize all user inputs to prevent SQL injection.")
            else:
                parts.append("  → OPAQUE — cannot resolve statically.")
                parts.append("  → Generate a // TODO: DYNAMIC_SQL comment.")
                parts.append("  → Create a skeleton JdbcTemplate.execute() call.")
        parts.append("")

    if proc_variable:
        parts.append("DYNAMIC PROCEDURE DISPATCH (EXECUTE IMMEDIATE with proc name variable):")
        for ref in proc_variable:
            parts.append(f"  Line {ref.line}: Variable={ref.variable}, Certainty={ref.certainty}")
            if ref.certainty == "resolved" and ref.resolved_targets:
                parts.append(f"  → Resolves to: {ref.resolved_targets[0]}")
                parts.append("  → Call the corresponding service method directly.")
            elif ref.certainty == "conditional" and ref.resolved_targets:
                parts.append(f"  → Possible targets: {', '.join(ref.resolved_targets)}")
                parts.append(f"  → Condition: {ref.condition}")
                parts.append("  → Generate an if-else or switch dispatching to the correct service.")
            elif ref.certainty == "dispatch_table":
                parts.append(f"  → Dispatch table: {ref.condition}")
                parts.append("  → Generate a // TODO: DISPATCH_TABLE comment.")
                parts.append("  → Consider a Strategy pattern with Map<String, Service>.")
            else:
                parts.append("  → OPAQUE — cannot resolve statically.")
                parts.append("  → Generate a // TODO: VARIABLE_DISPATCH comment.")
        parts.append("")

    return "\n".join(parts)
