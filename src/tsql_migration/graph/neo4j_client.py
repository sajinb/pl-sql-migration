"""Neo4j client for storing and querying the procedure call graph.

Supports two isolation strategies to avoid corrupting existing Neo4j data:

1. **Dedicated database** (Enterprise/Aura): Set `neo4j_database` in config.
   All queries run against that database only.

2. **Namespaced labels** (Community Edition): Set `neo4j_label_prefix` in config.
   All node labels are prefixed (e.g. `Mig_Procedure` instead of `Procedure`).
   Only prefixed nodes are touched by clear_graph.

Data model:
  Core nodes:   {prefix}Procedure, {prefix}Table, {prefix}TempTable
  Edge nodes:   {prefix}LinkedServer, {prefix}DynamicCall, {prefix}DispatchTable, {prefix}UnresolvedCall
  Relationships: CALLS, USES_TABLE, USES_TEMP_TABLE, CALLS_REMOTE,
                 READS_REMOTE, USES_DYNAMIC_SQL, DYNAMIC_DISPATCH, UNRESOLVED_CALL
"""

from __future__ import annotations

from typing import Optional

from neo4j import GraphDatabase, Driver

from tsql_migration.state import (
    EdgeCaseReport,
    ProcedureMetadata,
)


class Neo4jClient:
    """Manages the procedure call graph in Neo4j with database/label isolation."""

    def __init__(
        self,
        uri: str,
        user: str,
        password: str,
        database: str = "",
        label_prefix: str = "Mig_",
    ) -> None:
        self._driver: Driver = GraphDatabase.driver(uri, auth=(user, password))
        self._database: Optional[str] = database or None
        self._prefix = label_prefix

        # Prefixed label names
        self.L_PROCEDURE = f"{self._prefix}Procedure"
        self.L_TABLE = f"{self._prefix}Table"
        self.L_TEMP_TABLE = f"{self._prefix}TempTable"
        self.L_LINKED_SERVER = f"{self._prefix}LinkedServer"
        self.L_DYNAMIC_CALL = f"{self._prefix}DynamicCall"
        self.L_DISPATCH_TABLE = f"{self._prefix}DispatchTable"
        self.L_UNRESOLVED_CALL = f"{self._prefix}UnresolvedCall"

    def close(self) -> None:
        self._driver.close()

    def _run(self, query: str, **params):
        """Execute a Cypher query against the configured database."""
        return self._driver.execute_query(query, parameters_=params, database_=self._database)

    # -------------------------------------------------------------------
    # Setup
    # -------------------------------------------------------------------

    def clear_graph(self) -> None:
        """Delete only migration-related nodes (identified by label prefix).

        This is safe to run even when the Neo4j instance has other data —
        only nodes with the configured label prefix are deleted.
        """
        self._run(
            f"""
            MATCH (n)
            WHERE any(label IN labels(n) WHERE label STARTS WITH $prefix)
            DETACH DELETE n
            """,
            prefix=self._prefix,
        )

    def create_constraints(self) -> None:
        """Create uniqueness constraints for core node types."""
        constraints = [
            f"CREATE CONSTRAINT IF NOT EXISTS FOR (p:{self.L_PROCEDURE}) REQUIRE p.name IS UNIQUE",
            f"CREATE CONSTRAINT IF NOT EXISTS FOR (t:{self.L_TABLE}) REQUIRE t.name IS UNIQUE",
            f"CREATE CONSTRAINT IF NOT EXISTS FOR (tt:{self.L_TEMP_TABLE}) REQUIRE tt.name IS UNIQUE",
        ]
        for cypher in constraints:
            try:
                self._run(cypher)
            except Exception:
                pass  # constraint may already exist

    # -------------------------------------------------------------------
    # Populate graph from parsed metadata
    # -------------------------------------------------------------------

    def add_procedure(self, proc: ProcedureMetadata) -> None:
        """Add a procedure node with its table/temp table relationships."""
        LP = self.L_PROCEDURE
        LT = self.L_TABLE
        LTT = self.L_TEMP_TABLE

        self._run(
            f"""
            MERGE (p:{LP} {{name: $name}})
            SET p.schema = $schema,
                p.lineCount = $lineCount,
                p.paramCount = $paramCount,
                p.sourceFile = $sourceFile
            """,
            name=proc.procedure_name,
            schema=proc.schema_name,
            lineCount=proc.total_line_count,
            paramCount=len(proc.parameters),
            sourceFile=proc.source_file,
        )

        for table in proc.referenced_tables:
            self._run(
                f"""
                MERGE (t:{LT} {{name: $table}})
                WITH t
                MERGE (p:{LP} {{name: $proc}})
                MERGE (p)-[:USES_TABLE]->(t)
                """,
                table=table,
                proc=proc.procedure_name,
            )

        for temp in proc.temp_tables:
            self._run(
                f"""
                MERGE (tt:{LTT} {{name: $name}})
                SET tt.stagingName = $stagingName,
                    tt.isGlobal = $isGlobal
                WITH tt
                MERGE (p:{LP} {{name: $proc}})
                MERGE (p)-[:USES_TEMP_TABLE]->(tt)
                """,
                name=temp.original_name,
                stagingName=temp.staging_table_name,
                isGlobal=temp.is_global_temp,
                proc=proc.procedure_name,
            )

        for called in proc.called_procedures:
            self._run(
                f"""
                MERGE (callee:{LP} {{name: $callee}})
                WITH callee
                MERGE (caller:{LP} {{name: $caller}})
                MERGE (caller)-[:CALLS {{certainty: "resolved"}}]->(callee)
                """,
                callee=called,
                caller=proc.procedure_name,
            )

    def add_edge_cases(self, report: EdgeCaseReport) -> None:
        """Add edge case nodes and relationships to the graph."""
        LP = self.L_PROCEDURE
        LLS = self.L_LINKED_SERVER
        LDC = self.L_DYNAMIC_CALL
        LDT = self.L_DISPATCH_TABLE
        LUC = self.L_UNRESOLVED_CALL

        for ref in report.linked_servers:
            self._run(
                f"""
                MERGE (ls:{LLS} {{name: $server}})
                SET ls.database = $database
                WITH ls
                MERGE (p:{LP} {{name: $proc}})
                MERGE (p)-[:CALLS_REMOTE {{
                    operation: $operation,
                    remoteObject: $remoteObject,
                    line: $line
                }}]->(ls)
                """,
                server=ref.server,
                database=ref.database,
                proc=ref.procedure,
                operation=ref.operation,
                remoteObject=ref.remote_object,
                line=ref.line,
            )

        for ref in report.dynamic_sql_calls:
            self._run(
                f"""
                CREATE (dc:{LDC} {{
                    expression: $expression,
                    tier: $tier,
                    line: $line,
                    resolvedSql: $resolvedSql
                }})
                WITH dc
                MERGE (p:{LP} {{name: $proc}})
                MERGE (p)-[:USES_DYNAMIC_SQL]->(dc)
                """,
                expression=ref.expression,
                tier=ref.tier,
                line=ref.line,
                resolvedSql=ref.resolved_sql or "",
                proc=ref.procedure,
            )

        for ref in report.variable_calls:
            if ref.certainty == "resolved" and ref.resolved_targets:
                for target in ref.resolved_targets:
                    self._run(
                        f"""
                        MERGE (callee:{LP} {{name: $callee}})
                        WITH callee
                        MERGE (caller:{LP} {{name: $caller}})
                        MERGE (caller)-[:CALLS {{
                            type: "variable",
                            certainty: "resolved",
                            variable: $variable
                        }}]->(callee)
                        """,
                        callee=target,
                        caller=ref.procedure,
                        variable=ref.variable,
                    )
            elif ref.certainty == "conditional" and ref.resolved_targets:
                for target in ref.resolved_targets:
                    self._run(
                        f"""
                        MERGE (callee:{LP} {{name: $callee}})
                        WITH callee
                        MERGE (caller:{LP} {{name: $caller}})
                        MERGE (caller)-[:CALLS {{
                            type: "variable",
                            certainty: "conditional",
                            variable: $variable,
                            condition: $condition
                        }}]->(callee)
                        """,
                        callee=target,
                        caller=ref.procedure,
                        variable=ref.variable,
                        condition=ref.condition or "",
                    )
            elif ref.certainty == "dispatch_table":
                self._run(
                    f"""
                    CREATE (dt:{LDT} {{
                        name: $tableRef,
                        variable: $variable,
                        line: $line
                    }})
                    WITH dt
                    MERGE (p:{LP} {{name: $proc}})
                    MERGE (p)-[:DYNAMIC_DISPATCH]->(dt)
                    """,
                    tableRef=ref.condition or "unknown",
                    variable=ref.variable,
                    line=ref.line,
                    proc=ref.procedure,
                )
            else:
                self._run(
                    f"""
                    CREATE (uc:{LUC} {{
                        variable: $variable,
                        line: $line,
                        context: $context
                    }})
                    WITH uc
                    MERGE (p:{LP} {{name: $proc}})
                    MERGE (p)-[:UNRESOLVED_CALL]->(uc)
                    """,
                    variable=ref.variable,
                    line=ref.line,
                    context=ref.condition or "",
                    proc=ref.procedure,
                )

    # -------------------------------------------------------------------
    # Queries
    # -------------------------------------------------------------------

    def get_leaf_procedures(self) -> list[str]:
        """Find procedures that don't call any other procedure."""
        LP = self.L_PROCEDURE
        records, _, _ = self._run(
            f"""
            MATCH (p:{LP})
            WHERE NOT (p)-[:CALLS]->()
            RETURN p.name AS name
            ORDER BY p.name
            """
        )
        return [r["name"] for r in records]

    def get_migration_order(self) -> list[str]:
        """Topological sort — leaf procedures first, then their callers."""
        LP = self.L_PROCEDURE

        records, _, _ = self._run(
            f"""
            MATCH (p:{LP})
            OPTIONAL MATCH (p)-[:CALLS]->(callee:{LP})
            RETURN p.name AS name, count(callee) AS outDegree
            """
        )
        out_degree: dict[str, int] = {r["name"]: r["outDegree"] for r in records}

        records, _, _ = self._run(
            f"""
            MATCH (caller:{LP})-[:CALLS]->(callee:{LP})
            RETURN caller.name AS caller, callee.name AS callee
            """
        )
        reverse_adj: dict[str, list[str]] = {}
        for r in records:
            reverse_adj.setdefault(r["callee"], []).append(r["caller"])

        from collections import deque

        queue: deque[str] = deque()
        for name, deg in out_degree.items():
            if deg == 0:
                queue.append(name)

        order: list[str] = []
        while queue:
            proc = queue.popleft()
            order.append(proc)
            for caller in reverse_adj.get(proc, []):
                out_degree[caller] -= 1
                if out_degree[caller] == 0:
                    queue.append(caller)

        # Append any remaining (cycles)
        for name in out_degree:
            if name not in order:
                order.append(name)

        return order

    def detect_cycles(self) -> list[list[str]]:
        """Detect circular call dependencies."""
        LP = self.L_PROCEDURE
        records, _, _ = self._run(
            f"""
            MATCH path = (p:{LP})-[:CALLS*]->(p)
            RETURN [n IN nodes(path) | n.name] AS cycle
            LIMIT 20
            """
        )
        seen: set[str] = set()
        cycles: list[list[str]] = []
        for r in records:
            key = "->".join(sorted(r["cycle"]))
            if key not in seen:
                seen.add(key)
                cycles.append(r["cycle"])
        return cycles

    def get_external_dependencies(self) -> list[str]:
        """Find procedures that are called but have no source (no lineCount)."""
        LP = self.L_PROCEDURE
        records, _, _ = self._run(
            f"""
            MATCH (p:{LP})
            WHERE p.lineCount IS NULL OR p.lineCount = 0
            RETURN p.name AS name
            ORDER BY p.name
            """
        )
        return [r["name"] for r in records]

    def get_linked_server_report(self) -> list[dict]:
        """Get all linked server references."""
        LP = self.L_PROCEDURE
        LLS = self.L_LINKED_SERVER
        records, _, _ = self._run(
            f"""
            MATCH (p:{LP})-[r:CALLS_REMOTE|READS_REMOTE]->(ls:{LLS})
            RETURN p.name AS procedure, ls.name AS server,
                   ls.database AS database, r.operation AS operation,
                   r.remoteObject AS remoteObject, r.line AS line
            ORDER BY p.name
            """
        )
        return [dict(r) for r in records]

    def get_unresolved_calls_report(self) -> list[dict]:
        """Get all unresolved/dynamic calls."""
        LP = self.L_PROCEDURE
        records, _, _ = self._run(
            f"""
            MATCH (p:{LP})-[r:UNRESOLVED_CALL|DYNAMIC_DISPATCH|USES_DYNAMIC_SQL]->(target)
            RETURN p.name AS procedure, labels(target)[0] AS targetType,
                   properties(target) AS details, type(r) AS relType
            ORDER BY p.name
            """
        )
        return [dict(r) for r in records]

    def get_all_referenced_tables(self) -> list[str]:
        """Get all unique table names referenced by any procedure."""
        LP = self.L_PROCEDURE
        LT = self.L_TABLE
        records, _, _ = self._run(
            f"""
            MATCH (:{LP})-[:USES_TABLE]->(t:{LT})
            RETURN DISTINCT t.name AS name
            ORDER BY t.name
            """
        )
        return [r["name"] for r in records]
