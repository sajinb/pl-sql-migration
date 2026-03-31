"""Node 3: PLAN — Determine migration order via topological sort."""

from __future__ import annotations

import os

from tsql_migration.graph.neo4j_client import Neo4jClient
from tsql_migration.state import MigrationState


def plan_node(state: MigrationState) -> dict:
    """Query Neo4j for the topological migration order."""
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
        migration_order = client.get_migration_order()

        print(f"[PLAN] Migration order (bottom-up, {len(migration_order)} procedures):")
        for i, name in enumerate(migration_order, 1):
            proc = state.procedures.get(name)
            suffix = ""
            if proc:
                suffix = f" ({proc.total_line_count} lines)"
                if proc.called_procedures:
                    suffix += f" calls: {proc.called_procedures}"
            else:
                suffix = " [external dependency]"
            print(f"  {i}. {name}{suffix}")

        if state.cycles:
            print(f"\n  WARNING: {len(state.cycles)} circular dependencies detected.")
            for cycle in state.cycles:
                print(f"    Cycle: {' -> '.join(cycle)}")

        return {"migration_order": migration_order}
    finally:
        client.close()
