"""Node 4: EXTRACT SCHEMA — Generate JPA entities and repositories for referenced tables."""

from __future__ import annotations

import os
from pathlib import Path

from tsql_migration.schema.ddl_parser import DdlParser
from tsql_migration.schema.entity_generator import EntityGenerator
from tsql_migration.schema.schema_extractor import SchemaExtractor
from tsql_migration.state import EntityMetadata, MigrationState


def extract_schema_node(state: MigrationState) -> dict:
    """Extract table schemas and generate entity/repository code."""
    config = state.config

    # Collect all referenced tables across all procedures
    all_tables: set[str] = set()
    for proc in state.procedures.values():
        all_tables.update(proc.referenced_tables)

    if not all_tables:
        print("[EXTRACT SCHEMA] No tables referenced — skipping.")
        return {"entities": {}, "repositories": {}, "schema_source": "none"}

    print(f"[EXTRACT SCHEMA] {len(all_tables)} tables referenced: {sorted(all_tables)}")

    entities: dict[str, EntityMetadata] = {}
    schema_source = "none"

    # Strategy 1: Live DB connection
    if config.source_db.enabled:
        try:
            extractor = SchemaExtractor(config.source_db.connection_string_env)
            entities = extractor.extract_tables(sorted(all_tables))
            schema_source = "live_db"
            print(f"  Extracted {len(entities)} tables from live DB.")
        except Exception as e:
            print(f"  Live DB extraction failed: {e}. Falling back to DDL scripts.")

    # Strategy 2: DDL scripts (offline)
    if not entities:
        ddl_dir = Path(config.ddl_input_dir)
        if ddl_dir.exists() and any(ddl_dir.glob("*.sql")):
            parser = DdlParser()
            all_ddl_entities = parser.parse_directory(ddl_dir)
            # Filter to only referenced tables
            entities = {
                name: meta
                for name, meta in all_ddl_entities.items()
                if name in all_tables
            }
            schema_source = "ddl_scripts"
            print(f"  Extracted {len(entities)} tables from DDL scripts.")
        else:
            print(f"  No DDL scripts found in {ddl_dir}.")

    # Strategy 3: Stub entities for tables we couldn't resolve
    missing = all_tables - set(entities.keys())
    if missing:
        print(f"  Creating stub entities for {len(missing)} unresolved tables: {sorted(missing)}")
        for table_name in missing:
            from tsql_migration.schema.schema_extractor import to_pascal_case
            entities[table_name] = EntityMetadata(
                table_name=table_name,
                schema_name="dbo",
                entity_class_name=to_pascal_case(table_name),
                columns=[],
                primary_key=[],
                foreign_keys=[],
            )

    # Generate Java code
    generator = EntityGenerator(config.base_package)
    repositories: dict[str, str] = {}

    for name, entity in entities.items():
        if entity.columns:  # Only generate full code if we have column info
            repositories[name] = generator.generate_repository(entity)

    print(f"[EXTRACT SCHEMA] Done: {len(entities)} entities, "
          f"{len(repositories)} repositories (source: {schema_source})")

    return {
        "entities": entities,
        "repositories": repositories,
        "schema_source": schema_source,
    }
