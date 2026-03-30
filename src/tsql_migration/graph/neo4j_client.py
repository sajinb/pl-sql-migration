"""Neo4j client for storing and querying the procedure call graph.

Data model:
  Core nodes:   Procedure, Table, TempTable
  Edge nodes:   LinkedServer, DynamicCall, DispatchTable, UnresolvedCall
  Relationships: CALLS, USES_TABLE, USES_TEMP_TABLE, CALLS_REMOTE,
                 READS_REMOTE, USES_DYNAMIC_SQL, DYNAMIC_DISPATCH, UNRESOLVED_CALL
"""

from __future__ import annotations

from neo4j import GraphDatabase, Driver

from tsql_migration.state import (
    EdgeCaseReport,
    ProcedureMetadata,
)


class Neo4jClient:
    """Manages the procedure call graph in Neo4j."""

    def __init__(self, uri: str, user: str, password: str) -> None:
        self._driver: Driver = GraphDatabase.driver(uri, auth=(user, password))

    def close(self) -> None:
        self._driver.close()

    # -------------------------------------------------------------------
    # Setup
    # -------------------------------------------------------------------

    def clear_graph(self) -> None:
        """Delete all nodes and relationships (use at start of new analysis)."""
        self._driver.execute_query("MATCH (n) DETACH DELETE n")

    def create_constraints(self) -> None:
        """Create uniqueness constraints for core node types."""
        constraints = [
            "CREATE CONSTRAINT IF NOT EXISTS FOR (p:Procedure) REQUIRE p.name IS UNIQUE",
            "CREATE CONSTRAINT IF NOT EXISTS FOR (t:Table) REQUIRE t.name IS UNIQUE",
            "CREATE CONSTRAINT IF NOT EXISTS FOR (tt:TempTable) REQUIRE tt.name IS UNIQUE",
        ]
        for cypher in constraints:
            try:
                self._driver.execute_query(cypher)
            except Exception:
                pass  # constraint may already exist

    # -------------------------------------------------------------------
    # Populate graph from parsed metadata
    # -------------------------------------------------------------------

    def add_procedure(self, proc: ProcedureMetadata) -> None:
        """Add a procedure node with its table/temp table relationships."""
        # Create or merge Procedure node
        self._driver.execute_query(
            """
            MERGE (p:Procedure {name: $name})
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

        # Referenced tables
        for table in proc.referenced_tables:
            self._driver.execute_query(
                """
                MERGE (t:Table {name: $table})
                WITH t
                MERGE (p:Procedure {name: $proc})
                MERGE (p)-[:USES_TABLE]->(t)
                """,
                table=table,
                proc=proc.procedure_name,
            )

        # Temp tables
        for temp in proc.temp_tables:
            self._driver.execute_query(
                """
                MERGE (tt:TempTable {name: $name})
                SET tt.stagingName = $stagingName,
                    tt.isGlobal = $isGlobal
                WITH tt
                MERGE (p:Procedure {name: $proc})
                MERGE (p)-[:USES_TEMP_TABLE]->(tt)
                """,
                name=temp.original_name,
                stagingName=temp.staging_table_name,
                isGlobal=temp.is_global_temp,
                proc=proc.procedure_name,
            )

        # Called procedures
        for called in proc.called_procedures:
            self._driver.execute_query(
                """
                MERGE (callee:Procedure {name: $callee})
                WITH callee
                MERGE (caller:Procedure {name: $caller})
                MERGE (caller)-[:CALLS {certainty: "resolved"}]->(callee)
                """,
                callee=called,
                caller=proc.procedure_name,
            )

    def add_edge_cases(self, report: EdgeCaseReport) -> None:
        """Add edge case nodes and relationships to the graph."""
        # Linked servers
        for ref in report.linked_servers:
            self._driver.execute_query(
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
                server=ref.server,
                database=ref.database,
                proc=ref.procedure,
                operation=ref.operation,
                remoteObject=ref.remote_object,
                line=ref.line,
            )

        # Dynamic SQL
        for ref in report.dynamic_sql_calls:
            self._driver.execute_query(
                """
                CREATE (dc:DynamicCall {
                    expression: $expression,
                    tier: $tier,
                    line: $line,
                    resolvedSql: $resolvedSql
                })
                WITH dc
                MERGE (p:Procedure {name: $proc})
                MERGE (p)-[:USES_DYNAMIC_SQL]->(dc)
                """,
                expression=ref.expression,
                tier=ref.tier,
                line=ref.line,
                resolvedSql=ref.resolved_sql or "",
                proc=ref.procedure,
            )

        # Variable calls
        for ref in report.variable_calls:
            if ref.certainty == "resolved" and ref.resolved_targets:
                # Add a CALLS edge with type=variable
                for target in ref.resolved_targets:
                    self._driver.execute_query(
                        """
                        MERGE (callee:Procedure {name: $callee})
                        WITH callee
                        MERGE (caller:Procedure {name: $caller})
                        MERGE (caller)-[:CALLS {
                            type: "variable",
                            certainty: "resolved",
                            variable: $variable
                        }]->(callee)
                        """,
                        callee=target,
                        caller=ref.procedure,
                        variable=ref.variable,
                    )
            elif ref.certainty == "conditional" and ref.resolved_targets:
                for target in ref.resolved_targets:
                    self._driver.execute_query(
                        """
                        MERGE (callee:Procedure {name: $callee})
                        WITH callee
                        MERGE (caller:Procedure {name: $caller})
                        MERGE (caller)-[:CALLS {
                            type: "variable",
                            certainty: "conditional",
                            variable: $variable,
                            condition: $condition
                        }]->(callee)
                        """,
                        callee=target,
                        caller=ref.procedure,
                        variable=ref.variable,
                        condition=ref.condition or "",
                    )
            elif ref.certainty == "dispatch_table":
                self._driver.execute_query(
                    """
                    CREATE (dt:DispatchTable {
                        name: $tableRef,
                        variable: $variable,
                        line: $line
                    })
                    WITH dt
                    MERGE (p:Procedure {name: $proc})
                    MERGE (p)-[:DYNAMIC_DISPATCH]->(dt)
                    """,
                    tableRef=ref.condition or "unknown",
                    variable=ref.variable,
                    line=ref.line,
                    proc=ref.procedure,
                )
            else:
                # Opaque
                self._driver.execute_query(
                    """
                    CREATE (uc:UnresolvedCall {
                        variable: $variable,
                        line: $line,
                        context: $context
                    })
                    WITH uc
                    MERGE (p:Procedure {name: $proc})
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
        records, _, _ = self._driver.execute_query(
            """
            MATCH (p:Procedure)
            WHERE NOT (p)-[:CALLS]->()
            RETURN p.name AS name
            ORDER BY p.name
            """
        )
        return [r["name"] for r in records]

    def get_migration_order(self) -> list[str]:
        """Topological sort — leaf procedures first, then their callers.

        Uses a simple BFS-based approach since GDS may not be installed.
        """
        # Get all procedures and their outgoing call counts
        records, _, _ = self._driver.execute_query(
            """
            MATCH (p:Procedure)
            OPTIONAL MATCH (p)-[:CALLS]->(callee:Procedure)
            RETURN p.name AS name, count(callee) AS outDegree
            """
        )
        out_degree: dict[str, int] = {r["name"]: r["outDegree"] for r in records}

        # Get reverse edges (who calls whom)
        records, _, _ = self._driver.execute_query(
            """
            MATCH (caller:Procedure)-[:CALLS]->(callee:Procedure)
            RETURN caller.name AS caller, callee.name AS callee
            """
        )
        reverse_adj: dict[str, list[str]] = {}
        for r in records:
            reverse_adj.setdefault(r["callee"], []).append(r["caller"])

        # Kahn's algorithm — start with nodes that have 0 outgoing calls
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
        records, _, _ = self._driver.execute_query(
            """
            MATCH path = (p:Procedure)-[:CALLS*]->(p)
            RETURN [n IN nodes(path) | n.name] AS cycle
            LIMIT 20
            """
        )
        # Deduplicate cycles
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
        records, _, _ = self._driver.execute_query(
            """
            MATCH (p:Procedure)
            WHERE p.lineCount IS NULL OR p.lineCount = 0
            RETURN p.name AS name
            ORDER BY p.name
            """
        )
        return [r["name"] for r in records]

    def get_linked_server_report(self) -> list[dict]:
        """Get all linked server references."""
        records, _, _ = self._driver.execute_query(
            """
            MATCH (p:Procedure)-[r:CALLS_REMOTE|READS_REMOTE]->(ls:LinkedServer)
            RETURN p.name AS procedure, ls.name AS server,
                   ls.database AS database, r.operation AS operation,
                   r.remoteObject AS remoteObject, r.line AS line
            ORDER BY p.name
            """
        )
        return [dict(r) for r in records]

    def get_unresolved_calls_report(self) -> list[dict]:
        """Get all unresolved/dynamic calls."""
        records, _, _ = self._driver.execute_query(
            """
            MATCH (p:Procedure)-[r:UNRESOLVED_CALL|DYNAMIC_DISPATCH|USES_DYNAMIC_SQL]->(target)
            RETURN p.name AS procedure, labels(target)[0] AS targetType,
                   properties(target) AS details, type(r) AS relType
            ORDER BY p.name
            """
        )
        return [dict(r) for r in records]

    def get_all_referenced_tables(self) -> list[str]:
        """Get all unique table names referenced by any procedure."""
        records, _, _ = self._driver.execute_query(
            """
            MATCH (:Procedure)-[:USES_TABLE]->(t:Table)
            RETURN DISTINCT t.name AS name
            ORDER BY t.name
            """
        )
        return [r["name"] for r in records]
