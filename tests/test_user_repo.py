from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy import event, text

import db_client
import repositories.user_repo as user_repo


@pytest.fixture
def sqlite_users_db(monkeypatch, tmp_path):
    """Same technique as tests/test_operations_case_transaction_boundary.py -
    real SQLite proves real commit/read behavior without needing Postgres."""
    db_path = tmp_path / "users_test.db"
    url = f"sqlite:///{db_path}"

    monkeypatch.setattr(db_client, "get_secret", lambda name, default=None: url if name == "DATABASE_URL" else default)
    db_client._ENGINE_CACHE.pop(url, None)

    engine = db_client.get_engine(url)

    @event.listens_for(engine, "connect")
    def _register_now(dbapi_conn, connection_record):
        dbapi_conn.create_function("now", 0, lambda: dt.datetime.now(dt.timezone.utc).isoformat())

    with engine.begin() as conn:
        conn.execute(
            text(
                "create table app_users ("
                "id integer primary key autoincrement, email text, display_name text, "
                "password_hash text, role text, is_active boolean default 1, "
                "created_at text, updated_at text)"
            )
        )

    yield url

    db_client._ENGINE_CACHE.pop(url, None)


def test_create_user_returns_new_id(sqlite_users_db) -> None:
    user_id = user_repo.create_user(
        email="dispatcher@calitranscorp.com",
        display_name="Dee Dispatcher",
        password_hash="hashed-value",
        role="dispatcher",
    )
    assert isinstance(user_id, int)
    assert user_id > 0


def test_get_user_by_email_is_case_insensitive(sqlite_users_db) -> None:
    user_repo.create_user(
        email="Dispatcher@CaliTransCorp.com",
        display_name="Dee Dispatcher",
        password_hash="hashed-value",
        role="dispatcher",
    )
    found = user_repo.get_user_by_email("dispatcher@calitranscorp.com")
    assert found is not None
    assert found["display_name"] == "Dee Dispatcher"


def test_get_user_by_email_returns_none_for_unknown_email(sqlite_users_db) -> None:
    assert user_repo.get_user_by_email("nobody@calitranscorp.com") is None


def test_set_user_active_toggles_flag(sqlite_users_db) -> None:
    user_id = user_repo.create_user(
        email="acc@calitranscorp.com", display_name="Acc Ounting", password_hash="h", role="accounting"
    )
    user_repo.set_user_active(user_id, False)
    user = user_repo.get_user_by_id(user_id)
    assert bool(user["is_active"]) is False

    user_repo.set_user_active(user_id, True)
    user = user_repo.get_user_by_id(user_id)
    assert bool(user["is_active"]) is True


def test_update_password_hash_replaces_stored_hash(sqlite_users_db) -> None:
    user_id = user_repo.create_user(
        email="mgr@calitranscorp.com", display_name="Mgr Ager", password_hash="old-hash", role="manager"
    )
    user_repo.update_password_hash(user_id, "new-hash")
    user = user_repo.get_user_by_id(user_id)
    assert user["password_hash"] == "new-hash"


def test_list_users_returns_all_created_users(sqlite_users_db) -> None:
    user_repo.create_user(email="a@calitranscorp.com", display_name="A", password_hash="h", role="dispatcher")
    user_repo.create_user(email="b@calitranscorp.com", display_name="B", password_hash="h", role="manager")

    df = user_repo.list_users()
    assert set(df["email"]) == {"a@calitranscorp.com", "b@calitranscorp.com"}


def test_missing_schema_raises_schema_not_ready_not_silent_create(monkeypatch, tmp_path) -> None:
    """No runtime DDL: querying against a database that never had
    app_users_migration.sql applied must fail loudly, not self-heal."""
    db_path = tmp_path / "no_users_table.db"
    url = f"sqlite:///{db_path}"
    monkeypatch.setattr(db_client, "get_secret", lambda name, default=None: url if name == "DATABASE_URL" else default)
    db_client._ENGINE_CACHE.pop(url, None)

    engine = db_client.get_engine(url)
    with engine.begin() as conn:
        conn.execute(text("create table unrelated (id integer primary key)"))

    with pytest.raises(db_client.SchemaNotReadyError):
        user_repo.get_user_by_email("anyone@calitranscorp.com")

    db_client._ENGINE_CACHE.pop(url, None)
