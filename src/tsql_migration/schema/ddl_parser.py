"""Offline DDL parsing using sqlglot — extracts table schemas from CREATE TABLE scripts.

Used when a live SQL Server connection is not available.
"""

from __future__ import annotations

from pathlib import Path

import sqlglot
from sqlglot import exp

from tsql_migration.schema.schema_extractor import map_sql_to_java_type, to_pascal_case
from tsql_migration.state import ColumnMetadata, EntityMetadata, ForeignKeyMetadata


class DdlParser:
    """Parse CREATE TABLE statements from .sql files using sqlglot."""

    def parse_directory(self, ddl_dir: Path) -> dict[str, EntityMetadata]:
        """Parse all .sql files in the DDL directory."""
        entities: dict[str, EntityMetadata] = {}
        for sql_file in sorted(ddl_dir.glob("*.sql")):
            sql = sql_file.read_text(encoding="utf-8-sig")
            parsed = self.parse_sql(sql)
            entities.update(parsed)
        return entities

    def parse_sql(self, sql: str) -> dict[str, EntityMetadata]:
        """Parse SQL containing one or more CREATE TABLE statements."""
        entities: dict[str, EntityMetadata] = {}

        for statement in sqlglot.parse(sql, dialect="tsql", error_level=sqlglot.ErrorLevel.IGNORE):
            if statement is None:
                continue
            if not isinstance(statement, exp.Create):
                continue

            table_expr = statement.find(exp.Table)
            if not table_expr:
                continue

            table_name = table_expr.name
            schema_name = table_expr.db or "dbo"

            # Skip temp tables
            if table_name.startswith("#"):
                continue

            columns: list[ColumnMetadata] = []
            pk_columns: list[str] = []
            foreign_keys: list[ForeignKeyMetadata] = []

            # Extract column definitions
            schema_expr = statement.find(exp.Schema)
            if schema_expr:
                for col_def in schema_expr.find_all(exp.ColumnDef):
                    col_name = col_def.name
                    col_type = self._extract_column_type(col_def)
                    nullable = not self._has_not_null(col_def)
                    is_identity = self._has_identity(col_def)
                    is_pk = self._has_primary_key(col_def)

                    if is_pk:
                        pk_columns.append(col_name)

                    columns.append(
                        ColumnMetadata(
                            name=col_name,
                            sql_type=col_type,
                            java_type=map_sql_to_java_type(col_type),
                            nullable=nullable,
                            is_primary_key=is_pk,
                            is_auto_increment=is_identity,
                        )
                    )

            # Extract table-level PRIMARY KEY constraints
            for constraint in self._find_pk_constraints(sql, table_name):
                if constraint not in pk_columns:
                    pk_columns.append(constraint)
                    for col in columns:
                        if col.name == constraint:
                            col.is_primary_key = True

            # Extract FOREIGN KEY constraints
            foreign_keys = self._find_fk_constraints(sql, table_name)

            entities[table_name] = EntityMetadata(
                table_name=table_name,
                schema_name=schema_name,
                entity_class_name=to_pascal_case(table_name),
                columns=columns,
                primary_key=pk_columns,
                foreign_keys=foreign_keys,
            )

        return entities

    def _extract_column_type(self, col_def: exp.ColumnDef) -> str:
        """Extract the SQL type string from a column definition."""
        kind = col_def.find(exp.DataType)
        if kind:
            return kind.sql(dialect="tsql")
        return "VARCHAR"

    def _has_not_null(self, col_def: exp.ColumnDef) -> bool:
        return col_def.find(exp.NotNullColumnConstraint) is not None

    def _has_identity(self, col_def: exp.ColumnDef) -> bool:
        """Check if column has IDENTITY property."""
        sql_text = col_def.sql(dialect="tsql").upper()
        return "IDENTITY" in sql_text

    def _has_primary_key(self, col_def: exp.ColumnDef) -> bool:
        """Check if column has inline PRIMARY KEY constraint."""
        sql_text = col_def.sql(dialect="tsql").upper()
        return "PRIMARY KEY" in sql_text

    def _find_pk_constraints(self, sql: str, table_name: str) -> list[str]:
        """Find table-level PRIMARY KEY constraint columns via regex."""
        import re

        pattern = re.compile(
            r"(?i)CONSTRAINT\s+\w+\s+PRIMARY\s+KEY\s*(?:CLUSTERED|NONCLUSTERED)?\s*\(([^)]+)\)",
        )
        columns: list[str] = []
        for match in pattern.finditer(sql):
            for col in match.group(1).split(","):
                col = col.strip().strip("[]").split()[0]  # remove ASC/DESC
                columns.append(col)
        return columns

    def _find_fk_constraints(self, sql: str, table_name: str) -> list[ForeignKeyMetadata]:
        """Find FOREIGN KEY constraints via regex."""
        import re

        pattern = re.compile(
            r"(?i)FOREIGN\s+KEY\s*\(\s*\[?(\w+)\]?\s*\)\s*REFERENCES\s+"
            r"(?:\[?\w+\]?\.)?\[?(\w+)\]?\s*\(\s*\[?(\w+)\]?\s*\)",
        )
        fks: list[ForeignKeyMetadata] = []
        for match in pattern.finditer(sql):
            fks.append(
                ForeignKeyMetadata(
                    column=match.group(1),
                    referenced_table=match.group(2),
                    referenced_column=match.group(3),
                )
            )
        return fks
