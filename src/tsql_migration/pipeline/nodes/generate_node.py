"""Node 7: GENERATE — Write all generated Java files, DDLs, and reports to disk."""

from __future__ import annotations

import json
from pathlib import Path

from tsql_migration.schema.entity_generator import EntityGenerator
from tsql_migration.state import MigrationState


def generate_node(state: MigrationState) -> dict:
    """Write all output files to the configured output directory."""
    config = state.config
    output_dir = Path(config.output_dir)
    output_files: list[str] = []

    # Create output directories
    dirs = ["entity", "repository", "service", "dto", "ddl", "cleanup"]
    for d in dirs:
        (output_dir / d).mkdir(parents=True, exist_ok=True)

    # --- Entity classes ---
    generator = EntityGenerator(config.base_package)
    for name, entity in state.entities.items():
        if entity.columns:  # Only write entities with known columns
            entity_code = generator.generate_entity(entity)
            path = output_dir / "entity" / f"{entity.entity_class_name}.java"
            path.write_text(entity_code)
            output_files.append(str(path))

    # --- Repository interfaces ---
    for name, entity in state.entities.items():
        if entity.columns:
            repo_code = generator.generate_repository(entity)
            path = output_dir / "repository" / f"{entity.entity_class_name}Repository.java"
            path.write_text(repo_code)
            output_files.append(str(path))

    # --- Migrated service classes ---
    for proc_name, migrated in state.migrated_procedures.items():
        path = output_dir / "service" / f"{migrated.service_name}.java"
        path.write_text(migrated.service_code)
        output_files.append(str(path))

    # --- Staging table DDLs ---
    if state.staging_ddls:
        ddl_content = "\n\n".join(state.staging_ddls)
        path = output_dir / "ddl" / "staging_tables.sql"
        path.write_text(ddl_content)
        output_files.append(str(path))

    # --- Cleanup service ---
    if state.temp_table_mapping:
        cleanup_code = _generate_cleanup_service(state, config.base_package)
        path = output_dir / "cleanup" / "StagingTableCleanupService.java"
        path.write_text(cleanup_code)
        output_files.append(str(path))

        batch_id_code = _generate_batch_id_util(config.base_package)
        path = output_dir / "cleanup" / "BatchIdGenerator.java"
        path.write_text(batch_id_code)
        output_files.append(str(path))

    # --- Migration report ---
    report = _build_report(state)
    report_path = output_dir / "migration_report.json"
    report_path.write_text(json.dumps(report, indent=2))
    output_files.append(str(report_path))

    # --- Edge case report ---
    if state.edge_case_report.unresolved_count > 0:
        edge_report = {
            "linked_servers": [ls.model_dump() for ls in state.edge_case_report.linked_servers],
            "dynamic_sql_calls": [ds.model_dump() for ds in state.edge_case_report.dynamic_sql_calls],
            "variable_calls": [vc.model_dump() for vc in state.edge_case_report.variable_calls],
            "unresolved_count": state.edge_case_report.unresolved_count,
        }
        edge_path = output_dir / "edge_case_report.json"
        edge_path.write_text(json.dumps(edge_report, indent=2))
        output_files.append(str(edge_path))

    print(f"[GENERATE] Wrote {len(output_files)} files to {output_dir}/")
    for f in output_files:
        print(f"  {f}")

    return {"output_files": output_files}


def _build_report(state: MigrationState) -> dict:
    return {
        "total_procedures": len(state.procedures),
        "migrated": len(state.migrated_procedures),
        "failed": len(state.failed_procedures),
        "migration_order": state.migration_order,
        "entities_generated": len(state.entities),
        "staging_tables": len(state.staging_ddls),
        "schema_source": state.schema_source,
        "edge_cases": {
            "linked_servers": len(state.edge_case_report.linked_servers),
            "dynamic_sql": len(state.edge_case_report.dynamic_sql_calls),
            "variable_calls": len(state.edge_case_report.variable_calls),
            "unresolved": state.edge_case_report.unresolved_count,
        },
        "migrated_procedures": {
            name: {
                "service_name": m.service_name,
                "method_name": m.method_name,
            }
            for name, m in state.migrated_procedures.items()
        },
        "failed_procedures": state.failed_procedures,
    }


def _generate_cleanup_service(state: MigrationState, base_package: str) -> str:
    cleanup_lines: list[str] = []
    for orig, staging in state.temp_table_mapping.items():
        cleanup_lines.append(
            f'        jdbcTemplate.update("DELETE FROM [{staging}] WHERE batch_id = ?", batchId);'
        )

    scheduled_lines: list[str] = []
    for orig, staging in state.temp_table_mapping.items():
        scheduled_lines.append(
            f'        totalDeleted += jdbcTemplate.update(\n'
            f'            "DELETE FROM [{staging}] WHERE created_at < DATEADD(HOUR, -" + STALE_HOURS + ", GETDATE())");'
        )

    return f"""\
package {base_package}.cleanup;

import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Service;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

@Service
public class StagingTableCleanupService {{

    private static final Logger log = LoggerFactory.getLogger(StagingTableCleanupService.class);
    private static final int STALE_HOURS = 24;

    private final JdbcTemplate jdbcTemplate;

    public StagingTableCleanupService(JdbcTemplate jdbcTemplate) {{
        this.jdbcTemplate = jdbcTemplate;
    }}

    public void cleanupBatch(String batchId) {{
{chr(10).join(cleanup_lines)}
        log.debug("Cleaned up staging data for batch: {{}}", batchId);
    }}

    @Scheduled(fixedRate = 3600000)
    public void cleanupStaleData() {{
        log.info("Running scheduled staging table cleanup...");
        int totalDeleted = 0;
{chr(10).join(scheduled_lines)}
        log.info("Cleaned up {{}} stale staging records", totalDeleted);
    }}
}}
"""


def _generate_batch_id_util(base_package: str) -> str:
    return f"""\
package {base_package}.cleanup;

import java.util.UUID;

public final class BatchIdGenerator {{

    private BatchIdGenerator() {{}}

    public static String generate() {{
        return UUID.randomUUID().toString();
    }}
}}
"""
