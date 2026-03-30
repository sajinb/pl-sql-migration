"""T-SQL stored procedure parser using sqlglot.

Parses T-SQL source into ProcedureMetadata, extracting:
- Procedure name, schema, parameters
- Temp table usage (#tables)
- Called procedures (EXEC)
- Referenced tables (FROM, JOIN, INSERT INTO, UPDATE, DELETE, MERGE)
- Logical blocks for chunking
"""

from __future__ import annotations

import re
from pathlib import Path

import sqlglot
from sqlglot import exp
from sqlglot.dialects.tsql import TSQL

from tsql_migration.state import (
    ParameterMetadata,
    ProcedureMetadata,
    SqlBlock,
    TempTableColumn,
    TempTableUsage,
)


class TSqlParser:
    """Parses T-SQL stored procedure files using sqlglot's TSQL dialect."""

    def __init__(self) -> None:
        self.dialect = "tsql"

    def parse_file(self, file_path: Path) -> ProcedureMetadata:
        """Parse a single .sql file into ProcedureMetadata."""
        raw_sql = file_path.read_text(encoding="utf-8-sig")
        metadata = self.parse_sql(raw_sql)
        metadata.source_file = str(file_path)
        return metadata

    def parse_directory(self, directory: Path) -> dict[str, ProcedureMetadata]:
        """Parse all .sql files in a directory."""
        results: dict[str, ProcedureMetadata] = {}
        for sql_file in sorted(directory.glob("*.sql")):
            metadata = self.parse_file(sql_file)
            results[metadata.procedure_name] = metadata
        return results

    def parse_sql(self, raw_sql: str) -> ProcedureMetadata:
        """Parse T-SQL source code into ProcedureMetadata."""
        lines = raw_sql.split("\n")
        proc_name, schema_name = self._extract_procedure_name(raw_sql)
        parameters = self._extract_parameters(raw_sql)
        temp_tables = self._extract_temp_tables(raw_sql)
        called_procedures = self._extract_called_procedures(raw_sql)
        referenced_tables = self._extract_referenced_tables(raw_sql, temp_tables)
        logical_blocks = self._extract_logical_blocks(raw_sql, lines)

        return ProcedureMetadata(
            procedure_name=proc_name,
            schema_name=schema_name,
            parameters=parameters,
            temp_tables=temp_tables,
            called_procedures=called_procedures,
            referenced_tables=referenced_tables,
            logical_blocks=logical_blocks,
            raw_sql=raw_sql,
            total_line_count=len(lines),
        )

    def _extract_procedure_name(self, sql: str) -> tuple[str, str]:
        """Extract procedure name and schema from CREATE PROCEDURE statement."""
        match = re.search(
            r"(?i)CREATE\s+(?:OR\s+ALTER\s+)?PROC(?:EDURE)?\s+"
            r"(?:\[?(\w+)\]?\.)?\[?(\w+)\]?",
            sql,
        )
        if match:
            schema = match.group(1) or "dbo"
            name = match.group(2)
            return name, schema
        return "UnknownProcedure", "dbo"

    def _extract_parameters(self, sql: str) -> list[ParameterMetadata]:
        """Extract procedure parameters."""
        # Get text between procedure name and AS keyword
        param_section = re.search(
            r"(?i)PROC(?:EDURE)?\s+[^\n]+(.*?)\bAS\b",
            sql,
            re.DOTALL,
        )
        if not param_section:
            return []

        params: list[ParameterMetadata] = []
        param_text = param_section.group(1)
        for match in re.finditer(
            r"(?i)(@\w+)\s+(\w+(?:\([^)]*\))?)\s*(?:=\s*([^,\n]+))?\s*(?:(OUTPUT|OUT))?",
            param_text,
        ):
            params.append(
                ParameterMetadata(
                    name=match.group(1),
                    sql_type=match.group(2),
                    direction="OUT" if match.group(4) else "IN",
                    default_value=match.group(3).strip() if match.group(3) else None,
                )
            )
        return params

    def _extract_temp_tables(self, sql: str) -> list[TempTableUsage]:
        """Extract temp table CREATE and SELECT INTO statements."""
        temp_tables: list[TempTableUsage] = []
        seen_names: set[str] = set()

        # CREATE TABLE #name (...)
        for match in re.finditer(
            r"(?i)CREATE\s+TABLE\s+(#{1,2}\w+)\s*\(([^)]+)\)", sql
        ):
            temp_name = match.group(1)
            if temp_name in seen_names:
                continue
            seen_names.add(temp_name)

            columns = self._parse_column_defs(match.group(2))
            temp_tables.append(
                TempTableUsage(
                    original_name=temp_name,
                    staging_table_name=self._temp_to_staging_name(temp_name),
                    columns=columns,
                    create_statement=match.group(0),
                    is_global_temp=temp_name.startswith("##"),
                )
            )

        # SELECT ... INTO #name
        for match in re.finditer(
            r"(?i)SELECT\s+.+?\s+INTO\s+(#{1,2}\w+)", sql, re.DOTALL
        ):
            temp_name = match.group(1)
            if temp_name in seen_names:
                continue
            seen_names.add(temp_name)

            temp_tables.append(
                TempTableUsage(
                    original_name=temp_name,
                    staging_table_name=self._temp_to_staging_name(temp_name),
                    columns=[],  # columns inferred from SELECT — can't parse statically
                    create_statement=match.group(0),
                    is_global_temp=temp_name.startswith("##"),
                )
            )

        return temp_tables

    def _extract_called_procedures(self, sql: str) -> list[str]:
        """Extract procedure names from EXEC/EXECUTE statements."""
        called: list[str] = []
        for match in re.finditer(
            r"(?i)(?:EXEC(?:UTE)?)\s+(?:\[?(\w+)\]?\.)?"
            r"(?:\[?(\w+)\]?\.)?"
            r"\[?(\w+)\]?",
            sql,
        ):
            # The last capture group is always the procedure name.
            # Middle groups could be schema or database parts.
            proc_name = match.group(3)
            # Skip system procedures
            if proc_name and not proc_name.startswith(("sp_", "xp_")):
                if proc_name not in called:
                    called.append(proc_name)
        return called

    def _extract_referenced_tables(
        self, sql: str, temp_tables: list[TempTableUsage]
    ) -> list[str]:
        """Extract table names referenced in FROM, JOIN, INSERT, UPDATE, DELETE, MERGE."""
        temp_names = {t.original_name for t in temp_tables}
        tables: list[str] = []

        # Use sqlglot to parse individual statements where possible
        try:
            for statement in sqlglot.parse(sql, dialect=self.dialect, error_level=sqlglot.ErrorLevel.IGNORE):
                if statement is None:
                    continue
                for table in statement.find_all(exp.Table):
                    table_name = table.name
                    if (
                        table_name
                        and not table_name.startswith("#")
                        and table_name not in temp_names
                        and table_name.lower() not in ("deleted", "inserted")
                        and table_name not in tables
                    ):
                        tables.append(table_name)
        except Exception:
            # Fallback to regex if sqlglot can't parse the full procedure
            tables = self._extract_tables_regex(sql, temp_names)

        return tables

    def _extract_tables_regex(
        self, sql: str, temp_names: set[str]
    ) -> list[str]:
        """Regex fallback for table extraction."""
        tables: list[str] = []
        for match in re.finditer(
            r"(?i)(?:FROM|JOIN|INTO|UPDATE|DELETE\s+FROM|MERGE\s+INTO?)\s+"
            r"(?:\[?(\w+)\]?\.)?\[?(\w+)\]?",
            sql,
        ):
            table = match.group(2)
            if (
                table
                and not table.startswith("#")
                and table not in temp_names
                and table.lower() not in ("deleted", "inserted")
                and table not in tables
            ):
                tables.append(table)
        return tables

    def _extract_logical_blocks(
        self, sql: str, lines: list[str]
    ) -> list[SqlBlock]:
        """Break the procedure body into logical blocks for chunking."""
        blocks: list[SqlBlock] = []
        body_start = self._find_body_start(lines)
        block_counter = 0
        i = body_start

        while i < len(lines):
            trimmed = lines[i].strip().upper()

            if not trimmed or trimmed in ("BEGIN", "END", "GO"):
                i += 1
                continue

            if trimmed.startswith("DECLARE") or trimmed.startswith("SET @"):
                end = self._find_contiguous_block(lines, i, ("DECLARE", "SET @"))
                blocks.append(self._make_block(f"block_{block_counter}", "DECLARATION", lines, i, end))
                block_counter += 1
                i = end + 1
            elif trimmed.startswith("CREATE TABLE #"):
                end = self._find_closing_paren(lines, i)
                blocks.append(self._make_block(f"block_{block_counter}", "TEMP_TABLE_CREATE", lines, i, end))
                block_counter += 1
                i = end + 1
            elif "CURSOR" in trimmed and trimmed.startswith("DECLARE"):
                end = self._find_keyword_line(lines, i, "DEALLOCATE")
                blocks.append(self._make_block(f"block_{block_counter}", "CURSOR_LOOP", lines, i, end))
                block_counter += 1
                i = end + 1
            elif trimmed.startswith("BEGIN TRY"):
                end = self._find_keyword_line(lines, i, "END CATCH")
                blocks.append(self._make_block(f"block_{block_counter}", "TRY_CATCH", lines, i, end))
                block_counter += 1
                i = end + 1
            elif trimmed.startswith("BEGIN TRAN"):
                end = max(
                    self._find_keyword_line(lines, i, "COMMIT"),
                    self._find_keyword_line(lines, i, "ROLLBACK"),
                )
                blocks.append(self._make_block(f"block_{block_counter}", "TRANSACTION", lines, i, end))
                block_counter += 1
                i = end + 1
            elif trimmed.startswith("WHILE"):
                end = self._find_matching_end(lines, i)
                blocks.append(self._make_block(f"block_{block_counter}", "WHILE_LOOP", lines, i, end))
                block_counter += 1
                i = end + 1
            elif trimmed.startswith("IF"):
                end = self._find_matching_end(lines, i)
                blocks.append(self._make_block(f"block_{block_counter}", "IF_ELSE", lines, i, end))
                block_counter += 1
                i = end + 1
            elif trimmed.startswith("EXEC"):
                blocks.append(self._make_block(f"block_{block_counter}", "PROCEDURE_CALL", lines, i, i))
                block_counter += 1
                i += 1
            elif any(trimmed.startswith(kw) for kw in ("INSERT", "UPDATE", "DELETE", "MERGE")):
                end = self._find_statement_end(lines, i)
                blocks.append(self._make_block(f"block_{block_counter}", "DML_OPERATION", lines, i, end))
                block_counter += 1
                i = end + 1
            elif trimmed.startswith("SELECT"):
                end = self._find_statement_end(lines, i)
                blocks.append(self._make_block(f"block_{block_counter}", "SELECT_QUERY", lines, i, end))
                block_counter += 1
                i = end + 1
            elif trimmed.startswith("RETURN"):
                blocks.append(self._make_block(f"block_{block_counter}", "RETURN_BLOCK", lines, i, i))
                block_counter += 1
                i += 1
            else:
                blocks.append(self._make_block(f"block_{block_counter}", "OTHER", lines, i, i))
                block_counter += 1
                i += 1

        return blocks

    # -----------------------------------------------------------------------
    # Helper methods
    # -----------------------------------------------------------------------

    def _parse_column_defs(self, column_text: str) -> list[TempTableColumn]:
        columns: list[TempTableColumn] = []
        for col_def in column_text.split(","):
            col_def = col_def.strip()
            if not col_def:
                continue
            parts = col_def.split(None, 2)
            if len(parts) >= 2:
                col_name = parts[0].strip("[]")
                col_type = parts[1].split()[0]
                nullable = "NOT NULL" not in col_def.upper()
                columns.append(TempTableColumn(name=col_name, sql_type=col_type, nullable=nullable))
        return columns

    def _temp_to_staging_name(self, temp_name: str) -> str:
        cleaned = re.sub(r"^#+", "", temp_name)
        snake = re.sub(r"([a-z])([A-Z])", r"\1_\2", cleaned).lower()
        return f"stg_{snake}"

    def _find_body_start(self, lines: list[str]) -> int:
        for i, line in enumerate(lines):
            if re.match(r"^\s*AS\b", line, re.IGNORECASE):
                return i + 1
        return 0

    def _make_block(
        self, block_id: str, block_type: str, lines: list[str], start: int, end: int
    ) -> SqlBlock:
        end = min(end, len(lines) - 1)
        content = "\n".join(lines[start : end + 1])
        input_vars = list(dict.fromkeys(re.findall(r"@\w+", content)))
        output_vars = list(
            dict.fromkeys(
                re.findall(r"(?i)SET\s+(@\w+)", content)
                + re.findall(r"(?i)SELECT\s+(@\w+)\s*=", content)
            )
        )
        return SqlBlock(
            block_id=block_id,
            block_type=block_type,
            content=content,
            start_line=start,
            end_line=end,
            input_variables=input_vars,
            output_variables=output_vars,
        )

    def _find_contiguous_block(
        self, lines: list[str], start: int, prefixes: tuple[str, ...]
    ) -> int:
        i = start
        while i + 1 < len(lines):
            next_trimmed = lines[i + 1].strip().upper()
            if any(next_trimmed.startswith(p) for p in prefixes):
                i += 1
            else:
                break
        return i

    def _find_closing_paren(self, lines: list[str], start: int) -> int:
        depth = 0
        for i in range(start, len(lines)):
            for ch in lines[i]:
                if ch == "(":
                    depth += 1
                elif ch == ")":
                    depth -= 1
                    if depth == 0 and i > start:
                        return i
        return start

    def _find_matching_end(self, lines: list[str], start: int) -> int:
        depth = 0
        for i in range(start, len(lines)):
            trimmed = lines[i].strip().upper()
            if re.match(r"^BEGIN\b", trimmed) and not any(
                trimmed.startswith(kw) for kw in ("BEGIN TRY", "BEGIN CATCH", "BEGIN TRAN")
            ):
                depth += 1
            if re.match(r"^END\b", trimmed):
                depth -= 1
                if depth <= 0:
                    return i
        return len(lines) - 1

    def _find_keyword_line(self, lines: list[str], start: int, keyword: str) -> int:
        for i in range(start, len(lines)):
            if lines[i].strip().upper().startswith(keyword.upper()):
                return i
        return len(lines) - 1

    def _find_statement_end(self, lines: list[str], start: int) -> int:
        for i in range(start, len(lines)):
            if lines[i].strip().endswith(";"):
                return i
            if i > start and i + 1 < len(lines) and not lines[i + 1].strip():
                return i
        return start
