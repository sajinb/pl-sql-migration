"""Edge-case-specific prompt fragments for LLM context."""


def build_edge_case_context(
    procedure_name: str,
    linked_servers: list,
    dynamic_sql_calls: list,
    variable_calls: list,
) -> str:
    """Build a string describing edge cases relevant to this procedure."""
    parts: list[str] = []

    # Filter to this procedure
    proc_linked = [ls for ls in linked_servers if ls.procedure == procedure_name]
    proc_dynamic = [ds for ds in dynamic_sql_calls if ds.procedure == procedure_name]
    proc_variable = [vc for vc in variable_calls if vc.procedure == procedure_name]

    if not proc_linked and not proc_dynamic and not proc_variable:
        return ""

    if proc_linked:
        parts.append("LINKED SERVER REFERENCES (4-part names):")
        for ls in proc_linked:
            parts.append(
                f"  Line {ls.line}: {ls.operation} [{ls.server}].[{ls.database}].{ls.remote_object}"
            )
            parts.append(
                "  → Generate a // TODO: LINKED_SERVER comment with the original reference."
            )
            parts.append(
                "  → Use a placeholder method call like remoteDataService.fetchFromRemote(...)"
            )
        parts.append("")

    if proc_dynamic:
        parts.append("DYNAMIC SQL CALLS:")
        for ds in proc_dynamic:
            parts.append(f"  Line {ds.line}: Tier={ds.tier}, Expression: {ds.expression}")
            if ds.tier == "simple" and ds.resolved_sql:
                parts.append(f"  → Resolved SQL: {ds.resolved_sql}")
                parts.append("  → Convert to a parameterized JdbcTemplate query.")
            elif ds.tier == "templated" and ds.resolved_sql:
                parts.append(f"  → Template: {ds.resolved_sql}")
                parts.append("  → Convert to a parameterized JdbcTemplate query with named params.")
            elif ds.tier == "concatenated":
                parts.append("  → This is concatenated dynamic SQL.")
                parts.append("  → Convert to a query builder pattern or Criteria API.")
                parts.append("  → IMPORTANT: Parameterize all user inputs to prevent SQL injection.")
            else:
                parts.append("  → This is OPAQUE dynamic SQL — cannot resolve statically.")
                parts.append("  → Generate a // TODO: DYNAMIC_SQL comment with the original expression.")
                parts.append("  → Create a skeleton JdbcTemplate.execute() call.")
        parts.append("")

    if proc_variable:
        parts.append("VARIABLE PROCEDURE CALLS (EXEC @variable):")
        for vc in proc_variable:
            parts.append(f"  Line {vc.line}: EXEC {vc.variable}, Certainty={vc.certainty}")
            if vc.certainty == "resolved" and vc.resolved_targets:
                parts.append(f"  → Resolves to: {vc.resolved_targets[0]}")
                parts.append(f"  → Call the corresponding service method directly.")
            elif vc.certainty == "conditional" and vc.resolved_targets:
                parts.append(f"  → Possible targets: {', '.join(vc.resolved_targets)}")
                parts.append(f"  → Condition: {vc.condition}")
                parts.append("  → Generate a switch/if-else dispatching to the correct service.")
            elif vc.certainty == "dispatch_table":
                parts.append(f"  → Dispatch table: {vc.condition}")
                parts.append("  → Generate a // TODO: DISPATCH_TABLE comment.")
                parts.append("  → Consider using a Strategy pattern with a Map<String, Service>.")
            else:
                parts.append("  → OPAQUE — cannot resolve statically.")
                parts.append("  → Generate a // TODO: VARIABLE_CALL comment with context.")
        parts.append("")

    return "\n".join(parts)
