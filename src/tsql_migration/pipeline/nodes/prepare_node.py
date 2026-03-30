"""Node 5: PREPARE — Generate staging table DDLs for temp table replacements."""

from __future__ import annotations

from tsql_migration.state import MigrationState


def prepare_node(state: MigrationState) -> dict:
    """Generate staging table DDLs and temp table mapping."""
    staging_ddls: list[str] = []
    temp_table_mapping: dict[str, str] = {}
    seen: set[str] = set()

    for proc in state.procedures.values():
        for temp in proc.temp_tables:
            if temp.original_name in seen:
                continue
            seen.add(temp.original_name)

            # Map temp name to staging name
            staging_name = temp.staging_table_name
            temp_table_mapping[temp.original_name] = staging_name

            # Generate DDL
            ddl = _generate_staging_ddl(temp, state.config.staging_table_prefix)
            staging_ddls.append(ddl)

    print(f"[PREPARE] Generated {len(staging_ddls)} staging table DDLs:")
    for orig, staging in temp_table_mapping.items():
        print(f"  {orig} → {staging}")

    return {
        "staging_ddls": staging_ddls,
        "temp_table_mapping": temp_table_mapping,
    }


def _generate_staging_ddl(temp_table, prefix: str) -> str:
    """Generate CREATE TABLE DDL for a staging table."""
    table_name = temp_table.staging_table_name
    lines = [
        f"-- Staging table replacing temp table: {temp_table.original_name}",
        f"IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = '{table_name}')",
        f"CREATE TABLE [{table_name}] (",
        "    [batch_id] VARCHAR(36) NOT NULL,",
        "    [created_at] DATETIME2 DEFAULT GETDATE(),",
    ]

    if temp_table.columns:
        for col in temp_table.columns:
            null_str = "" if col.nullable else " NOT NULL"
            lines.append(f"    [{col.name}] {col.sql_type}{null_str},")
    else:
        lines.append("    -- TODO: Define columns based on the source SELECT INTO query")

    lines.append(f"    INDEX [IX_{table_name}_batch] ([batch_id])")
    lines.append(");")
    lines.append("")

    return "\n".join(lines)
