"""SQL-aware text splitting for large stored procedures.

Uses LangChain's RecursiveCharacterTextSplitter with dialect-specific
separators to split large procedures into chunks that respect SQL
block boundaries.

Supported dialects: "tsql" (default), "oracle"
"""

from __future__ import annotations

from langchain_text_splitters import RecursiveCharacterTextSplitter

from tsql_migration.state import ProcedureMetadata, SqlBlock

# ---------------------------------------------------------------------------
# T-SQL separators
# ---------------------------------------------------------------------------
TSQL_SEPARATORS = [
    "\nBEGIN TRY",
    "\nBEGIN CATCH",
    "\nEND TRY",
    "\nEND CATCH",
    "\nBEGIN TRANSACTION",
    "\nCOMMIT",
    "\nROLLBACK",
    "\nDECLARE ",
    "\nCREATE TABLE #",
    "\nWHILE ",
    "\nIF ",
    "\nELSE",
    "\nINSERT ",
    "\nUPDATE ",
    "\nDELETE ",
    "\nMERGE ",
    "\nEXEC ",
    "\nSELECT ",
    "\nSET ",
    ";\n",
    "\nEND",
    "\n\n",
    "\n",
]

# Keep old name as alias so any external references still work
SQL_SEPARATORS = TSQL_SEPARATORS

# ---------------------------------------------------------------------------
# Oracle PL/SQL separators
# ---------------------------------------------------------------------------
ORACLE_SEPARATORS = [
    "\nEXCEPTION",
    "\nWHEN ",
    "\nEXECUTE IMMEDIATE",
    "\nBEGIN",
    "\nEND;",
    "\nEND LOOP;",
    "\nEND IF;",
    "\nEND CASE;",
    "\nFOR ",
    "\nWHILE ",
    "\nLOOP",
    "\nIF ",
    "\nELSIF ",
    "\nELSE",
    "\nINSERT ",
    "\nUPDATE ",
    "\nDELETE ",
    "\nMERGE ",
    "\nSELECT ",
    "\nOPEN ",
    "\nFETCH ",
    "\nCLOSE ",
    "\nRETURN ",
    ";\n",
    "\n\n",
    "\n",
]

# Approximate chars per token for estimation
CHARS_PER_TOKEN = 4


