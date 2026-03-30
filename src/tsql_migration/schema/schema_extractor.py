"""Live SQL Server schema extraction via INFORMATION_SCHEMA.

Connects to the source SQL Server database using pyodbc and extracts
table definitions, columns, primary keys, and foreign keys.
"""

from __future__ import annotations

import os
from typing import Optional

from tsql_migration.state import ColumnMetadata, EntityMetadata, ForeignKeyMetadata

# SQL type -> Java type mapping
SQL_TO_JAVA_TYPE: dict[str, str] = {
    "int": "Integer",
    "bigint": "Long",
    "smallint": "Short",
    "tinyint": "Short",
    "bit": "Boolean",
    "decimal": "BigDecimal",
    "numeric": "BigDecimal",
    "money": "BigDecimal",
    "smallmoney": "BigDecimal",
    "float": "Double",
    "real": "Float",
    "date": "LocalDate",
    "datetime": "LocalDateTime",
    "datetime2": "LocalDateTime",
    "smalldatetime": "LocalDateTime",
    "time": "LocalTime",
    "datetimeoffset": "OffsetDateTime",
    "char": "String",
    "varchar": "String",
    "nchar": "String",
    "nvarchar": "String",
    "text": "String",
    "ntext": "String",
    "uniqueidentifier": "UUID",
    "binary": "byte[]",
    "varbinary": "byte[]",
    "image": "byte[]",
    "xml": "String",
}


def map_sql_to_java_type(sql_type: str) -> str:
    """Map a SQL Server type to its Java equivalent."""
    base_type = sql_type.lower().split("(")[0].strip()
    return SQL_TO_JAVA_TYPE.get(base_type, "String")


class SchemaExtractor:
    """Extract table schemas from a live SQL Server database."""

    def __init__(self, connection_string_env: str) -> None:
        self._conn_str = os.environ.get(connection_string_env, "")

    def extract_tables(self, table_names: list[str]) -> dict[str, EntityMetadata]:
        """Extract metadata for the given table names from INFORMATION_SCHEMA."""
        try:
            import pyodbc
        except ImportError:
            raise ImportError(
                "pyodbc is required for live DB schema extraction. "
                "Install with: pip install 'tsql-migration[db]'"
            )

        if not self._conn_str:
            raise ValueError("Database connection string not set in environment.")

        entities: dict[str, EntityMetadata] = {}
        conn = pyodbc.connect(self._conn_str)
        try:
            for table_name in table_names:
                entity = self._extract_table(conn, table_name)
                if entity:
                    entities[table_name] = entity
        finally:
            conn.close()

        return entities

    def _extract_table(self, conn, table_name: str) -> Optional[EntityMetadata]:
        """Extract a single table's metadata."""
        cursor = conn.cursor()

        # Get columns
        cursor.execute(
            """
            SELECT
                c.COLUMN_NAME,
                c.DATA_TYPE,
                c.IS_NULLABLE,
                c.CHARACTER_MAXIMUM_LENGTH,
                COLUMNPROPERTY(OBJECT_ID(c.TABLE_SCHEMA + '.' + c.TABLE_NAME),
                               c.COLUMN_NAME, 'IsIdentity') AS is_identity
            FROM INFORMATION_SCHEMA.COLUMNS c
            WHERE c.TABLE_NAME = ?
            ORDER BY c.ORDINAL_POSITION
            """,
            table_name,
        )
        rows = cursor.fetchall()
        if not rows:
            return None

        columns: list[ColumnMetadata] = []
        for row in rows:
            col_name, data_type, is_nullable, max_length, is_identity = row
            columns.append(
                ColumnMetadata(
                    name=col_name,
                    sql_type=data_type,
                    java_type=map_sql_to_java_type(data_type),
                    nullable=is_nullable == "YES",
                    is_primary_key=False,  # set below
                    is_auto_increment=bool(is_identity),
                    max_length=max_length,
                )
            )

        # Get schema name
        cursor.execute(
            """
            SELECT TABLE_SCHEMA FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_NAME = ?
            """,
            table_name,
        )
        schema_row = cursor.fetchone()
        schema_name = schema_row[0] if schema_row else "dbo"

        # Get primary keys
        cursor.execute(
            """
            SELECT kcu.COLUMN_NAME
            FROM INFORMATION_SCHEMA.TABLE_CONSTRAINTS tc
            JOIN INFORMATION_SCHEMA.KEY_COLUMN_USAGE kcu
                ON tc.CONSTRAINT_NAME = kcu.CONSTRAINT_NAME
            WHERE tc.TABLE_NAME = ? AND tc.CONSTRAINT_TYPE = 'PRIMARY KEY'
            ORDER BY kcu.ORDINAL_POSITION
            """,
            table_name,
        )
        pk_columns = [row[0] for row in cursor.fetchall()]
        for col in columns:
            if col.name in pk_columns:
                col.is_primary_key = True

        # Get foreign keys
        cursor.execute(
            """
            SELECT
                kcu.COLUMN_NAME,
                ccu.TABLE_NAME AS referenced_table,
                ccu.COLUMN_NAME AS referenced_column
            FROM INFORMATION_SCHEMA.REFERENTIAL_CONSTRAINTS rc
            JOIN INFORMATION_SCHEMA.KEY_COLUMN_USAGE kcu
                ON rc.CONSTRAINT_NAME = kcu.CONSTRAINT_NAME
            JOIN INFORMATION_SCHEMA.KEY_COLUMN_USAGE ccu
                ON rc.UNIQUE_CONSTRAINT_NAME = ccu.CONSTRAINT_NAME
            WHERE kcu.TABLE_NAME = ?
            """,
            table_name,
        )
        fks = [
            ForeignKeyMetadata(
                column=row[0],
                referenced_table=row[1],
                referenced_column=row[2],
            )
            for row in cursor.fetchall()
        ]

        entity_class_name = to_pascal_case(table_name)

        return EntityMetadata(
            table_name=table_name,
            schema_name=schema_name,
            entity_class_name=entity_class_name,
            columns=columns,
            primary_key=pk_columns,
            foreign_keys=fks,
        )


def to_pascal_case(name: str) -> str:
    """Convert snake_case or plain name to PascalCase."""
    return "".join(word.capitalize() for word in name.replace("-", "_").split("_"))
