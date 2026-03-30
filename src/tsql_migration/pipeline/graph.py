"""LangGraph pipeline definition — the main DAG connecting all 7 nodes."""

from __future__ import annotations

from langgraph.graph import StateGraph, END

from tsql_migration.state import MigrationState
from tsql_migration.pipeline.nodes.parse_node import parse_node
from tsql_migration.pipeline.nodes.analyze_node import analyze_node
from tsql_migration.pipeline.nodes.plan_node import plan_node
from tsql_migration.pipeline.nodes.extract_schema_node import extract_schema_node
from tsql_migration.pipeline.nodes.prepare_node import prepare_node
from tsql_migration.pipeline.nodes.migrate_node import migrate_node
from tsql_migration.pipeline.nodes.generate_node import generate_node


def build_pipeline() -> StateGraph:
    """Build the LangGraph migration pipeline.

    Flow:
        PARSE → ANALYZE → PLAN → EXTRACT_SCHEMA → PREPARE → MIGRATE → GENERATE
    """
    workflow = StateGraph(MigrationState)

    # Add nodes
    workflow.add_node("parse", parse_node)
    workflow.add_node("analyze", analyze_node)
    workflow.add_node("plan", plan_node)
    workflow.add_node("extract_schema", extract_schema_node)
    workflow.add_node("prepare", prepare_node)
    workflow.add_node("migrate", migrate_node)
    workflow.add_node("generate", generate_node)

    # Define edges — linear pipeline
    workflow.set_entry_point("parse")
    workflow.add_edge("parse", "analyze")
    workflow.add_edge("analyze", "plan")
    workflow.add_edge("plan", "extract_schema")
    workflow.add_edge("extract_schema", "prepare")
    workflow.add_edge("prepare", "migrate")
    workflow.add_edge("migrate", "generate")
    workflow.add_edge("generate", END)

    return workflow


def compile_pipeline(checkpointer=None):
    """Compile the pipeline into a runnable graph.

    Args:
        checkpointer: Optional LangGraph checkpointer for state persistence.
                      Use SqliteSaver for dev, PostgresSaver for prod.
    """
    workflow = build_pipeline()

    if checkpointer:
        return workflow.compile(checkpointer=checkpointer)
    return workflow.compile()
