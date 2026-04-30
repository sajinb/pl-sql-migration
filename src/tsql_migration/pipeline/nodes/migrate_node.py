"""Node 6: MIGRATE — Convert each procedure to Java via LLM calls.

Routes to single LLM call for small procedures, chunked calls for large ones.
"""

from __future__ import annotations

import os
import re

from langchain_core.messages import HumanMessage, SystemMessage

from tsql_migration.chunker.sql_chunker import SqlChunker
from tsql_migration.state import MigratedProcedure, MigrationState


def migrate_node(state: MigrationState) -> dict:
    """Migrate all procedures in migration order."""
    config = state.config
    dialect = config.sql_dialect

    # Select prompts and edge-case context builder based on dialect
    if dialect == "oracle":
        from tsql_migration.pipeline.prompts.oracle_system_prompt import (
            ORACLE_SYSTEM_PROMPT as SYSTEM_PROMPT,
            build_oracle_migration_prompt as build_migration_prompt,
            build_oracle_edge_case_context as build_edge_case_context,
        )
    else:
        from tsql_migration.pipeline.prompts.system_prompt import (
            SYSTEM_PROMPT,
            build_migration_prompt,
        )
        from tsql_migration.pipeline.prompts.chunk_prompt import build_edge_case_context

    llm = _create_llm(config)
    chunker = SqlChunker(config.chunk_size_tokens, config.chunk_overlap_tokens, dialect=dialect)

    migrated: dict[str, MigratedProcedure] = dict(state.migrated_procedures)
    failed: dict[str, str] = dict(state.failed_procedures)

    for proc_name in state.migration_order:
        proc = state.procedures.get(proc_name)
        if not proc:
            print(f"[MIGRATE] SKIP: {proc_name} (external dependency)")
            continue

        print(f"[MIGRATE] Migrating: {proc_name} ({proc.total_line_count} lines)...")

        try:
            # Build dependency context
            dep_services = _build_dependency_context(proc, migrated)
            entity_sigs = _build_entity_signatures(state)
            edge_context = build_edge_case_context(
                proc_name,
                state.edge_case_report.linked_servers,
                state.edge_case_report.dynamic_sql_calls,
                state.edge_case_report.variable_calls,
            )

            if chunker.needs_chunking(proc, config.large_procedure_threshold):
                # Large procedure — chunked migration
                print(f"  Large procedure — chunking...")
                chunks = chunker.chunk(proc)
                print(f"  Split into {len(chunks)} chunks")
                generated_code = _migrate_chunked(
                    llm, proc, chunks, state.temp_table_mapping,
                    dep_services, entity_sigs, edge_context,
                )
            else:
                # Small procedure — single LLM call
                prompt = build_migration_prompt(
                    sql_content=proc.raw_sql,
                    procedure_name=proc_name,
                    temp_table_mapping=state.temp_table_mapping,
                    dependency_services=dep_services,
                    entity_signatures=entity_sigs,
                    edge_case_context=edge_context,
                )
                response = llm.invoke([
                    SystemMessage(content=SYSTEM_PROMPT),
                    HumanMessage(content=prompt),
                ])
                generated_code = response.content

            service_name = _to_service_name(proc_name)
            method_name = _to_method_name(proc_name)

            migrated[proc_name] = MigratedProcedure(
                procedure_name=proc_name,
                service_name=service_name,
                method_name=method_name,
                service_code=generated_code,
            )
            print(f"  SUCCESS: {proc_name} → {service_name}")

        except Exception as e:
            failed[proc_name] = str(e)
            print(f"  FAILED: {proc_name} — {e}")

    return {
        "migrated_procedures": migrated,
        "failed_procedures": failed,
    }


def _migrate_chunked(
    llm,
    proc,
    chunks,
    temp_table_mapping,
    dep_services,
    entity_sigs,
    edge_context,
) -> str:
    """Process procedure chunks sequentially, passing context forward."""
    composed_code = ""
    previous_output = ""

    for chunk in chunks:
        prompt = build_migration_prompt(
            sql_content=chunk.sql_content,
            procedure_name=chunk.procedure_name,
            temp_table_mapping=temp_table_mapping,
            dependency_services=dep_services,
            entity_signatures=entity_sigs,
            edge_case_context=edge_context,
            chunk_position=chunk.position,
            previous_chunk_output=previous_output,
        )

        response = llm.invoke([
            SystemMessage(content=SYSTEM_PROMPT),
            HumanMessage(content=prompt),
        ])

        chunk_code = response.content
        composed_code += chunk_code + "\n"
        previous_output = chunk_code
        print(f"    Chunk {chunk.index + 1}/{chunk.total} done")

    return composed_code


def _create_llm(config):
    """Create the appropriate LangChain LLM based on config."""
    if config.llm_provider == "openai":
        from langchain_openai import ChatOpenAI

        api_key = os.environ.get(config.llm_api_key_env, "")
        return ChatOpenAI(model=config.llm_model, api_key=api_key, temperature=0)
    else:
        from langchain_anthropic import ChatAnthropic

        api_key = os.environ.get(config.llm_api_key_env, "")
        return ChatAnthropic(model=config.llm_model, api_key=api_key, temperature=0)


def _build_dependency_context(
    proc, migrated: dict[str, MigratedProcedure]
) -> dict[str, str]:
    """Build context for already-migrated procedures this one calls."""
    deps: dict[str, str] = {}
    for called in proc.called_procedures:
        if called in migrated:
            m = migrated[called]
            deps[called] = f"{m.service_name}.{m.method_name}()"
    return deps


def _build_entity_signatures(state: MigrationState) -> dict[str, str]:
    """Build a summary of available entities for the LLM prompt."""
    sigs: dict[str, str] = {}
    for name, entity in state.entities.items():
        if entity.columns:
            cols = ", ".join(f"{c.name}:{c.java_type}" for c in entity.columns[:8])
            if len(entity.columns) > 8:
                cols += f", ... (+{len(entity.columns) - 8} more)"
            sigs[name] = f"{entity.entity_class_name} [{cols}]"
        else:
            sigs[name] = f"{entity.entity_class_name} [stub — columns TBD]"
    return sigs


def _to_service_name(proc_name: str) -> str:
    cleaned = re.sub(r"^(sp_|usp_|proc_)", "", proc_name)
    return _to_pascal_case(cleaned) + "Service"


def _to_method_name(proc_name: str) -> str:
    cleaned = re.sub(r"^(sp_|usp_|proc_)", "", proc_name)
    pascal = _to_pascal_case(cleaned)
    return pascal[0].lower() + pascal[1:]


def _to_pascal_case(name: str) -> str:
    return "".join(word.capitalize() for word in name.replace("-", "_").split("_"))
