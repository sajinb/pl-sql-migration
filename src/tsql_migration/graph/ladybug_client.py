"""LadybugDB client for storing and querying the procedure call graph.

Embedded alternative to Neo4jClient — no server required.
The graph is stored in a local directory (default: .ladybug_db/).

Data model mirrors Neo4jClient:
  Core nodes:   Procedure, Table, TempTable
  Edge nodes:   LinkedServer, DynamicCall, DispatchTable, UnresolvedCall
  Relationships: CALLS, USES_TABLE, USES_TEMP_TABLE, CALLS_REMOTE,
                 USES_DYNAMIC_SQL, DYNAMIC_DISPATCH, UNRESOLVED_CALL

Install:  pip install real-ladybug
Import:   import real_ladybug as lb

Key differences from Neo4jClient:
  - No server, URI, or credentials — just a local directory path
  - Schema must be created upfront via setup_schema() before any writes
  - detect_cycles() uses Python DFS (variable-length Cypher paths may vary by version)
  - Result rows are positional lists, not named dicts
"""

from __future__ import annotations

from collections import deque
from pathlib import Path

import real_ladybug as lb

from tsql_migration.state import EdgeCaseReport, ProcedureMetadata


class LadybugClient:
    """Manages the procedure call graph using LadybugDB (embedded, file-based).

    Drop-in interface equivalent to Neo4jClient — same public methods,
    same return types. No server or credentials required.

    Example:
        client = LadybugClient(db_path=".ladybug_db")
        client.setup_schema()
        client.clear_graph()
        client.add_procedure(proc)
        order = client.get_migration_order()
        client.close()
    """

    def __init__(self, db_path: str = ".ladybug_db") -> None:
        Path(db_path).mkdir(parents=True, exist_ok=True)
        self._db = lb.Database(db_path)
        self._conn = lb.Connection(self._db)

    def close(self) -> None:
        self._conn.close()

    def _run(self, query: str, params: dict | None = None) -> list:
        """Execute a Cypher query and return results as a list of positional rows."""
        result = self._conn.execute(query, params or {})
        rows = []
        while result.has_next():
            rows.append(result.get_next())
        return rows

    # -------------------------------------------------------------------
    # Schema setup  (call once before any writes)
    # -------------------------------------------------------------------

    def setup_schema(self) -> None:
        """Create node and relationship tables. Safe to call multiple times."""
        node_tables = [
            """
            CREATE NODE TABLE IF NOT EXISTS Procedure(
                name       STRING PRIMARY KEY,
                schema     STRING,
                lineCount  INT64,
                paramCount INT64,
                sourceFile STRING
            )
            """,
            """
            CREATE NODE TABLE IF NOT EXISTS Table(
                name STRING PRIMARY KEY
            )
            """,
            """
            CREATE NODE TABLE IF NOT EXISTS TempTable(
                name        STRING PRIMARY KEY,
                stagingName STRING,
                isGlobal    BOOLEAN
            )
            """,
            """
            CREATE NODE TABLE IF NOT EXISTS LinkedServer(
                name     STRING PRIMARY KEY,
                database STRING
            )
            """,
            # DynamicCall / DispatchTable / UnresolvedCall have no natural key —
            # SERIAL gives each node a unique auto-increment id.
            """
            CREATE NODE TABLE IF NOT EXISTS DynamicCall(
                id          SERIAL PRIMARY KEY,
                expression  STRING,
                tier        STRING,
                line        INT64,
                resolvedSql STRING
            )
            """,
            """
            CREATE NODE TABLE IF NOT EXISTS DispatchTable(
                id       SERIAL PRIMARY KEY,
                tableRef STRING,
                variable STRING,
                line     INT64
            )
            """,
            """
            CREATE NODE TABLE IF NOT EXISTS UnresolvedCall(
                id       SERIAL PRIMARY KEY,
                variable STRING,
                line     INT64,
                context  STRING
            )
            """,
        ]

        rel_tables = [
            """
            CREATE REL TABLE IF NOT EXISTS CALLS(
                FROM Procedure TO Procedure,
                type      STRING,
                certainty STRING,
                variable  STRING,
                condition STRING
            )
            """,
            "CREATE REL TABLE IF NOT EXISTS USES_TABLE(FROM Procedure TO Table)",
            "CREATE REL TABLE IF NOT EXISTS USES_TEMP_TABLE(FROM Procedure TO TempTable)",
            """
            CREATE REL TABLE IF NOT EXISTS CALLS_REMOTE(
                FROM Procedure TO LinkedServer,
                operation    STRING,
                remoteObject STRING,
                line         INT64
            )
            """,
            "CREATE REL TABLE IF NOT EXISTS USES_DYNAMIC_SQL(FROM Procedure TO DynamicCall)",
            "CREATE REL TABLE IF NOT EXISTS DYNAMIC_DISPATCH(FROM Procedure TO DispatchTable)",
            "CREATE REL TABLE IF NOT EXISTS UNRESOLVED_CALL(FROM Procedure TO UnresolvedCall)",
        ]

        for ddl in node_tables + rel_tables:
            try:
                self._conn.execute(ddl)
            except Exception:
                pass  # already exists

    # -------------------------------------------------------------------
    # Teardown
    # -------------------------------------------------------------------

    def clear_graph(self) -> None:
        """Delete all migration graph data (relationships first, then nodes)."""
        for rel in [
            "CALLS", "USES_TABLE", "USES_TEMP_TABLE", "CALLS_REMOTE",
            "USES_DYNAMIC_SQL", "DYNAMIC_DISPATCH", "UNRESOLVED_CALL",
        ]:
            try:
                self._conn.execute(f"MATCH ()-[r:{rel}]->() DELETE r")
            except Exception:
                pass

        for node in [
            "DynamicCall", "DispatchTable", "UnresolvedCall",
            "LinkedServer", "TempTable", "Table", "Procedure",
        ]:
            try:
                self._conn.execute(f"MATCH (n:{node}) DELETE n")
            except Exception:
                pass

    # -------------------------------------------------------------------
    # Populate graph from parsed metadata
    # -------------------------------------------------------------------

    def add_procedure(self, proc: ProcedureMetadata) -> None:
        """Add a procedure node with its table/temp table relationships."""
        self._conn.execute(
            """
            MERGE (p:Procedure {name: $name})
            SET p.schema = $schema,
                p.lineCount = $lineCount,
                p.paramCount = $paramCount,
                p.sourceFile = $sourceFile
            """,
            {
                "name": proc.procedure_name,
                "schema": proc.schema_name,
                "lineCount": proc.total_line_count,
                "paramCount": len(proc.parameters),
                "sourceFile": proc.source_file,
            },
        )

        for table in proc.referenced_tables:
            self._conn.execute(
                """
                MERGE (t:Table {name: $table})
                WITH t
                MERGE (p:Procedure {name: $proc})
                MERGE (p)-[:USES_TABLE]->(t)
                """,
                {"table": table, "proc": proc.procedure_name},
            )

        for temp in proc.temp_tables:
            self._conn.execute(
                """
                MERGE (tt:TempTable {name: $name})
                SET tt.stagingName = $stagingName,
                    tt.isGlobal = $isGlobal
                WITH tt
                MERGE (p:Procedure {name: $proc})
                MERGE (p)-[:USES_TEMP_TABLE]->(tt)
                """,
                {
                    "name": temp.original_name,
                    "stagingName": temp.staging_table_name,
                    "isGlobal": temp.is_global_temp,
                    "proc": proc.procedure_name,
                },
            )

        for called in proc.called_procedures:
            self._conn.execute(
                """
                MERGE (callee:Procedure {name: $callee})
                WITH callee
                MERGE (caller:Procedure {name: $caller})
                MERGE (caller)-[:CALLS {certainty: "resolved"}]->(callee)
                """,
                {"callee": called, "caller": proc.procedure_name},
            )

    def add_edge_cases(self, report: EdgeCaseReport) -> None:
        """Add edge case nodes and relationships to the graph."""
        for ref in report.linked_servers:
            self._conn.execute(
                """
                MERGE (ls:LinkedServer {name: $server})
                SET ls.database = $database
                WITH ls
                MERGE (p:Procedure {name: $proc})
                MERGE (p)-[:CALLS_REMOTE {
                    operation: $operation,
                    remoteObject: $remoteObject,
                    line: $line
                }]->(ls)
                """,
                {
                    "server": ref.server,
                    "database": ref.database,
                    "proc": ref.procedure,
                    "operation": ref.operation,
                    "remoteObject": ref.remote_object,
                    "line": ref.line,
                },
            )

        for ref in report.dynamic_sql_calls:
            self._conn.execute(
                """
                CREATE (dc:DynamicCall {
                    expression: $expression,
                    tier: $tier,
                    line: $line,
                    resolvedSql: $resolvedSql
                })
                WITH dc
                MATCH (p:Procedure {name: $proc})
                CREATE (p)-[:USES_DYNAMIC_SQL]->(dc)
                """,
                {
                    "expression": ref.expression,
                    "tier": ref.tier,
                    "line": ref.line,
                    "resolvedSql": ref.resolved_sql or "",
                    "proc": ref.procedure,
                },
            )

        for ref in report.variable_calls:
            if ref.certainty in ("resolved", "conditional") and ref.resolved_targets:
                for target in ref.resolved_targets:
                    self._conn.execute(
                        """
                        MERGE (callee:Procedure {name: $callee})
                        WITH callee
                        MERGE (caller:Procedure {name: $caller})
                        MERGE (caller)-[:CALLS {
                            type: "variable",
                            certainty: $certainty,
                            variable: $variable,
                            condition: $condition
                        }]->(callee)
                        """,
                        {
                            "callee": target,
                            "caller": ref.procedure,
                            "certainty": ref.certainty,
                            "variable": ref.variable,
                            "condition": ref.condition or "",
                        },
                    )
            elif ref.certainty == "dispatch_table":
                self._conn.execute(
                    """
                    CREATE (dt:DispatchTable {
                        tableRef: $tableRef,
                        variable: $variable,
                        line: $line
                    })
                    WITH dt
                    MATCH (p:Procedure {name: $proc})
                    CREATE (p)-[:DYNAMIC_DISPATCH]->(dt)
                    """,
                    {
                        "tableRef": ref.condition or "unknown",
                        "variable": ref.variable,
                        "line": ref.line,
                        "proc": ref.procedure,
                    },
                )
            else:
                self._conn.execute(
                    """
                    CREATE (uc:UnresolvedCall {
                        variable: $variable,
                        line: $line,
                        context: $context
                    })
                    WITH uc
                    MATCH (p:Procedure {name: $proc})
                    CREATE (p)-[:UNRESOLVED_CALL]->(uc)
                    """,
                    {
                        "variable": ref.variable,
                        "line": ref.line,
                        "context": ref.condition or "",
                        "proc": ref.procedure,
                    },
                )

    # -------------------------------------------------------------------
    # Queries
    # -------------------------------------------------------------------

    def get_leaf_procedures(self) -> list[str]:
        """Find procedures that don't call any other procedure."""
        rows = self._run(
            """
            MATCH (p:Procedure)
            WHERE NOT (p)-[:CALLS]->()
            RETURN p.name AS name
            ORDER BY p.name
            """
        )
        return [r[0] for r in rows]

    def get_migration_order(self) -> list[str]:
        """Topological sort — leaf procedures first, then their callers (Kahn's algorithm)."""
        rows = self._run(
            """
            MATCH (p:Procedure)
            OPTIONAL MATCH (p)-[:CALLS]->(callee:Procedure)
            RETURN p.name AS name, count(callee) AS outDegree
            """
        )
        out_degree: dict[str, int] = {r[0]: r[1] for r in rows}

        rows = self._run(
            """
            MATCH (caller:Procedure)-[:CALLS]->(callee:Procedure)
            RETURN caller.name AS caller, callee.name AS callee
            """
        )
        reverse_adj: dict[str, list[str]] = {}
        for r in rows:
            reverse_adj.setdefault(r[1], []).append(r[0])

        queue: deque[str] = deque(name for name, deg in out_degree.items() if deg == 0)
        order: list[str] = []
        while queue:
            proc = queue.popleft()
            order.append(proc)
            for caller in reverse_adj.get(proc, []):
                out_degree[caller] -= 1
                if out_degree[caller] == 0:
                    queue.append(caller)

        # Append any remaining nodes that are part of cycles
        for name in out_degree:
            if name not in order:
                order.append(name)

        return order

    def detect_cycles(self) -> list[list[str]]:
        """Detect circular call dependencies using Python DFS.

        LadybugDB variable-length path support (`[:CALLS*]`) may vary by version,
        so cycle detection is done in Python for reliability.
        """
        rows = self._run(
            """
            MATCH (caller:Procedure)-[:CALLS]->(callee:Procedure)
            RETURN caller.name AS caller, callee.name AS callee
            """
        )
        adj: dict[str, list[str]] = {}
        for r in rows:
            adj.setdefault(r[0], []).append(r[1])

        visited: set[str] = set()
        in_stack: set[str] = set()
        cycles: list[list[str]] = []

        def dfs(node: str, path: list[str]) -> None:
            if len(cycles) >= 20:
                return
            visited.add(node)
            in_stack.add(node)
            path.append(node)
            for neighbor in adj.get(node, []):
                if neighbor not in visited:
                    dfs(neighbor, path)
                elif neighbor in in_stack:
                    cycle_start = path.index(neighbor)
                    cycles.append(list(path[cycle_start:]))
            path.pop()
            in_stack.discard(node)

        all_nodes = set(adj.keys()) | {v for vs in adj.values() for v in vs}
        for node in all_nodes:
            if node not in visited:
                dfs(node, [])

        return cycles

    def get_external_dependencies(self) -> list[str]:
        """Find procedures that are called but have no source (no lineCount)."""
        rows = self._run(
            """
            MATCH (p:Procedure)
            WHERE p.lineCount IS NULL OR p.lineCount = 0
            RETURN p.name AS name
            ORDER BY p.name
            """
        )
        return [r[0] for r in rows]

    def get_linked_server_report(self) -> list[dict]:
        """Get all linked server references."""
        rows = self._run(
            """
            MATCH (p:Procedure)-[r:CALLS_REMOTE]->(ls:LinkedServer)
            RETURN p.name AS procedure, ls.name AS server,
                   ls.database AS database, r.operation AS operation,
                   r.remoteObject AS remoteObject, r.line AS line
            ORDER BY p.name
            """
        )
        keys = ["procedure", "server", "database", "operation", "remoteObject", "line"]
        return [dict(zip(keys, r)) for r in rows]

    def get_unresolved_calls_report(self) -> list[dict]:
        """Get all unresolved/dynamic calls."""
        results = []
        for rel, target_label in [
            ("UNRESOLVED_CALL", "UnresolvedCall"),
            ("DYNAMIC_DISPATCH", "DispatchTable"),
            ("USES_DYNAMIC_SQL", "DynamicCall"),
        ]:
            rows = self._run(
                f"""
                MATCH (p:Procedure)-[r:{rel}]->(target:{target_label})
                RETURN p.name AS procedure, properties(target) AS details
                ORDER BY p.name
                """
            )
            for r in rows:
                results.append({
                    "procedure": r[0],
                    "relType": rel,
                    "targetType": target_label,
                    "details": r[1],
                })
        return results

    def get_all_referenced_tables(self) -> list[str]:
        """Get all unique table names referenced by any procedure."""
        rows = self._run(
            """
            MATCH (:Procedure)-[:USES_TABLE]->(t:Table)
            RETURN DISTINCT t.name AS name
            ORDER BY t.name
            """
        )
        return [r[0] for r in rows]
