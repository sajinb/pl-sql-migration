"""Node 6.5: VALIDATE — Structural and optional LLM review of generated Java code.

Two validation tiers:

1. Structural checks (always run, instant):
   - Package declaration present
   - @Service annotation present
   - Public class declaration present
   - Balanced braces { }
   - Expected method name present
   - Output is not empty
   - // TODO marker count (indicates unresolved edge cases from migration)

2. LLM review (opt-in via config.validate_llm_review):
   - Sends generated code back to the LLM with a focused review prompt
   - LLM identifies missing imports, wrong annotations, logic errors
   - Results stored as ValidationIssue warnings (not errors — LLM may false-positive)

A procedure passes validation if it has zero *error*-severity issues.
Warnings do not block GENERATE but are reported.
"""

from __future__ import annotations

import os
import re

from langchain_core.messages import HumanMessage, SystemMessage

from tsql_migration.state import MigratedProcedure, MigrationState, ValidationIssue, ValidationResult

_LLM_REVIEW_SYSTEM = """\
You are a Java code reviewer specializing in Spring Boot 3 / Java 21.
You will be given a generated @Service class that was migrated from a T-SQL stored procedure.
Identify concrete issues only — missing imports, wrong Spring annotations, syntax errors, \
obvious logic mistakes, or unimplemented stubs that will cause compile or runtime failures.
Do NOT comment on style, naming, or things that are subjective.
Respond with a short bullet list. If there are no issues, respond with exactly: NO_ISSUES"""

_LLM_REVIEW_USER = """\
Review the following generated Java service class for the procedure `{proc_name}`.
Flag any issues that would prevent it from compiling or running correctly.

```java
{code}
```"""


def validate_node(state: MigrationState) -> dict:
    """Validate all migrated procedure service classes."""
    config = state.config
    llm = _create_llm(config) if config.validate_llm_review else None

    results: dict[str, ValidationResult] = {}

    for proc_name, migrated in state.migrated_procedures.items():
        print(f"[VALIDATE] Checking: {migrated.service_name}")

        issues = _structural_checks(migrated)
        todo_count = migrated.service_code.count("// TODO")
        llm_review = ""

        if todo_count > 0:
            issues.append(ValidationIssue(
                severity="warning",
                code="TODO_MARKERS",
                message=f"{todo_count} // TODO marker(s) left in generated code — manual review needed",
            ))

        if llm and migrated.service_code.strip():
            llm_review = _llm_review(llm, proc_name, migrated.service_code)
            if llm_review and llm_review.strip() != "NO_ISSUES":
                issues.append(ValidationIssue(
                    severity="warning",
                    code="LLM_REVIEW",
                    message=llm_review.strip(),
                ))

        passed = not any(i.severity == "error" for i in issues)

        results[proc_name] = ValidationResult(
            procedure_name=proc_name,
            service_name=migrated.service_name,
            passed=passed,
            issues=issues,
            todo_count=todo_count,
            llm_review=llm_review,
        )

        status = "PASS" if passed else "FAIL"
        errors = sum(1 for i in issues if i.severity == "error")
        warnings = sum(1 for i in issues if i.severity == "warning")
        print(f"  {status} — {errors} error(s), {warnings} warning(s)")
        for issue in issues:
            print(f"    [{issue.severity.upper()}] {issue.code}: {issue.message}")

    total = len(results)
    passed_count = sum(1 for r in results.values() if r.passed)
    print(f"[VALIDATE] {passed_count}/{total} procedures passed validation")

    return {"validation_results": results}


# ---------------------------------------------------------------------------
# Structural checks
# ---------------------------------------------------------------------------

def _structural_checks(migrated: MigratedProcedure) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    code = migrated.service_code

    if not code or len(code.strip()) < 50:
        issues.append(ValidationIssue(
            severity="error",
            code="EMPTY_OUTPUT",
            message="Generated code is empty or too short to be a valid Java class",
        ))
        return issues  # no point running further checks on empty code

    if not re.search(r"^\s*package\s+[\w.]+;", code, re.MULTILINE):
        issues.append(ValidationIssue(
            severity="error",
            code="MISSING_PACKAGE",
            message="No package declaration found",
        ))

    if "@Service" not in code:
        issues.append(ValidationIssue(
            severity="error",
            code="MISSING_SERVICE_ANNOTATION",
            message="@Service annotation not found — Spring will not manage this bean",
        ))

    if not re.search(r"public\s+class\s+\w+", code):
        issues.append(ValidationIssue(
            severity="error",
            code="MISSING_CLASS_DECLARATION",
            message="No public class declaration found",
        ))

    open_braces = code.count("{")
    close_braces = code.count("}")
    if open_braces != close_braces:
        issues.append(ValidationIssue(
            severity="error",
            code="UNBALANCED_BRACES",
            message=f"Unbalanced braces: {open_braces} '{{' vs {close_braces} '}}'",
        ))

    if migrated.method_name and not re.search(
        rf"\b{re.escape(migrated.method_name)}\s*\(", code
    ):
        issues.append(ValidationIssue(
            severity="warning",
            code="MISSING_METHOD",
            message=f"Expected method '{migrated.method_name}' not found in generated code",
        ))

    return issues


# ---------------------------------------------------------------------------
# LLM review
# ---------------------------------------------------------------------------

def _llm_review(llm, proc_name: str, code: str) -> str:
    try:
        response = llm.invoke([
            SystemMessage(content=_LLM_REVIEW_SYSTEM),
            HumanMessage(content=_LLM_REVIEW_USER.format(proc_name=proc_name, code=code)),
        ])
        return response.content
    except Exception as e:
        return f"LLM review failed: {e}"


def _create_llm(config):
    if config.llm_provider == "openai":
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(
            model=config.llm_model,
            api_key=os.environ.get(config.llm_api_key_env, ""),
            temperature=0,
        )
    else:
        from langchain_anthropic import ChatAnthropic
        return ChatAnthropic(
            model=config.llm_model,
            api_key=os.environ.get(config.llm_api_key_env, ""),
            temperature=0,
        )
