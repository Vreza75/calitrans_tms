"""Verifies a live PostgreSQL database has every table, column, index, and
trigger that the committed database/*.sql migrations define.

Expectations are parsed directly out of the migration files (not hand
duplicated here) so this script can't silently drift from the migrations
it's checking - the .sql files stay the single source of truth.

Usage:
    python scripts/verify_schema.py                        # uses configured DATABASE_URL
    python scripts/verify_schema.py --database-url "postgresql://..."

Exit code 0 if everything expected is present, 1 otherwise. Never prints
the database URL's credentials - only scheme/host/db name.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from sqlalchemy import create_engine, text

REPO_ROOT = Path(__file__).resolve().parent.parent
DATABASE_DIR = REPO_ROOT / "database"

sys.path.insert(0, str(REPO_ROOT))
from scripts.run_migrations import MIGRATION_ORDER, _mask, _resolve_database_url  # noqa: E402

_COLUMN_LINE_SKIP_WORDS = {
    "primary",
    "unique",
    "check",
    "constraint",
    "foreign",
}

_CREATE_TABLE_RE = re.compile(
    r"create table if not exists\s+(\w+)\s*\((.*?)\)\s*;", re.IGNORECASE | re.DOTALL
)
_ALTER_ADD_COLUMN_RE = re.compile(
    r"alter table\s+(\w+)\s+add column if not exists\s+(\w+)", re.IGNORECASE
)
_CREATE_INDEX_RE = re.compile(
    r"create (?:unique )?index if not exists\s+(\w+)\s+on\s+(\w+)", re.IGNORECASE
)
_CREATE_TRIGGER_RE = re.compile(r"create trigger\s+(\w+)", re.IGNORECASE)


def _split_top_level_commas(body: str) -> list[str]:
    """Splits a create-table body on commas that aren't nested inside
    parentheses, so `rate numeric(12,2)` and `unique(a, b)` each stay one
    segment instead of being torn apart at their inner comma."""
    segments = []
    depth = 0
    current: list[str] = []
    for char in body:
        if char == "(":
            depth += 1
            current.append(char)
        elif char == ")":
            depth -= 1
            current.append(char)
        elif char == "," and depth == 0:
            segments.append("".join(current))
            current = []
        else:
            current.append(char)
    if current:
        segments.append("".join(current))
    return segments


def _parse_expected_schema() -> dict:
    tables: dict[str, set[str]] = {}
    indexes: dict[str, str] = {}  # index name -> table
    triggers: set[str] = set()

    for filename in MIGRATION_ORDER:
        sql = (DATABASE_DIR / filename).read_text(encoding="utf-8")

        for table_name, body in _CREATE_TABLE_RE.findall(sql):
            columns = tables.setdefault(table_name.lower(), set())
            for raw_line in _split_top_level_commas(body):
                line = raw_line.strip()
                if not line:
                    continue
                leading_token = re.match(r'^"?([a-zA-Z_][a-zA-Z0-9_]*)', line)
                if not leading_token:
                    continue
                first_word = leading_token.group(1).lower()
                if first_word in _COLUMN_LINE_SKIP_WORDS:
                    continue
                columns.add(first_word)

        for table_name, column_name in _ALTER_ADD_COLUMN_RE.findall(sql):
            tables.setdefault(table_name.lower(), set()).add(column_name.lower())

        for index_name, table_name in _CREATE_INDEX_RE.findall(sql):
            indexes[index_name.lower()] = table_name.lower()

        for trigger_name in _CREATE_TRIGGER_RE.findall(sql):
            triggers.add(trigger_name.lower())

    return {"tables": tables, "indexes": indexes, "triggers": triggers}


def _live_columns(engine) -> dict[str, set[str]]:
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "select table_name, column_name from information_schema.columns "
                "where table_schema = 'public'"
            )
        ).fetchall()
    live: dict[str, set[str]] = {}
    for table_name, column_name in rows:
        live.setdefault(table_name.lower(), set()).add(column_name.lower())
    return live


def _live_indexes(engine) -> set[str]:
    with engine.connect() as conn:
        rows = conn.execute(
            text("select indexname from pg_indexes where schemaname = 'public'")
        ).fetchall()
    return {row[0].lower() for row in rows}


def _live_triggers(engine) -> set[str]:
    with engine.connect() as conn:
        rows = conn.execute(
            text("select trigger_name from information_schema.triggers where trigger_schema = 'public'")
        ).fetchall()
    return {row[0].lower() for row in rows}


def verify(database_url: str) -> int:
    print(f"Target database: {_mask(database_url)}")

    expected = _parse_expected_schema()
    engine = create_engine(database_url, future=True)

    live_columns = _live_columns(engine)
    live_indexes = _live_indexes(engine)
    live_triggers = _live_triggers(engine)

    ok = True

    for table_name in sorted(expected["tables"]):
        expected_columns = expected["tables"][table_name]
        if table_name not in live_columns:
            print(f"FAIL  table missing: {table_name}")
            ok = False
            continue
        missing_columns = sorted(expected_columns - live_columns[table_name])
        if missing_columns:
            print(f"FAIL  {table_name}: missing columns {missing_columns}")
            ok = False
        else:
            print(f"OK    {table_name}: {len(expected_columns)} columns present")

    for index_name in sorted(expected["indexes"]):
        if index_name not in live_indexes:
            print(f"FAIL  index missing: {index_name} (on {expected['indexes'][index_name]})")
            ok = False

    for trigger_name in sorted(expected["triggers"]):
        if trigger_name not in live_triggers:
            print(f"FAIL  trigger missing: {trigger_name}")
            ok = False

    index_count = len(expected["indexes"])
    trigger_count = len(expected["triggers"])
    print(
        f"Checked {len(expected['tables'])} tables, {index_count} indexes, "
        f"{trigger_count} triggers."
    )
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--database-url", default=None, help="Override DATABASE_URL for this run only.")
    args = parser.parse_args()

    database_url = args.database_url or _resolve_database_url(None)
    return verify(database_url)


if __name__ == "__main__":
    raise SystemExit(main())
