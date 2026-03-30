"""Node 1: PARSE — Parse all T-SQL files using sqlglot."""

from __future__ import annotations

from pathlib import Path

from tsql_migration.parser.tsql_parser import TSqlParser
from tsql_migration.state import MigrationState


def parse_node(state: MigrationState) -> dict:
    """Parse all .sql files in the input directory into ProcedureMetadata."""
    parser = TSqlParser()
    sql_dir = Path(state.sql_input_dir)

    if not sql_dir.exists():
        raise FileNotFoundError(f"SQL input directory not found: {sql_dir}")

    procedures = parser.parse_directory(sql_dir)

    print(f"[PARSE] Parsed {len(procedures)} procedures:")
    for name, meta in procedures.items():
        print(f"  - {name} ({meta.total_line_count} lines, "
              f"{len(meta.temp_tables)} temp tables, "
              f"calls: {meta.called_procedures})")

    return {"procedures": procedures}
