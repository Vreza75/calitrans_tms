from __future__ import annotations

import pytest
from sqlalchemy import text

import db_client


@pytest.fixture
def sqlite_transaction(monkeypatch, tmp_path):
    """Point db_client.transaction()/get_engine() at a throwaway sqlite
    file so commit/rollback semantics can be proven without a Postgres
    connection - this test never touches DATABASE_URL or any configured
    secret."""
    db_path = tmp_path / "transaction_test.db"
    url = f"sqlite:///{db_path}"

    monkeypatch.setattr(db_client, "get_secret", lambda name, default=None: url if name == "DATABASE_URL" else default)
    db_client._ENGINE_CACHE.pop(url, None)

    with db_client.get_engine(url).begin() as conn:
        conn.execute(text("create table widgets (id integer primary key, name text)"))

    yield url

    db_client._ENGINE_CACHE.pop(url, None)


def test_transaction_commits_all_statements_together(sqlite_transaction):
    with db_client.transaction() as conn:
        conn.execute(text("insert into widgets (id, name) values (1, 'a')"))
        conn.execute(text("insert into widgets (id, name) values (2, 'b')"))

    rows = db_client.read_df("select id, name from widgets order by id")
    assert list(rows["id"]) == [1, 2]


def test_transaction_rolls_back_all_statements_on_failure(sqlite_transaction):
    with pytest.raises(RuntimeError):
        with db_client.transaction() as conn:
            conn.execute(text("insert into widgets (id, name) values (10, 'first')"))
            raise RuntimeError("forced failure mid-command")

    rows = db_client.read_df("select id, name from widgets where id = 10")
    assert rows.empty
