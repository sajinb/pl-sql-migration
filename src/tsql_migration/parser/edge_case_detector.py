"""Detects the three toughest T-SQL scenarios:

1. Linked Servers (4-part names)  — e.g. [Server].[DB].[schema].[table]
2. Dynamic SQL (EXEC(@sql))       — EXEC(expr), sp_executesql
3. Variable Calls (EXEC @var)     — EXEC @procName
"""

from __future__ import annotations

import re
from typing import Optional

from tsql_migration.state import (
    DynamicSqlRef,
    EdgeCaseReport,
    LinkedServerRef,
    ProcedureMetadata,
    VariableCallRef,
)


class EdgeCaseDetector:
    """Detects linked servers, dynamic SQL, and variable procedure calls in T-SQL."""

    def detect(self, procedure: ProcedureMetadata) -> EdgeCaseReport:
        """Analyze a procedure for all three tough scenarios."""
        sql = procedure.raw_sql
        linked_servers = self._detect_linked_servers(procedure.procedure_name, sql)
        dynamic_sql = self._detect_dynamic_sql(procedure.procedure_name, sql)
        variable_calls = self._detect_variable_calls(procedure.procedure_name, sql)

        unresolved = (
            len(linked_servers)
            + len([d for d in dynamic_sql if d.tier in ("concatenated", "opaque")])
            + len([v for v in variable_calls if v.certainty in ("dispatch_table", "opaque")])
        )

        return EdgeCaseReport(
            linked_servers=linked_servers,
            dynamic_sql_calls=dynamic_sql,
            variable_calls=variable_calls,
            unresolved_count=unresolved,
        )

    def detect_all(
        self, procedures: dict[str, ProcedureMetadata]
    ) -> EdgeCaseReport:
        """Aggregate edge cases across all procedures."""
        combined = EdgeCaseReport()
        for proc in procedures.values():
            report = self.detect(proc)
            combined.linked_servers.extend(report.linked_servers)
            combined.dynamic_sql_calls.extend(report.dynamic_sql_calls)
            combined.variable_calls.extend(report.variable_calls)
            combined.unresolved_count += report.unresolved_count
        return combined

    # -------------------------------------------------------------------
    # 1. Linked Servers (4-part names)
    # -------------------------------------------------------------------

    def _detect_linked_servers(
        self, proc_name: str, sql: str
    ) -> list[LinkedServerRef]:
        """Detect 4-part name references: [Server].[Database].[Schema].[Object]."""
        refs: list[LinkedServerRef] = []

        # Pattern: [Server].[Database].[Schema].[Object] or Server.Database.Schema.Object
        pattern = re.compile(
            r"(?i)(?:FROM|JOIN|INTO|UPDATE|EXEC(?:UTE)?)\s+"
            r"\[?(\w+)\]?\.\[?(\w+)\]?\.\[?(\w+)\]?\.\[?(\w+)\]?",
        )

        for line_num, line in enumerate(sql.split("\n"), 1):
            for match in pattern.finditer(line):
                server = match.group(1)
                database = match.group(2)
                schema = match.group(3)
                obj = match.group(4)

                # Determine operation type from keyword before the 4-part name
                operation = self._classify_operation(line, match.start())

                refs.append(
                    LinkedServerRef(
                        procedure=proc_name,
                        server=server,
                        database=database,
                        remote_object=f"{schema}.{obj}",
                        operation=operation,
                        line=line_num,
                    )
                )

        return refs

    # -------------------------------------------------------------------
    # 2. Dynamic SQL
    # -------------------------------------------------------------------

    def _detect_dynamic_sql(
        self, proc_name: str, sql: str
    ) -> list[DynamicSqlRef]:
        """Detect EXEC(@sql), EXEC('literal'), and sp_executesql calls."""
        refs: list[DynamicSqlRef] = []
        lines = sql.split("\n")

        for line_num, line in enumerate(lines, 1):
            trimmed = line.strip()

            # EXEC(@variable) or EXEC(@expr)
            exec_expr_match = re.search(
                r"(?i)\bEXEC(?:UTE)?\s*\(\s*(.+?)\s*\)", trimmed
            )
            if exec_expr_match:
                expr = exec_expr_match.group(1)
                tier, resolved = self._classify_dynamic_sql(expr, lines, line_num)
                refs.append(
                    DynamicSqlRef(
                        procedure=proc_name,
                        tier=tier,
                        expression=expr,
                        line=line_num,
                        resolved_sql=resolved,
                    )
                )
                continue

            # sp_executesql
            sp_exec_match = re.search(
                r"(?i)\bEXEC(?:UTE)?\s+sp_executesql\s+(.+)", trimmed
            )
            if sp_exec_match:
                template_expr = sp_exec_match.group(1).split(",")[0].strip()
                tier = "templated"
                resolved = None
                # If template is a string literal, extract it
                if template_expr.startswith(("N'", "'")):
                    resolved = template_expr.strip("N").strip("'")
                    tier = "templated"
                elif template_expr.startswith("@"):
                    # Variable — try to trace backward
                    traced = self._trace_variable_value(
                        template_expr, lines, line_num - 1
                    )
                    if traced:
                        resolved = traced
                        tier = "templated"
                    else:
                        tier = "opaque"

                refs.append(
                    DynamicSqlRef(
                        procedure=proc_name,
                        tier=tier,
                        expression=trimmed,
                        line=line_num,
                        resolved_sql=resolved,
                    )
                )

        return refs

    def _classify_dynamic_sql(
        self, expression: str, lines: list[str], current_line: int
    ) -> tuple[str, Optional[str]]:
        """Classify dynamic SQL expression into a tier."""
        expr = expression.strip()

        # Tier 1: Simple — literal string
        if expr.startswith(("'", "N'")):
            resolved = expr.strip("N").strip("'")
            return "simple", resolved

        # Tier 3/4: Variable reference
        if expr.startswith("@"):
            traced = self._trace_variable_value(expr, lines, current_line - 1)
            if traced:
                # Check if it's concatenated
                if "+" in traced:
                    return "concatenated", traced
                return "simple", traced.strip("N").strip("'")
            return "opaque", None

        # Tier 3: Concatenation in the EXEC() itself
        if "+" in expr:
            return "concatenated", expr

        return "opaque", None

    # -------------------------------------------------------------------
    # 3. Variable Calls (EXEC @procName)
    # -------------------------------------------------------------------

    def _detect_variable_calls(
        self, proc_name: str, sql: str
    ) -> list[VariableCallRef]:
        """Detect EXEC @variable patterns."""
        refs: list[VariableCallRef] = []
        lines = sql.split("\n")

        for line_num, line in enumerate(lines, 1):
            # EXEC @variable (but NOT EXEC(@expr) and NOT EXEC proc_name)
            match = re.search(
                r"(?i)\bEXEC(?:UTE)?\s+(@\w+)\b(?!\s*\()", line.strip()
            )
            if not match:
                continue

            variable = match.group(1)
            # Backward trace to find assignments
            assignments = self._trace_all_assignments(variable, lines, line_num - 1)

            if not assignments:
                # Variable is a parameter or unresolvable
                refs.append(
                    VariableCallRef(
                        procedure=proc_name,
                        variable=variable,
                        certainty="opaque",
                        resolved_targets=[],
                        line=line_num,
                    )
                )
            elif len(assignments) == 1 and assignments[0]["type"] == "literal":
                refs.append(
                    VariableCallRef(
                        procedure=proc_name,
                        variable=variable,
                        certainty="resolved",
                        resolved_targets=[assignments[0]["value"]],
                        line=line_num,
                    )
                )
            elif all(a["type"] == "literal" for a in assignments):
                # Multiple literal assignments (IF/ELSE branches)
                targets = [a["value"] for a in assignments]
                conditions = [a.get("condition", "") for a in assignments]
                refs.append(
                    VariableCallRef(
                        procedure=proc_name,
                        variable=variable,
                        certainty="conditional",
                        resolved_targets=targets,
                        condition="; ".join(c for c in conditions if c),
                        line=line_num,
                    )
                )
            elif any(a["type"] == "table_lookup" for a in assignments):
                refs.append(
                    VariableCallRef(
                        procedure=proc_name,
                        variable=variable,
                        certainty="dispatch_table",
                        resolved_targets=[],
                        condition=next(
                            a["value"]
                            for a in assignments
                            if a["type"] == "table_lookup"
                        ),
                        line=line_num,
                    )
                )
            else:
                refs.append(
                    VariableCallRef(
                        procedure=proc_name,
                        variable=variable,
                        certainty="opaque",
                        resolved_targets=[],
                        line=line_num,
                    )
                )

        return refs

    # -------------------------------------------------------------------
    # Shared helpers
    # -------------------------------------------------------------------

    def _trace_variable_value(
        self, variable: str, lines: list[str], before_line: int
    ) -> Optional[str]:
        """Walk backward to find the most recent SET @var = value."""
        for i in range(before_line - 1, -1, -1):
            line = lines[i].strip()
            # SET @var = 'value'
            match = re.search(
                rf"(?i)SET\s+{re.escape(variable)}\s*=\s*(.+)",
                line,
            )
            if match:
                return match.group(1).rstrip(";").strip()
            # SELECT @var = col FROM ...
            match = re.search(
                rf"(?i)SELECT\s+{re.escape(variable)}\s*=\s*(.+)",
                line,
            )
            if match:
                return match.group(1).rstrip(";").strip()
        return None

    def _trace_all_assignments(
        self, variable: str, lines: list[str], before_line: int
    ) -> list[dict]:
        """Find all assignments to a variable before the given line.

        Returns a list of dicts with keys: type (literal|table_lookup|expression),
        value, condition (optional).
        """
        assignments: list[dict] = []
        in_if_block = False
        current_condition = ""

        for i in range(before_line - 1, -1, -1):
            line = lines[i].strip()
            upper = line.upper()

            # Track IF context
            if upper.startswith("ELSE"):
                in_if_block = True
                current_condition = "ELSE"
            elif upper.startswith("IF "):
                in_if_block = True
                current_condition = line

            # SET @var = 'literal'
            set_match = re.search(
                rf"(?i)SET\s+{re.escape(variable)}\s*=\s*(.+)",
                line,
            )
            if set_match:
                value = set_match.group(1).rstrip(";").strip()
                atype = self._classify_assignment_value(value)
                assignments.append(
                    {
                        "type": atype,
                        "value": value.strip("'").strip("N'"),
                        "condition": current_condition if in_if_block else "",
                    }
                )
                if not in_if_block:
                    break  # Single unconditional assignment — done

            # SELECT @var = col FROM table
            select_match = re.search(
                rf"(?i)SELECT\s+{re.escape(variable)}\s*=\s*(\w+)\s+FROM\s+(\w+)",
                line,
            )
            if select_match:
                col = select_match.group(1)
                table = select_match.group(2)
                assignments.append(
                    {
                        "type": "table_lookup",
                        "value": f"{table}.{col}",
                        "condition": current_condition if in_if_block else "",
                    }
                )
                if not in_if_block:
                    break

        return assignments

    def _classify_assignment_value(self, value: str) -> str:
        """Classify a SET value as literal, expression, or other."""
        stripped = value.strip()
        if stripped.startswith(("'", "N'")):
            return "literal"
        if "+" in stripped:
            return "expression"
        return "expression"

    def _classify_operation(self, line: str, match_start: int) -> str:
        """Determine the SQL operation type from context before the match."""
        prefix = line[:match_start].strip().upper()
        if "EXEC" in prefix:
            return "EXEC"
        if "INSERT" in prefix:
            return "INSERT"
        if "UPDATE" in prefix:
            return "UPDATE"
        if "DELETE" in prefix:
            return "DELETE"
        if "FROM" in prefix or "JOIN" in prefix:
            return "SELECT"
        return "UNKNOWN"
