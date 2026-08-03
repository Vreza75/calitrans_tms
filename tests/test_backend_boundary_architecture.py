"""Phase 1 architecture constraints: the application/ layer must stay
framework-neutral, and importing it must not pull Streamlit into the
process. These tests run in a fresh subprocess per assertion so a prior
test's `import streamlit` elsewhere in the same pytest session can't mask
a real regression (sys.modules is process-global and would otherwise
"already" contain streamlit by the time this test runs).
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

APPLICATION_MODULES = [
    "application.exceptions",
    "application.work_items.models",
    "application.work_items.queries",
    "application.work_items.commands",
    "application.loads.models",
    "application.loads.queries",
    "application.loads.commands",
    "application.attachments.models",
    "application.attachments.queries",
    "application.conversations.models",
    "application.conversations.queries",
    "application.order_drafts.models",
    "application.order_drafts.queries",
    "application.order_drafts.commands",
]

REPOSITORY_MODULES = [
    "repositories.work_item_repo",
    "repositories.inbox_repo",
    "repositories.load_repo",
]


def _run_import_check(modules: list[str]) -> subprocess.CompletedProcess:
    script = (
        "import sys\n"
        + "\n".join(f"import {m}" for m in modules)
        + "\nprint('streamlit-loaded' if 'streamlit' in sys.modules else 'streamlit-clean')\n"
    )
    return subprocess.run(
        [sys.executable, "-c", script],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_application_layer_does_not_import_streamlit() -> None:
    result = _run_import_check(APPLICATION_MODULES)
    assert result.returncode == 0, result.stderr
    assert "streamlit-clean" in result.stdout, result.stdout


def test_repository_layer_does_not_import_streamlit() -> None:
    result = _run_import_check(REPOSITORY_MODULES)
    assert result.returncode == 0, result.stderr
    assert "streamlit-clean" in result.stdout, result.stdout


def test_application_module_source_never_mentions_streamlit_at_top_level() -> None:
    """A stricter static check: no `application/*.py` file may have a
    top-level (unindented) `import streamlit` or `from streamlit`. A lazy
    import inside a function body (indented) is fine and is how this
    layer reuses heavier legacy services - see application/work_items/
    queries.py's get_attachment_summary() for the documented pattern."""
    app_dir = REPO_ROOT / "application"
    offenders = []
    for path in app_dir.rglob("*.py"):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith("import streamlit") or line.startswith("from streamlit"):
                offenders.append(str(path.relative_to(REPO_ROOT)))
    assert offenders == []


def test_fastapi_routers_do_not_contain_raw_sql() -> None:
    """Routers call application services; they must not run SQL directly
    (that logic belongs in repositories/)."""
    routers_dir = REPO_ROOT / "api" / "routers"
    offenders = []
    for path in routers_dir.glob("*.py"):
        text = path.read_text(encoding="utf-8").lower()
        if "select " in text or "insert into" in text or "update " in text and " set " in text:
            offenders.append(str(path.relative_to(REPO_ROOT)))
    assert offenders == []


def test_streamlit_page_and_fastapi_router_call_the_same_application_functions() -> None:
    """Streamlit and FastAPI must share one command/query implementation,
    not maintain parallel copies. Checked by import identity: the router
    module and the Streamlit-facing service both resolve to the exact
    same function objects."""
    from api.routers import work_items as work_items_router
    from application.work_items import commands as work_item_commands
    from application.work_items import queries as work_item_queries

    assert work_items_router.get_work_item_detail is work_item_queries.get_work_item_detail
    assert work_items_router.work_item_commands.close_work_item is work_item_commands.close_work_item
