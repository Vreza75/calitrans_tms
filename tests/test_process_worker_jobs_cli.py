"""Phase 7: scripts/process_worker_jobs.py's CLI surface. Argument
parsing and the confirmation-gate logic are testable without a database;
the actual list/inspect/retry behavior is covered by
tests/test_worker_job_repo.py's PostgreSQL-gated tests (repositories/
worker_job_repo.py owns that logic, this script is a thin CLI wrapper).
"""
from __future__ import annotations

import argparse

import pytest

from scripts.process_worker_jobs import main


def test_process_subcommand_parses_defaults(monkeypatch):
    called = {}

    def _fake_cmd_process(args):
        called["args"] = args
        return 0

    monkeypatch.setattr("sys.argv", ["process_worker_jobs.py", "process"])
    monkeypatch.setattr("scripts.process_worker_jobs._cmd_process", _fake_cmd_process)
    exit_code = main()
    assert exit_code == 0
    assert called["args"].max_jobs == 50
    assert called["args"].reclaim_stuck_minutes is None


def test_process_subcommand_accepts_overrides(monkeypatch):
    called = {}

    def _fake_cmd_process(args):
        called["args"] = args
        return 0

    monkeypatch.setattr(
        "sys.argv", ["process_worker_jobs.py", "process", "--max-jobs", "10", "--reclaim-stuck-minutes", "5"]
    )
    monkeypatch.setattr("scripts.process_worker_jobs._cmd_process", _fake_cmd_process)
    main()
    assert called["args"].max_jobs == 10
    assert called["args"].reclaim_stuck_minutes == 5


def test_retry_requires_job_id(monkeypatch):
    monkeypatch.setattr("sys.argv", ["process_worker_jobs.py", "retry"])
    with pytest.raises(SystemExit):
        main()


def test_retry_all_without_yes_is_a_no_op_and_touches_no_db(monkeypatch):
    """The confirmation gate must short-circuit before any DB import/call -
    proven by never patching db_client/worker_job_repo here at all; if the
    function tried to reach the database it would raise (no DATABASE_URL
    configured in this test environment), not just return 1."""
    monkeypatch.setattr("sys.argv", ["process_worker_jobs.py", "retry-all"])
    exit_code = main()
    assert exit_code == 1


def test_retry_all_with_yes_proceeds_to_the_repo_call(monkeypatch):
    called = {}
    monkeypatch.setattr("sys.argv", ["process_worker_jobs.py", "retry-all", "--yes"])

    class _FakeConn:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def _fake_transaction():
        return _FakeConn()

    def _fake_retry_all_failed(conn, *, reset_attempts=False):
        called["reset_attempts"] = reset_attempts
        return 3

    monkeypatch.setattr("db_client.transaction", _fake_transaction)
    monkeypatch.setattr("repositories.worker_job_repo.retry_all_failed", _fake_retry_all_failed)

    exit_code = main()
    assert exit_code == 0
    assert called["reset_attempts"] is False


def test_all_subcommands_are_registered():
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command")
    # Smoke check against the real main() parser via --help exit codes
    # instead of reaching into argparse internals.
    for command in ("process", "list-pending", "list-failed", "inspect", "retry", "retry-all"):
        import subprocess
        import sys as _sys
        from pathlib import Path

        script = Path(__file__).resolve().parent.parent / "scripts" / "process_worker_jobs.py"
        result = subprocess.run(
            [_sys.executable, str(script), command, "--help"], capture_output=True, text=True
        )
        assert result.returncode == 0, f"{command} --help failed: {result.stderr}"
