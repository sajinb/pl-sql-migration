"""CLI entry point for the T-SQL to Spring Boot migration tool."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from tsql_migration.config import load_config, MigrationConfig
from tsql_migration.pipeline.graph import compile_pipeline
from tsql_migration.state import MigrationState


def main() -> None:
    parser = argparse.ArgumentParser(
        description="T-SQL to Spring Boot 3 / Java 21 Migration Tool",
    )
    parser.add_argument(
        "sql_input_dir",
        nargs="?",
        help="Directory containing .sql stored procedure files",
    )
    parser.add_argument(
        "--config", "-c",
        default="config.yaml",
        help="Path to config.yaml (default: config.yaml)",
    )
    parser.add_argument("--output", "-o", help="Output directory")
    parser.add_argument("--provider", choices=["anthropic", "openai"], help="LLM provider")
    parser.add_argument("--model", help="LLM model name")
    parser.add_argument("--threshold", type=int, help="Large procedure line threshold")
    parser.add_argument("--jdbc", action="store_true", help="Use JdbcTemplate instead of JPA")
    parser.add_argument(
        "--checkpoint",
        action="store_true",
        help="Enable LangGraph checkpointing for resume on failure",
    )

    args = parser.parse_args()

    # Load config
    config = load_config(args.config)

    # Override config with CLI args
    if args.sql_input_dir:
        config = config.model_copy(update={"sql_input_dir": args.sql_input_dir})
    if args.output:
        config = config.model_copy(update={"output_dir": args.output})
    if args.provider:
        config = config.model_copy(update={"llm_provider": args.provider})
    if args.model:
        config = config.model_copy(update={"llm_model": args.model})
    if args.threshold:
        config = config.model_copy(update={"large_procedure_threshold": args.threshold})
    if args.jdbc:
        config = config.model_copy(update={"use_jdbc_template": True})

    # Validate input directory
    sql_dir = Path(config.sql_input_dir)
    if not sql_dir.exists():
        print(f"Error: SQL input directory not found: {sql_dir}")
        sys.exit(1)

    sql_files = list(sql_dir.glob("*.sql"))
    if not sql_files:
        print(f"Error: No .sql files found in: {sql_dir}")
        sys.exit(1)

    # Build pipeline
    print("=" * 60)
    print("T-SQL to Spring Boot 3 Migration Tool")
    print("=" * 60)
    print(f"Input:     {config.sql_input_dir}")
    print(f"Output:    {config.output_dir}")
    print(f"LLM:       {config.llm_provider} / {config.llm_model}")
    print(f"Threshold: {config.large_procedure_threshold} lines")
    print(f"SQL files: {len(sql_files)}")
    print("=" * 60)

    # Set up checkpointer if requested
    checkpointer = None
    if args.checkpoint:
        try:
            from langgraph.checkpoint.sqlite import SqliteSaver
            import sqlite3

            conn = sqlite3.connect(config.checkpoint_db)
            checkpointer = SqliteSaver(conn)
            print(f"Checkpointing enabled: {config.checkpoint_db}")
        except ImportError:
            print("Warning: langgraph checkpoint sqlite not available. Running without checkpointing.")

    graph = compile_pipeline(checkpointer=checkpointer)

    # Build initial state
    initial_state = MigrationState(
        sql_input_dir=config.sql_input_dir,
        config=config,
    )

    # Run the pipeline
    config_dict = {}
    if args.checkpoint:
        config_dict["configurable"] = {"thread_id": "migration-run-1"}

    final_state = graph.invoke(initial_state.model_dump(), config=config_dict if config_dict else None)

    # Summary
    print("\n" + "=" * 60)
    print("MIGRATION COMPLETE")
    print("=" * 60)
    migrated = final_state.get("migrated_procedures", {})
    failed = final_state.get("failed_procedures", {})
    output_files = final_state.get("output_files", [])
    print(f"Procedures migrated: {len(migrated)}")
    print(f"Procedures failed:   {len(failed)}")
    print(f"Files generated:     {len(output_files)}")
    if failed:
        print("\nFailed procedures:")
        for name, reason in failed.items():
            print(f"  - {name}: {reason}")
    print(f"\nOutput directory: {config.output_dir}/")


if __name__ == "__main__":
    main()
