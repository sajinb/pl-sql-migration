"""Oracle PL/SQL stored procedure and package body parser.

Handles:
- Standalone procedures/functions:
    CREATE [OR REPLACE] PROCEDURE [schema.]name(...)
    CREATE [OR REPLACE] FUNCTION  [schema.]name(...) RETURN type
- Package bodies (unwrapped into individual ProcedureMetadata objects):
    CREATE [OR REPLACE] PACKAGE BODY [schema.]pkg_name AS
        PROCEDURE proc1(...) AS ... END proc1;
        FUNCTION  func1(...) RETURN type AS ... END func1;
    END pkg_name;

Files may use extensions .sql, .pkb (package body), .pks (package spec — skipped).

Key Oracle ↔ T-SQL mapping for parsing:
  @param IN type          →  p_param IN type
  @param OUTPUT           →  p_param OUT/IN OUT type
  #TempTable              →  GLOBAL TEMPORARY TABLE (DDL), or PL/SQL collection
  EXEC sp_name            →  direct call: pkg.proc_name(args); or CALL proc_name(args)
  EXEC(@sql)              →  EXECUTE IMMEDIATE sql_string
  BEGIN TRY / END CATCH   →  BEGIN / EXCEPTION WHEN ... END
  @@ROWCOUNT              →  SQL%ROWCOUNT
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

import sqlglot
from sqlglot import exp

from tsql_migration.state import (
    ParameterMetadata,
    ProcedureMetadata,
    SqlBlock,
    TempTableColumn,
    TempTableUsage,
)

# Oracle types that are commonly used in PL/SQL
_ORACLE_TYPES = {
    "NUMBER", "INTEGER", "INT", "SMALLINT", "FLOAT", "REAL", "DOUBLE",
    "VARCHAR2", "VARCHAR", "NVARCHAR2", "CHAR", "NCHAR",
    "DATE", "TIMESTAMP", "INTERVAL",
    "CLOB", "NCLOB", "BLOB", "RAW", "LONG",
    "BOOLEAN", "BINARY_INTEGER", "PLS_INTEGER",
    "ROWID", "UROWID", "SYS_REFCURSOR",
    "XMLTYPE",
}


class OraclePlSqlParser:
    """Parses Oracle PL/SQL files into ProcedureMetadata.

    Package bodies are unwrapped: each PROCEDURE/FUNCTION inside becomes
    its own ProcedureMetadata with name ``<pkg>.<proc>``.

    The public interface mirrors TSqlParser — both expose ``parse_directory``
    returning ``dict[str, ProcedureMetadata]``.
    """

    def __init__(self) -> None:
        self.dialect = "oracle"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def parse_directory(self, directory: Path) -> dict[str, ProcedureMetadata]:
        """Parse all PL/SQL files in *directory*.

        Recognised extensions: .sql, .pkb (package body), .prc (procedure).
        Package spec files (.pks) are skipped — they contain no body code.
        """
        results: dict[str, ProcedureMetadata] = {}
        patterns = ["*.sql", "*.pkb", "*.prc"]
        for pattern in patterns:
            for sql_file in sorted(directory.glob(pattern)):
                for meta in self.parse_file(sql_file):
                    results[meta.procedure_name] = meta
        return results

    def parse_file(self, file_path: Path) -> list[ProcedureMetadata]:
        """Parse a single file.  Returns a list because packages yield N procedures."""
        raw_sql = file_path.read_text(encoding="utf-8-sig")
        results = self.parse_sql(raw_sql)
        for r in results:
            r.source_file = str(file_path)
        return results

    def parse_sql(self, raw_sql: str) -> list[ProcedureMetadata]:
        """Parse Oracle PL/SQL source.  Returns list (package = multiple procedures)."""
        # Skip package specs — they have no executable code
        if re.search(r"(?i)CREATE\s+(?:OR\s+REPLACE\s+)?PACKAGE\s+(?!BODY)", raw_sql):
            return []

        if re.search(r"(?i)CREATE\s+(?:OR\s+REPLACE\s+)?PACKAGE\s+BODY", raw_sql):
            return self._parse_package_body(raw_sql)

        meta = self._parse_standalone(raw_sql)
        return [meta] if meta else []

    # ------------------------------------------------------------------
    # Package body unwrapping
    # ------------------------------------------------------------------

    def _parse_package_body(self, raw_sql: str) -> list[ProcedureMetadata]:
        """Unwrap a package body into individual ProcedureMetadata objects."""
        pkg_match = re.search(
            r"(?i)CREATE\s+(?:OR\s+REPLACE\s+)?PACKAGE\s+BODY\s+"
            r"(?:\w+\.)?(\w+)\s+(?:AS|IS)",
            raw_sql,
        )
        if not pkg_match:
            return []

        pkg_name = pkg_match.group(1)
        body_start = pkg_match.end()

        # Find all top-level PROCEDURE / FUNCTION headers within the package body.
        # We look for them at a position where they're not inside a nested block.
        proc_starts = self._find_top_level_units(raw_sql, body_start)
        if not proc_starts:
            return []

        results: list[ProcedureMetadata] = []
        for i, (unit_start, unit_kind, unit_name) in enumerate(proc_starts):
            # Unit text ends where the next unit begins, or at the package END
            if i + 1 < len(proc_starts):
                unit_end = proc_starts[i + 1][0]
            else:
                # Find the closing END of this unit, then stop
                unit_end = self._find_unit_end(raw_sql, unit_start)

            unit_sql = raw_sql[unit_start:unit_end].strip()
            meta = self._parse_standalone(unit_sql, package_name=pkg_name)
            if meta:
                results.append(meta)

        return results

    def _find_top_level_units(
        self, sql: str, from_pos: int
    ) -> list[tuple[int, str, str]]:
        """Find positions of top-level PROCEDURE/FUNCTION declarations.

        We track BEGIN/END depth so that nested blocks are skipped.
        Only units at depth 0 (package level) are returned.
        """
        units: list[tuple[int, str, str]] = []
        depth = 0
        pos = from_pos

        token_re = re.compile(
            r"\b(BEGIN|END|PROCEDURE|FUNCTION)\b",
            re.IGNORECASE,
        )

        for m in token_re.finditer(sql, from_pos):
            kw = m.group(1).upper()
            abs_pos = m.start()

            # Skip END IF / END LOOP / END CASE / END WHILE — they don't change depth
            tail = sql[m.end():m.end() + 10].strip().upper()
            if kw == "END" and re.match(r"(IF|LOOP|CASE|WHILE)\b", tail):
                continue

            if kw == "BEGIN":
                depth += 1
            elif kw == "END":
                if depth > 0:
                    depth -= 1
            elif kw in ("PROCEDURE", "FUNCTION") and depth == 0:
                # Extract the name following the keyword
                name_m = re.match(r"\s+(\w+)", sql[m.end():m.end() + 60])
                if name_m:
                    units.append((abs_pos, kw, name_m.group(1)))

        return units

    def _find_unit_end(self, sql: str, unit_start: int) -> int:
        """Find the position just after ``END [name];`` for the unit starting at *unit_start*."""
        depth = 0
        begin_seen = False

        token_re = re.compile(r"\b(BEGIN|END)\b", re.IGNORECASE)
        for m in token_re.finditer(sql, unit_start):
            kw = m.group(1).upper()

            # Skip END IF / END LOOP / END CASE
            tail = sql[m.end():m.end() + 10].strip().upper()
            if kw == "END" and re.match(r"(IF|LOOP|CASE|WHILE)\b", tail):
                continue

            if kw == "BEGIN":
                depth += 1
                begin_seen = True
            elif kw == "END" and begin_seen:
                depth -= 1
                if depth == 0:
                    # Consume optional name and semicolon
                    rest = sql[m.end():]
                    semi = re.match(r"[\s\w]*;", rest)
                    return m.end() + (semi.end() if semi else 0)

        return len(sql)

    # ------------------------------------------------------------------
    # Standalone procedure / function parsing
    # ------------------------------------------------------------------

    def _parse_standalone(
        self, raw_sql: str, package_name: Optional[str] = None
    ) -> Optional[ProcedureMetadata]:
        """Parse a single CREATE PROCEDURE / FUNCTION block."""
        proc_name, schema_name = self._extract_name(raw_sql, package_name)
        if proc_name == "UnknownProcedure" and not raw_sql.strip():
            return None

        parameters = self._extract_parameters(raw_sql)
        declare_vars = self._extract_declare_section(raw_sql)
        temp_tables = self._extract_gtt_usage(raw_sql, declare_vars)
        called_procedures = self._extract_called_procedures(raw_sql)
        referenced_tables = self._extract_referenced_tables(raw_sql, temp_tables)
        logical_blocks = self._extract_logical_blocks(raw_sql)
        lines = raw_sql.split("\n")

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

    # ------------------------------------------------------------------
    # Name extraction
    # ------------------------------------------------------------------

    def _extract_name(
        self, sql: str, package_name: Optional[str]
    ) -> tuple[str, str]:
        """Extract procedure/function name and schema."""
        match = re.search(
            r"(?i)(?:PROCEDURE|FUNCTION)\s+(?:(\w+)\.)?(\w+)",
            sql,
        )
        if not match:
            return "UnknownProcedure", package_name or "dbo"

        schema = match.group(1) or package_name or "dbo"
        name = match.group(2)
        full_name = f"{package_name}.{name}" if package_name else name
        return full_name, schema

    # ------------------------------------------------------------------
    # Parameter extraction
    # ------------------------------------------------------------------

    def _extract_parameters(self, sql: str) -> list[ParameterMetadata]:
        """Extract parameters from the procedure/function signature.

        Handles:
            p_name  IN  NUMBER
            p_name  OUT VARCHAR2
            p_name  IN OUT  CLOB
            p_name  IN  NUMBER DEFAULT 0
            p_name  NUMBER  (direction defaults to IN)
        """
        # Find the parameter list between first ( and its matching )
        paren_start = sql.find("(")
        if paren_start == -1:
            return []

        paren_end = self._find_matching_paren(sql, paren_start)
        param_text = sql[paren_start + 1 : paren_end]

        if not param_text.strip():
            return []

        params: list[ParameterMetadata] = []
        # Split on commas that are NOT inside nested parens (e.g. VARCHAR2(100))
        for raw_param in self._split_params(param_text):
            raw_param = raw_param.strip()
            if not raw_param:
                continue

            p = self._parse_single_param(raw_param)
            if p:
                params.append(p)

        return params

    def _parse_single_param(self, text: str) -> Optional[ParameterMetadata]:
        """Parse one parameter definition."""
        # Pattern: name [IN | OUT | IN OUT] type [(size)] [DEFAULT expr]
        m = re.match(
            r"(?i)(\w+)\s+"
            r"(IN\s+OUT|IN|OUT)?\s*"
            r"([\w%]+(?:\([\w,\s]+\))?)"
            r"(?:\s+DEFAULT\s+(.+))?$",
            text.strip(),
        )
        if not m:
            return None

        name = m.group(1)
        direction_raw = (m.group(2) or "IN").strip().upper()
        direction = "INOUT" if "OUT" in direction_raw and "IN" in direction_raw else direction_raw
        sql_type = m.group(3).strip()
        default = m.group(4).strip() if m.group(4) else None

        return ParameterMetadata(
            name=name,
            sql_type=sql_type,
            direction=direction,
            default_value=default,
        )

    # ------------------------------------------------------------------
    # DECLARE section
    # ------------------------------------------------------------------

    def _extract_declare_section(self, sql: str) -> list[str]:
        """Extract variable names declared between IS/AS and BEGIN."""
        # Find IS or AS keyword (end of procedure header)
        is_as = re.search(r"(?i)\b(?:IS|AS)\b", sql)
        begin_kw = re.search(r"(?i)\bBEGIN\b", sql)

        if not is_as or not begin_kw:
            return []

        declare_section = sql[is_as.end() : begin_kw.start()]
        var_names: list[str] = []

        for line in declare_section.split("\n"):
            stripped = line.strip()
            if not stripped or stripped.startswith("--"):
                continue
            # Variable declaration: v_name type; or v_name type := value;
            # Also TYPE declarations, CURSOR declarations
            m = re.match(r"(?i)(\w+)\s+\w+", stripped)
            if m:
                name = m.group(1).upper()
                # Skip Oracle keywords that appear in declare sections
                if name not in ("TYPE", "SUBTYPE", "CURSOR", "PRAGMA", "PROCEDURE", "FUNCTION"):
                    var_names.append(m.group(1))

        return var_names

    # ------------------------------------------------------------------
    # GTT (Global Temporary Table) detection
    # ------------------------------------------------------------------

    def _extract_gtt_usage(
        self, sql: str, declare_vars: list[str]
    ) -> list[TempTableUsage]:
        """Detect Oracle Global Temporary Table references.

        Oracle GTTs are pre-created DDL objects, not inline syntax like #temp.
        We detect:
        1. CREATE GLOBAL TEMPORARY TABLE within the same file
        2. PL/SQL collection TYPE ... IS TABLE OF ... (in-memory table equivalent)
        """
        temp_tables: list[TempTableUsage] = []
        seen: set[str] = set()

        # 1. Inline GTT DDL (sometimes included in same file)
        for m in re.finditer(
            r"(?i)CREATE\s+GLOBAL\s+TEMPORARY\s+TABLE\s+(\w+)\s*\(([^)]+)\)",
            sql,
        ):
            gtt_name = m.group(1)
            if gtt_name in seen:
                continue
            seen.add(gtt_name)
            columns = self._parse_oracle_column_defs(m.group(2))
            temp_tables.append(
                TempTableUsage(
                    original_name=gtt_name,
                    staging_table_name=self._to_staging_name(gtt_name),
                    columns=columns,
                    create_statement=m.group(0),
                    is_global_temp=True,
                )
            )

        # 2. PL/SQL TABLE-OF collection types used as in-memory result sets
        for m in re.finditer(
            r"(?i)TYPE\s+(\w+)\s+IS\s+TABLE\s+OF\s+(\w+(?:\.\w+)?(?:%ROWTYPE|%TYPE)?)",
            sql,
        ):
            type_name = m.group(1)
            if type_name in seen:
                continue
            seen.add(type_name)
            temp_tables.append(
                TempTableUsage(
                    original_name=type_name,
                    staging_table_name=self._to_staging_name(type_name),
                    columns=[],
                    create_statement=m.group(0),
                    is_global_temp=False,
                )
            )

        return temp_tables

    # ------------------------------------------------------------------
    # Procedure call detection
    # ------------------------------------------------------------------

    def _extract_called_procedures(self, sql: str) -> list[str]:
        """Detect procedure calls in Oracle PL/SQL.

        Detects:
        - CALL proc_name(...)               — explicit CALL syntax
        - pkg_name.proc_name(...)           — package-qualified call
        - schema.pkg_name.proc_name(...)    — schema + package qualified
        """
        called: list[str] = []
        body = self._get_body(sql)

        # 1. Explicit CALL statement
        for m in re.finditer(
            r"(?i)\bCALL\s+(?:(\w+)\.)?(?:(\w+)\.)?(\w+)\s*\(",
            body,
        ):
            proc = m.group(3)
            pkg = m.group(2)
            name = f"{pkg}.{proc}" if pkg else proc
            if name not in called:
                called.append(name)

        # 2. Package-qualified calls: pkg.proc(...) on a statement line
        # Heuristic: identifier.identifier( at start of statement (after semicolon, or at line start)
        for m in re.finditer(
            r"(?i)(?:^|;\s*)(\w+)\.(\w+)\s*\(",
            body,
            re.MULTILINE,
        ):
            pkg, proc = m.group(1), m.group(2)
            # Filter out common non-procedure qualified names
            if pkg.upper() in ("DBMS_OUTPUT", "DBMS_SQL", "UTL_FILE", "UTL_HTTP",
                                "SYS", "STANDARD", "DUAL"):
                continue
            name = f"{pkg}.{proc}"
            if name not in called:
                called.append(name)

        return called

    # ------------------------------------------------------------------
    # Referenced table extraction
    # ------------------------------------------------------------------

    def _extract_referenced_tables(
        self, sql: str, temp_tables: list[TempTableUsage]
    ) -> list[str]:
        """Extract table names from DML statements using sqlglot oracle dialect."""
        temp_names = {t.original_name.upper() for t in temp_tables}
        tables: list[str] = []

        body = self._get_body(sql)

        try:
            for statement in sqlglot.parse(
                body, dialect="oracle", error_level=sqlglot.ErrorLevel.IGNORE
            ):
                if statement is None:
                    continue
                for table in statement.find_all(exp.Table):
                    name = table.name
                    if (
                        name
                        and name.upper() not in temp_names
                        and name.upper() not in ("DUAL",)
                        and not re.search(r"@", name)  # skip db link refs
                        and name not in tables
                    ):
                        tables.append(name)
        except Exception:
            tables = self._extract_tables_regex(body, temp_names)

        return tables

    def _extract_tables_regex(
        self, sql: str, temp_names: set[str]
    ) -> list[str]:
        """Regex fallback for table extraction."""
        tables: list[str] = []
        for m in re.finditer(
            r"(?i)(?:FROM|JOIN|INTO|UPDATE|DELETE\s+FROM|MERGE\s+INTO?)\s+"
            r"(?:(\w+)\.)?(\w+)(?!\s*\()",  # exclude function calls
            sql,
        ):
            table = m.group(2)
            if (
                table
                and table.upper() not in temp_names
                and table.upper() not in ("DUAL",)
                and "@" not in table
                and table not in tables
            ):
                tables.append(table)
        return tables

    # ------------------------------------------------------------------
    # Logical block extraction
    # ------------------------------------------------------------------

    def _extract_logical_blocks(self, sql: str) -> list[SqlBlock]:
        """Break the procedure body into logical blocks for chunking."""
        body = self._get_body(sql)
        lines = body.split("\n")
        blocks: list[SqlBlock] = []
        block_counter = 0
        i = 0

        while i < len(lines):
            trimmed = lines[i].strip().upper()

            if not trimmed or trimmed.startswith("--"):
                i += 1
                continue

            if re.match(r"^(V_\w+|L_\w+|\w+)\s+\w+.*:=|^\w+\s+\w+.*;$", trimmed):
                # Variable declaration or simple assignment
                end = self._find_contiguous_declarations(lines, i)
                blocks.append(self._make_block(f"block_{block_counter}", "DECLARATION", lines, i, end))
                block_counter += 1
                i = end + 1
            elif trimmed.startswith("BEGIN"):
                end = self._find_matching_end(lines, i)
                blocks.append(self._make_block(f"block_{block_counter}", "BEGIN_END", lines, i, end))
                block_counter += 1
                i = end + 1
            elif trimmed.startswith("EXCEPTION"):
                end = self._find_keyword_line(lines, i + 1, "END")
                blocks.append(self._make_block(f"block_{block_counter}", "EXCEPTION_BLOCK", lines, i, end))
                block_counter += 1
                i = end + 1
            elif trimmed.startswith("FOR ") and "LOOP" in trimmed:
                end = self._find_end_loop(lines, i)
                blocks.append(self._make_block(f"block_{block_counter}", "CURSOR_LOOP", lines, i, end))
                block_counter += 1
                i = end + 1
            elif trimmed.startswith("WHILE ") and "LOOP" in trimmed:
                end = self._find_end_loop(lines, i)
                blocks.append(self._make_block(f"block_{block_counter}", "WHILE_LOOP", lines, i, end))
                block_counter += 1
                i = end + 1
            elif trimmed.startswith("LOOP"):
                end = self._find_end_loop(lines, i)
                blocks.append(self._make_block(f"block_{block_counter}", "LOOP", lines, i, end))
                block_counter += 1
                i = end + 1
            elif trimmed.startswith("IF "):
                end = self._find_end_if(lines, i)
                blocks.append(self._make_block(f"block_{block_counter}", "IF_ELSE", lines, i, end))
                block_counter += 1
                i = end + 1
            elif trimmed.startswith("EXECUTE IMMEDIATE"):
                end = self._find_statement_end(lines, i)
                blocks.append(self._make_block(f"block_{block_counter}", "DYNAMIC_SQL", lines, i, end))
                block_counter += 1
                i = end + 1
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
            elif trimmed.startswith("OPEN ") or trimmed.startswith("FETCH ") or trimmed.startswith("CLOSE "):
                blocks.append(self._make_block(f"block_{block_counter}", "CURSOR_OP", lines, i, i))
                block_counter += 1
                i += 1
            elif trimmed.startswith("RETURN"):
                blocks.append(self._make_block(f"block_{block_counter}", "RETURN_BLOCK", lines, i, i))
                block_counter += 1
                i += 1
            else:
                blocks.append(self._make_block(f"block_{block_counter}", "OTHER", lines, i, i))
                block_counter += 1
                i += 1

        return blocks

    # ------------------------------------------------------------------
    # Helper utilities
    # ------------------------------------------------------------------

    def _get_body(self, sql: str) -> str:
        """Return the executable body of the procedure (between BEGIN and END)."""
        begin_m = re.search(r"(?i)\bBEGIN\b", sql)
        if not begin_m:
            return sql
        # Find the outermost END
        end_pos = self._find_unit_end(sql, begin_m.start())
        return sql[begin_m.start() : end_pos]

    def _find_matching_paren(self, sql: str, open_pos: int) -> int:
        """Find the closing ) that matches the ( at open_pos."""
        depth = 0
        for i in range(open_pos, len(sql)):
            if sql[i] == "(":
                depth += 1
            elif sql[i] == ")":
                depth -= 1
                if depth == 0:
                    return i
        return len(sql) - 1

    def _split_params(self, param_text: str) -> list[str]:
        """Split parameter text on commas, respecting nested parentheses."""
        parts: list[str] = []
        depth = 0
        current = ""
        for ch in param_text:
            if ch == "(":
                depth += 1
                current += ch
            elif ch == ")":
                depth -= 1
                current += ch
            elif ch == "," and depth == 0:
                parts.append(current)
                current = ""
            else:
                current += ch
        if current.strip():
            parts.append(current)
        return parts

    def _parse_oracle_column_defs(self, column_text: str) -> list[TempTableColumn]:
        """Parse column definitions from a CREATE TABLE or GTT statement."""
        columns: list[TempTableColumn] = []
        for col_def in column_text.split(","):
            col_def = col_def.strip()
            if not col_def:
                continue
            parts = col_def.split(None, 2)
            if len(parts) >= 2:
                name = parts[0].strip('"')
                col_type = parts[1].split("(")[0]
                nullable = "NOT NULL" not in col_def.upper()
                columns.append(TempTableColumn(name=name, sql_type=col_type, nullable=nullable))
        return columns

    def _to_staging_name(self, name: str) -> str:
        """Convert a GTT or collection name to a staging table name."""
        cleaned = re.sub(r"^(t_|gtt_|tmp_|temp_)", "", name, flags=re.IGNORECASE)
        snake = re.sub(r"([a-z])([A-Z])", r"\1_\2", cleaned).lower()
        return f"stg_{snake}"

    def _make_block(
        self, block_id: str, block_type: str, lines: list[str], start: int, end: int
    ) -> SqlBlock:
        end = min(end, len(lines) - 1)
        content = "\n".join(lines[start : end + 1])
        # Oracle uses := for assignment; variables have no @ prefix
        all_vars = list(dict.fromkeys(re.findall(r"\bv_\w+\b|\bl_\w+\b|\bp_\w+\b", content)))
        assigned_vars = list(
            dict.fromkeys(re.findall(r"(\b(?:v_|l_)\w+)\s*:=", content))
        )
        return SqlBlock(
            block_id=block_id,
            block_type=block_type,
            content=content,
            start_line=start,
            end_line=end,
            input_variables=all_vars,
            output_variables=assigned_vars,
        )

    def _find_contiguous_declarations(self, lines: list[str], start: int) -> int:
        """Find end of a run of declaration/assignment lines."""
        i = start
        while i + 1 < len(lines):
            next_line = lines[i + 1].strip().upper()
            if not next_line or next_line.startswith("--"):
                i += 1
                continue
            # Stop when we hit a control-flow keyword
            if re.match(
                r"^(BEGIN|END|IF|FOR|WHILE|LOOP|EXCEPTION|EXECUTE|"
                r"SELECT|INSERT|UPDATE|DELETE|MERGE|OPEN|FETCH|CLOSE|RETURN)\b",
                next_line,
            ):
                break
            i += 1
        return i

    def _find_matching_end(self, lines: list[str], start: int) -> int:
        """Find END matching the BEGIN at *start*, tracking nesting."""
        depth = 0
        for i in range(start, len(lines)):
            upper = lines[i].strip().upper()
            if re.match(r"^BEGIN\b", upper):
                depth += 1
            if re.match(r"^END\b", upper):
                tail = upper[3:].strip()
                if not re.match(r"^(IF|LOOP|CASE|WHILE)\b", tail):
                    depth -= 1
                    if depth <= 0:
                        return i
        return len(lines) - 1

    def _find_end_loop(self, lines: list[str], start: int) -> int:
        for i in range(start + 1, len(lines)):
            if re.match(r"(?i)^\s*END\s+LOOP\b", lines[i]):
                return i
        return len(lines) - 1

    def _find_end_if(self, lines: list[str], start: int) -> int:
        depth = 0
        for i in range(start, len(lines)):
            upper = lines[i].strip().upper()
            if re.match(r"^IF\b", upper):
                depth += 1
            if re.match(r"^END\s+IF\b", upper):
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
        """Find end of a SQL statement (terminated by ;)."""
        for i in range(start, len(lines)):
            if lines[i].rstrip().endswith(";"):
                return i
            if i > start and i + 1 < len(lines) and not lines[i + 1].strip():
                return i
        return start