class SqlChunker:
    """Splits large stored procedures into LLM-friendly chunks.

    Args:
        chunk_size_tokens:   Maximum tokens per chunk (default 8000).
        chunk_overlap_tokens: Overlap between adjacent chunks (default 500).
        dialect:             ``"tsql"`` (default) or ``"oracle"``.
    """

    def __init__(
        self,
        chunk_size_tokens: int = 8000,
        chunk_overlap_tokens: int = 500,
        dialect: str = "tsql",
    ) -> None:
        self.chunk_size_chars = chunk_size_tokens * CHARS_PER_TOKEN
        self.chunk_overlap_chars = chunk_overlap_tokens * CHARS_PER_TOKEN
        self.dialect = dialect

        separators = ORACLE_SEPARATORS if dialect == "oracle" else TSQL_SEPARATORS

        self._splitter = RecursiveCharacterTextSplitter(
            separators=separators,
            chunk_size=self.chunk_size_chars,
            chunk_overlap=self.chunk_overlap_chars,
            length_function=len,
            keep_separator=True,
        )

    def needs_chunking(self, procedure: ProcedureMetadata, threshold: int = 2000) -> bool:
        """Return True if *procedure* exceeds *threshold* lines."""
        return procedure.total_line_count > threshold

    def chunk(self, procedure: ProcedureMetadata) -> list[ProcedureChunk]:
        """Split a procedure into chunks for sequential LLM processing.

        Each chunk includes:
        - The SQL fragment
        - Position metadata (first/middle/last/only)
        - Variables that flow in/out of the chunk
        """
        if not self.needs_chunking(procedure):
            return [
                ProcedureChunk(
                    procedure_name=procedure.procedure_name,
                    index=0,
                    total=1,
                    position="only",
                    sql_content=procedure.raw_sql,
                    input_variables=[],
                    output_variables=[],
                )
            ]

        # Use the procedure body (after AS/BEGIN) for splitting
        raw_chunks = self._splitter.split_text(procedure.raw_sql)

        if len(raw_chunks) <= 1:
            return [
                ProcedureChunk(
                    procedure_name=procedure.procedure_name,
                    index=0,
                    total=1,
                    position="only",
                    sql_content=procedure.raw_sql,
                    input_variables=[],
                    output_variables=[],
                )
            ]

        total = len(raw_chunks)
        chunks: list[ProcedureChunk] = []
        for i, text in enumerate(raw_chunks):
            if i == 0:
                position = "first"
            elif i == total - 1:
                position = "last"
            else:
                position = "middle"

            # Extract variables referenced and assigned in this chunk
            import re
            all_vars = list(dict.fromkeys(re.findall(r"@\w+", text)))
            assigned_vars = list(
                dict.fromkeys(
                    re.findall(r"(?i)SET\s+(@\w+)", text)
                    + re.findall(r"(?i)SELECT\s+(@\w+)\s*=", text)
                )
            )

            chunks.append(
                ProcedureChunk(
                    procedure_name=procedure.procedure_name,
                    index=i,
                    total=total,
                    position=position,
                    sql_content=text,
                    input_variables=all_vars,
                    output_variables=assigned_vars,
                )
            )

        return chunks

    def chunk_by_blocks(self, procedure: ProcedureMetadata, max_blocks: int = 5) -> list[ProcedureChunk]:
        """Alternative chunking strategy: group logical blocks.

        Groups the procedure's pre-parsed logical blocks into chunks
        of at most `max_blocks` blocks each. Preserves block boundaries
        better than character-based splitting.
        """
        blocks = procedure.logical_blocks
        if not blocks:
            return self.chunk(procedure)

        chunks: list[ProcedureChunk] = []
        current_blocks: list[SqlBlock] = []
        chunk_index = 0

        for block in blocks:
            current_blocks.append(block)
            if len(current_blocks) >= max_blocks:
                chunks.append(self._blocks_to_chunk(
                    procedure.procedure_name, current_blocks, chunk_index
                ))
                chunk_index += 1
                current_blocks = []

        if current_blocks:
            chunks.append(self._blocks_to_chunk(
                procedure.procedure_name, current_blocks, chunk_index
            ))

        # Set total and positions
        total = len(chunks)
        for i, chunk in enumerate(chunks):
            chunk.total = total
            if total == 1:
                chunk.position = "only"
            elif i == 0:
                chunk.position = "first"
            elif i == total - 1:
                chunk.position = "last"
            else:
                chunk.position = "middle"

        return chunks

    def _blocks_to_chunk(
        self, proc_name: str, blocks: list[SqlBlock], index: int
    ) -> ProcedureChunk:
        content = "\n\n".join(b.content for b in blocks)
        input_vars: list[str] = []
        output_vars: list[str] = []
        for b in blocks:
            input_vars.extend(v for v in b.input_variables if v not in input_vars)
            output_vars.extend(v for v in b.output_variables if v not in output_vars)

        return ProcedureChunk(
            procedure_name=proc_name,
            index=index,
            total=0,  # set later
            position="middle",  # set later
            sql_content=content,
            input_variables=input_vars,
            output_variables=output_vars,
        )


class ProcedureChunk:
    """A chunk of a stored procedure for LLM processing."""

    def __init__(
        self,
        procedure_name: str,
        index: int,
        total: int,
        position: str,  # only, first, middle, last
        sql_content: str,
        input_variables: list[str],
        output_variables: list[str],
    ) -> None:
        self.procedure_name = procedure_name
        self.index = index
        self.total = total
        self.position = position
        self.sql_content = sql_content
        self.input_variables = input_variables
        self.output_variables = output_variables

    def build_context(
        self,
        temp_table_mapping: dict[str, str],
        entity_signatures: dict[str, str],
        previous_output: str = "",
    ) -> str:
        """Build the context string for the LLM prompt."""
        parts: list[str] = []
        parts.append(f"Procedure: {self.procedure_name}")
        parts.append(f"Chunk {self.index + 1} of {self.total} (position: {self.position})")
        parts.append("")

        if temp_table_mapping:
            parts.append("Temp table → staging table mapping:")
            for orig, staging in temp_table_mapping.items():
                parts.append(f"  {orig} → {staging}")
            parts.append("")

        if entity_signatures:
            parts.append("Available JPA entities (already generated):")
            for table, sig in entity_signatures.items():
                parts.append(f"  {table}: {sig}")
            parts.append("")

        if self.input_variables:
            parts.append(f"Variables referenced: {', '.join(self.input_variables)}")
        if self.output_variables:
            parts.append(f"Variables assigned: {', '.join(self.output_variables)}")
        parts.append("")

        if previous_output:
            parts.append("Previously generated Java code from prior chunks:")
            parts.append(previous_output)
            parts.append("")

        parts.append("T-SQL to migrate:")
        parts.append(self.sql_content)

        return "\n".join(parts)
