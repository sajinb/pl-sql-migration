"""Node 2: ANALYZE — Build call graph in Neo4j and detect edge cases."""

from __future__ import annotations

import os

from tsql_migration.graph.neo4j_client import Neo4jClient
from tsql_migration.parser.edge_case_detector import EdgeCaseDetector
from tsql_migration.state import MigrationState


def analyze_node(state: MigrationState) -> dict:
    """Build the call graph in Neo4j and detect edge cases."""
    config = state.config

    neo4j_password = os.environ.get(config.neo4j_password_env, "neo4j")
    client = Neo4jClient(
        config.neo4j_uri,
        config.neo4j_user,
        neo4j_password,
        database=config.neo4j_database,
        label_prefix=config.neo4j_label_prefix,
    )

    try:
        # Clear and set up
        client.clear_graph()
        client.create_constraints()

        # Add all procedures to Neo4j
        for proc in state.procedures.values():
            client.add_procedure(proc)

        # Detect edge cases
        detector = EdgeCaseDetector()
        edge_case_report = detector.detect_all(state.procedures)

        # Add edge cases to Neo4j
        client.add_edge_cases(edge_case_report)

        # Detect cycles
        cycles = client.detect_cycles()

        # Find external dependencies
        external_deps = client.get_external_dependencies()

        print(f"[ANALYZE] Call graph built in Neo4j:")
        print(f"  Procedures: {len(state.procedures)}")
        print(f"  Cycles detected: {len(cycles)}")
        print(f"  External dependencies: {external_deps}")
        print(f"  Linked servers: {len(edge_case_report.linked_servers)}")
        print(f"  Dynamic SQL calls: {len(edge_case_report.dynamic_sql_calls)}")
        print(f"  Variable calls: {len(edge_case_report.variable_calls)}")
        print(f"  Unresolved items: {edge_case_report.unresolved_count}")

        return {
            "call_graph_built": True,
            "cycles": cycles,
            "external_dependencies": external_deps,
            "edge_case_report": edge_case_report,
        }
    finally:
        client.close()
