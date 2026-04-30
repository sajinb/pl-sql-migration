"""Node 1: PARSE — Parse all SQL files using the configured dialect parser."""

from __future__ import annotations

from pathlib import Path

from tsql_migration.state import MigrationState


def parse_node(state: MigrationState) -> dict:
    """Parse all .sql files in the input directory into ProcedureMetadata.

    Routes to TSqlParser (dialect="tsql") or OraclePlSqlParser (dialect="oracle")
    based on ``config.sql_dialect``.
    """
    dialect = state.config.sql_dialect
    sql_dir = Path(state.sql_input_dir)

    if not sql_dir.exists():
        raise FileNotFoundError(f"SQL input directory not found: {sql_dir}")

    if dialect == "oracle":
        from tsql_migration.parser.oracle_parser import OraclePlSqlParser
        parser = OraclePlSqlParser()
    else:
        from tsql_migration.parser.tsql_parser import TSqlParser
        parser = TSqlParser()

    procedures = parser.parse_directory(sql_dir)

    print(f"[PARSE] Dialect={dialect} — parsed {len(procedures)} procedures:")
    for name, meta in procedures.items():
        print(f"  - {name} ({meta.total_line_count} lines, "
              f"{len(meta.temp_tables)} temp tables, "
              f"calls: {meta.called_procedures})")

    return {"procedures": procedures}
