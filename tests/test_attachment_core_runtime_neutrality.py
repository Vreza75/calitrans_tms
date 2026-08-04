"""Phase 1 correction (Codex finding): importing application.work_items.
queries didn't import streamlit, but *calling* get_attachment_summary /
get_attachment_content did (via a lazy import of
services.operations_attachment_service, which imports streamlit at module
top). These tests call the functions, not just import the modules, and
run in a fresh subprocess so an earlier test's `import streamlit`
elsewhere in the same pytest session can't mask a regression.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _run(script: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-c", script],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_calling_get_attachment_summary_does_not_import_streamlit() -> None:
    script = (
        "import sys\n"
        "from application.work_items.queries import get_attachment_summary\n"
        "get_attachment_summary(1, record={'id': 1, 'conversation_key': ''}, parsed={})\n"
        "print('streamlit-loaded' if 'streamlit' in sys.modules else 'streamlit-clean')\n"
    )
    result = _run(script)
    assert result.returncode == 0, result.stderr
    assert "streamlit-clean" in result.stdout, result.stdout


def test_calling_get_attachment_content_does_not_import_streamlit(tmp_path) -> None:
    attachment_file = tmp_path / "booking.pdf"
    attachment_file.write_bytes(b"%PDF-1.4 fake content")

    # get_attachment_content re-derives refs via get_attachment_summary,
    # which in turn needs load_operations_inbox_record - patch that in
    # the subprocess so this stays a pure runtime-neutrality check, not a
    # DB test.
    script = (
        "import sys\n"
        "import pandas as pd\n"
        "from repositories import inbox_repo\n"
        f"file_path = r'{attachment_file}'\n"
        "record = {'id': 1, 'conversation_key': '', 'parsed_data': {"
        "    '_operations_attachments': [{'filename': 'booking.pdf', 'file_path': file_path, "
        "'content_type': 'application/pdf', 'is_pdf': True}]"
        "}}\n"
        "inbox_repo.load_operations_inbox_record = lambda intake_id: pd.DataFrame([record])\n"
        "from application.work_items import queries as wiq\n"
        "ref = wiq.attachment_ref(file_path)\n"
        "content, meta = wiq.get_attachment_content(1, ref)\n"
        "assert content == b'%PDF-1.4 fake content', content\n"
        "print('streamlit-loaded' if 'streamlit' in sys.modules else 'streamlit-clean')\n"
    )
    result = _run(script)
    assert result.returncode == 0, result.stderr
    assert "streamlit-clean" in result.stdout, result.stdout


def test_calling_attachment_ref_generation_does_not_import_streamlit() -> None:
    script = (
        "import sys\n"
        "from services.operations_attachment_core import attachment_ref\n"
        "attachment_ref('storage/load_documents/some_file.pdf')\n"
        "print('streamlit-loaded' if 'streamlit' in sys.modules else 'streamlit-clean')\n"
    )
    result = _run(script)
    assert result.returncode == 0, result.stderr
    assert "streamlit-clean" in result.stdout, result.stdout


def test_operations_attachment_core_module_has_no_streamlit_import() -> None:
    source = (REPO_ROOT / "services" / "operations_attachment_core.py").read_text(encoding="utf-8")
    offending = [
        line for line in source.splitlines()
        if line.strip().startswith(("import streamlit", "from streamlit"))
    ]
    assert offending == []
