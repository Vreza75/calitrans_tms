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
    "application.inbox.models",
    "application.inbox.commands",
    "application.jobs.models",
    "application.jobs.queries",
]

REPOSITORY_MODULES = [
    "repositories.work_item_repo",
    "repositories.inbox_repo",
    "repositories.load_repo",
    "repositories.worker_job_repo",
    "repositories.domain_event_repo",
]

# Phase 9: the realtime event-delivery layer must be as framework-neutral
# as application/ and repositories/ - a client-notification concern has
# no business importing Streamlit, and services/realtime_publisher.py
# must be runnable standalone (scripts/process_realtime_events.py), same
# requirement as services/outbox_processor.py and workers/processor.py.
REALTIME_MODULES = [
    "realtime.event_types",
    "realtime.events",
    "realtime.channels",
    "realtime.publisher",
    "services.realtime_publisher",
]

# Phase 7: the worker runtime must be runnable as a standalone process
# (scripts/process_worker_jobs.py) with no Streamlit dependency, same
# framework-neutrality requirement as application/ and repositories/.
WORKER_MODULES = [
    "workers.processor",
    "workers.inbox_handlers",
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


def test_worker_runtime_does_not_import_streamlit() -> None:
    result = _run_import_check(WORKER_MODULES)
    assert result.returncode == 0, result.stderr
    assert "streamlit-clean" in result.stdout, result.stdout


def test_realtime_layer_does_not_import_streamlit() -> None:
    result = _run_import_check(REALTIME_MODULES)
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


def test_worker_module_source_never_mentions_streamlit_at_top_level() -> None:
    """Same static check as the application/ one above, applied to
    workers/ - Phase 7's runtime must stay this way as it grows new job
    handlers."""
    workers_dir = REPO_ROOT / "workers"
    offenders = []
    for path in workers_dir.rglob("*.py"):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith("import streamlit") or line.startswith("from streamlit"):
                offenders.append(str(path.relative_to(REPO_ROOT)))
    assert offenders == []


def test_realtime_module_source_never_mentions_streamlit_at_top_level() -> None:
    """Same static check as the application/ and workers/ ones above,
    applied to realtime/ - must stay this way as the event catalog
    grows."""
    realtime_dir = REPO_ROOT / "realtime"
    offenders = []
    for path in realtime_dir.rglob("*.py"):
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


def test_streamlit_and_fastapi_share_the_same_inbox_sync_command() -> None:
    """Phase 7: pages_app/operations_inbox.py's "Sync Email Engine"
    button and POST /api/v1/inbox/sync must both resolve to the exact
    same request_inbox_sync function object - not two implementations of
    "enqueue an inbox sync job"."""
    import pages_app.operations_inbox as operations_inbox_page
    from api.routers import inbox as inbox_router
    from application.inbox import commands as inbox_commands

    assert operations_inbox_page.request_inbox_sync is inbox_commands.request_inbox_sync
    assert inbox_router.request_inbox_sync is inbox_commands.request_inbox_sync
