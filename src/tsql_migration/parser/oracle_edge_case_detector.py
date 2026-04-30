"""Oracle PL/SQL edge case detector.

Mirrors the interface of EdgeCaseDetector but targets Oracle-specific patterns.

Three tough scenarios (Oracle equivalents of the T-SQL ones):

1. Database Links  (``table@link_name``)
   Oracle's equivalent of T-SQL linked servers.
   Pattern: any_table@link_name  or  schema.table@link_name
   These become ``LinkedServerRef`` with server=link_name, database=remote schema.

2. Dynamic SQL  (``EXECUTE IMMEDIATE``)
   Oracle's equivalent of ``EXEC(@sql)`` / ``sp_executesql``.
   Four tiers:
     simple      — EXECUTE IMMEDIATE 'literal string'
     templated   — EXECUTE IMMEDIATE v_sql USING bind_var  (parameterised)
     concatenated — EXECUTE IMMEDIATE 'prefix' || v_val || 'suffix'
     opaque      — EXECUTE IMMEDIATE v_sql  (no USING, variable not resolvable)
   Also detects DBMS_SQL usage (always opaque).

3. Dynamic Dispatch  (``EXECUTE IMMEDIATE v_proc_name || '()'``)
   Oracle equivalent of T-SQL variable procedure calls (EXEC @var).
   Detected when the EXECUTE IMMEDIATE expression looks like a procedure dispatch:
   constructing a call string from a variable that holds a procedure name.
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


class OracleEdgeCaseDetector:
    """Detects Oracle-specific edge cases in PL/SQL procedures.

    Public interface identical to ``EdgeCaseDetector`` so pipeline nodes
    can swap between them based on ``config.sql_dialect``.
    """

    def detect(self, procedure: ProcedureMetadata) -> EdgeCaseReport:
        """Analyse one procedure for all three scenarios."""
        sql = procedure.raw_sql
        proc = procedure.procedure_name

        db_links = self._detect_database_links(proc, sql)
        dynamic_sql = self._detect_dynamic_sql(proc, sql)
        variable_calls = self._detect_variable_dispatch(proc, sql)

        unresolved = (
            len(db_links)
            + len([d for d in dynamic_sql if d.tier in ("concatenated", "opaque")])
            + len([v for v in variable_calls if v.certainty in ("dispatch_table", "opaque")])
        )

        return EdgeCaseReport(
            linked_servers=db_links,
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

    # ------------------------------------------------------------------
    # 1. Database links  (table@link_name)
    # ------------------------------------------------------------------

    def _detect_database_links(
        self, proc_name: str, sql: str
    ) -> list[LinkedServerRef]:
        """Detect Oracle database link references: table@link or schema.table@link."""
        refs: list[LinkedServerRef] = []

        # Pattern: [schema.]table@link_name inside FROM/JOIN/INSERT/UPDATE/DELETE/EXEC
        pattern = re.compile(
            r"(?i)(?:FROM|JOIN|INTO|UPDATE|DELETE\s+FROM|EXECUTE\s+IMMEDIATE)\s+"
            r"(?:(\w+)\.)?(\w+)@(\w+)",
        )

        for line_num, line in enumerate(sql.split("\n"), 1):
            for m in pattern.finditer(line):
                remote_schema = m.group(1) or ""
                obj_name = m.group(2)
                link_name = m.group(3)
                operation = self._classify_operation(line, m.start())

                refs.append(
                    LinkedServerRef(
                        procedure=proc_name,
                        server=link_name,
                        database=remote_schema or link_name,
                        remote_object=f"{remote_schema}.{obj_name}" if remote_schema else obj_name,
                        operation=operation,
                        line=line_num,
                    )
                )

        # Also catch any remaining @link patterns not preceded by DML keywords
        bare_pattern = re.compile(r"(\w+)@(\w+)")
        existing_lines = {r.line for r in refs}
        for line_num, line in enumerate(sql.split("\n"), 1):
            if line_num in existing_lines:
                continue
            if line.strip().startswith("--"):
                continue
            for m in bare_pattern.finditer(line):
                obj_name = m.group(1)
                link_name = m.group(2)
                refs.append(
                    LinkedServerRef(
                        procedure=proc_name,
                        server=link_name,
                        database=link_name,
                        remote_object=obj_name,
                        operation="REFERENCE",
                        line=line_num,
                    )
                )

        return refs

    # ------------------------------------------------------------------
    # 2. Dynamic SQL  (EXECUTE IMMEDIATE)
    # ------------------------------------------------------------------

    def _detect_dynamic_sql(
        self, proc_name: str, sql: str
    ) -> list[DynamicSqlRef]:
        """Detect EXECUTE IMMEDIATE and DBMS_SQL calls."""
        refs: list[DynamicSqlRef] = []
        lines = sql.split("\n")

        for line_num, line in enumerate(lines, 1):
            stripped = line.strip()
            upper = stripped.upper()

            # EXECUTE IMMEDIATE <expr> [USING ...]
            ei_match = re.search(
                r"(?i)\bEXECUTE\s+IMMEDIATE\s+(.+?)(?:\s+(?:INTO|USING|RETURNING)\b|;|$)",
                stripped,
            )
            if ei_match:
                expr = ei_match.group(1).strip()
                has_using = bool(re.search(r"(?i)\bUSING\b", stripped))
                tier, resolved = self._classify_ei_expression(expr, lines, line_num, has_using)
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

            # DBMS_SQL package calls — always opaque
            if re.search(r"(?i)\bDBMS_SQL\s*\.", stripped):
                refs.append(
                    DynamicSqlRef(
                        procedure=proc_name,
                        tier="opaque",
                        expression=stripped,
                        line=line_num,
                        resolved_sql=None,
                    )
                )

        return refs

    def _classify_ei_expression(
        self,
        expression: str,
        lines: list[str],
        current_line: int,
        has_using: bool,
    ) -> tuple[str, Optional[str]]:
        """Classify an EXECUTE IMMEDIATE expression into a tier."""
        expr = expression.strip()

        # Tier 1: Simple — plain string literal
        if re.match(r"^'[^']*'$", expr) or re.match(r'^"[^"]*"$', expr):
            return "simple", expr.strip("'\"")

        # Tier 2: Templated — variable with USING binds
        if has_using and expr.startswith(("v_", "l_", "c_")):
            traced = self._trace_variable(expr, lines, current_line - 1)
            if traced:
                return "templated", traced
            return "templated", None

        # Tier 3: Concatenated — || string building
        if "||" in expr:
            return "concatenated", expr

        # Variable without USING — trace it
        if re.match(r"^\w+$", expr):
            traced = self._trace_variable(expr, lines, current_line - 1)
            if traced:
                if "||" in traced:
                    return "concatenated", traced
                return "simple", traced.strip("'\"")
            return "opaque", None

        return "opaque", None

    # ------------------------------------------------------------------
    # 3. Dynamic procedure dispatch  (EXECUTE IMMEDIATE proc_call_string)
    # ------------------------------------------------------------------

    def _detect_variable_dispatch(
        self, proc_name: str, sql: str
    ) -> list[VariableCallRef]:
        """Detect EXECUTE IMMEDIATE used to call procedures dynamically.

        Pattern: EXECUTE IMMEDIATE v_proc_name || '(args)'
                 EXECUTE IMMEDIATE 'BEGIN ' || v_proc || '(args); END;'
        """
        refs: list[VariableCallRef] = []
        lines = sql.split("\n")

        for line_num, line in enumerate(lines, 1):
            stripped = line.strip()
            m = re.search(
                r"(?i)\bEXECUTE\s+IMMEDIATE\s+(.+)",
                stripped,
            )
            if not m:
                continue

            expr = m.group(1).strip().rstrip(";")

            # Only consider as variable dispatch when it looks like a procedure call:
            # e.g. 'BEGIN ' || v_proc || '(); END;'  or  v_proc_name || '(arg)'
            if not re.search(r"(?i)\bBEGIN\b|\(\s*\)", expr):
                continue

            variable = None
            # Extract the variable being used as the proc name
            var_m = re.search(r"\b(v_\w+|l_\w+|p_\w+)\b", expr)
            if var_m:
                variable = var_m.group(1)

            if not variable:
                continue

            assignments = self._trace_all_assignments(variable, lines, line_num - 1)

            if not assignments:
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
                            a["value"] for a in assignments if a["type"] == "table_lookup"
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

    # ------------------------------------------------------------------
    # Shared helpers
    # ------------------------------------------------------------------

    def _trace_variable(
        self, variable: str, lines: list[str], before_line: int
    ) -> Optional[str]:
        """Walk backward to find the most recent assignment to *variable*."""
        for i in range(before_line - 1, -1, -1):
            line = lines[i].strip()
            # Oracle assignment:  v_var := 'value';
            m = re.search(
                rf"(?i){re.escape(variable)}\s*:=\s*(.+?)\s*;?\s*$",
                line,
            )
            if m:
                return m.group(1).strip()
            # SELECT INTO:  SELECT col INTO v_var FROM ...
            m = re.search(
                rf"(?i)SELECT\s+(.+?)\s+INTO\s+{re.escape(variable)}\b",
                line,
            )
            if m:
                return m.group(1).strip()
        return None

    def _trace_all_assignments(
        self, variable: str, lines: list[str], before_line: int
    ) -> list[dict]:
        """Find all assignments to *variable* before the given line."""
        assignments: list[dict] = []
        in_if_block = False
        current_condition = ""

        for i in range(before_line - 1, -1, -1):
            line = lines[i].strip()
            upper = line.upper()

            if upper.startswith("ELSE"):
                in_if_block = True
                current_condition = "ELSE"
            elif upper.startswith("IF "):
                in_if_block = True
                current_condition = line

            # Oracle assignment operator :=
            assign_m = re.search(
                rf"(?i){re.escape(variable)}\s*:=\s*(.+?)\s*;?\s*$",
                line,
            )
            if assign_m:
                value = assign_m.group(1).strip()
                atype = "literal" if re.match(r"^'[^']*'$", value) else "expression"
                assignments.append({
                    "type": atype,
                    "value": value.strip("'"),
                    "condition": current_condition if in_if_block else "",
                })
                if not in_if_block:
                    break

            # SELECT INTO lookup
            sel_m = re.search(
                rf"(?i)SELECT\s+(\w+)\s+INTO\s+{re.escape(variable)}\s+FROM\s+(\w+)",
                line,
            )
            if sel_m:
                assignments.append({
                    "type": "table_lookup",
                    "value": f"{sel_m.group(2)}.{sel_m.group(1)}",
                    "condition": current_condition if in_if_block else "",
                })
                if not in_if_block:
                    break

        return assignments

    def _classify_operation(self, line: str, match_start: int) -> str:
        prefix = line[:match_start].strip().upper()
        if "EXECUTE" in prefix:
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
