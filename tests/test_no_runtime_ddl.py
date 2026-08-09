"""Architecture regression guard: normal application/service/API execution
must not issue schema DDL (CREATE TABLE, ALTER TABLE, CREATE INDEX, DROP
TABLE, DROP INDEX). DDL belongs in database/*.sql migrations, scripts/
tooling, or test-only disposable schema setup - never in a runtime code
path that a request/page/service call can reach.

This is a targeted token scanner, not a SQL parser - it does not need to
understand SQL, only to catch the obvious case of a DDL keyword appearing
in a runtime module's source.

No allowlist: every runtime self-heal DDL call previously found here
(services/operations_inbox_service.py, services/operations_case_service.py,
services/dispatch_data_service.py, repositories/task_repo.py,
repositories/ai_usage_repo.py, ai_core/usage_logger.py,
ai_core/company_memory.py, pages_app/port_houston_integration.py) has been
converted to a read-only db_client.require_schema_ready() check that
raises db_client.SchemaNotReadyError instead of running DDL - see
docs/DATABASE_MIGRATIONS.md. If this test ever fails, that is a real
regression: schema DDL was added to a runtime code path.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Runtime code directories/files that must never issue DDL. Deliberately
# excludes database/ (migrations + a thin compat shim already verified
# DDL-free in its .py files), scripts/ (migration tooling), and tests/.
_SCAN_TARGETS = [
    REPO_ROOT / "services",
    REPO_ROOT / "repositories",
    REPO_ROOT / "application",
    REPO_ROOT / "api",
    REPO_ROOT / "pages_app",
    REPO_ROOT / "ui_components",
    REPO_ROOT / "ai_core",
    REPO_ROOT / "ai_agents",
    REPO_ROOT / "db_client.py",
    REPO_ROOT / "app.py",
]

_FORBIDDEN_DDL_PATTERN = re.compile(
    r"\b(create\s+table|create\s+(?:unique\s+)?index|alter\s+table|drop\s+table|drop\s+index)\b",
    re.IGNORECASE,
)


def _iter_python_files():
    for target in _SCAN_TARGETS:
        if target.is_file():
            yield target
        elif target.is_dir():
            yield from target.rglob("*.py")


def _find_ddl_violations() -> list[tuple[str, int, str]]:
    violations = []
    for path in _iter_python_files():
        rel_path = path.relative_to(REPO_ROOT).as_posix()
        text = path.read_text(encoding="utf-8", errors="ignore")
        for line_number, line in enumerate(text.splitlines(), start=1):
            if _FORBIDDEN_DDL_PATTERN.search(line):
                violations.append((rel_path, line_number, line.strip()))

    return violations


def test_no_runtime_ddl_anywhere_in_application_code():
    violations = _find_ddl_violations()
    assert not violations, (
        "Runtime DDL found - schema changes belong in database/*.sql "
        "migrations, never in application/service/API code. If a table/"
        "column genuinely needs to exist, add or extend a migration in "
        "database/*.sql, register it in scripts/run_migrations.py's "
        "MIGRATION_ORDER, and guard the runtime code with "
        "db_client.require_schema_ready() instead. Violations:\n"
        + "\n".join(f"  {path}:{line_no}: {line}" for path, line_no, line in violations)
    )
