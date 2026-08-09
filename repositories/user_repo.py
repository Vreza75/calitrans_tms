# repositories/user_repo.py

from __future__ import annotations

from typing import Any

import pandas as pd
from sqlalchemy import text as sa_text

from db_client import execute, read_df, require_schema_ready, transaction


def _ensure_app_users_ready() -> None:
    require_schema_ready("app_users", "password_hash", migration_hint="database/app_users_migration.sql")


def get_user_by_email(email: str) -> dict[str, Any] | None:
    _ensure_app_users_ready()
    df = read_df(
        "select id, email, display_name, password_hash, role, is_active "
        "from app_users where lower(email) = lower(:email)",
        {"email": email},
    )
    return df.iloc[0].to_dict() if not df.empty else None


def get_user_by_id(user_id: int) -> dict[str, Any] | None:
    _ensure_app_users_ready()
    df = read_df(
        "select id, email, display_name, password_hash, role, is_active "
        "from app_users where id = :id",
        {"id": user_id},
    )
    return df.iloc[0].to_dict() if not df.empty else None


def list_users() -> pd.DataFrame:
    _ensure_app_users_ready()
    return read_df(
        "select id, email, display_name, role, is_active, created_at "
        "from app_users order by display_name"
    )


def create_user(*, email: str, display_name: str, password_hash: str, role: str) -> int:
    """Uses transaction() (engine.begin(), commits on clean exit) rather
    than the module-level execute() helper, which discards its result -
    RETURNING id needs the row back before the transaction closes."""
    _ensure_app_users_ready()
    with transaction() as conn:
        row = conn.execute(
            sa_text(
                "insert into app_users (email, display_name, password_hash, role) "
                "values (:email, :display_name, :password_hash, :role) returning id"
            ),
            {"email": email, "display_name": display_name, "password_hash": password_hash, "role": role},
        ).first()
    return int(row[0])


def set_user_active(user_id: int, is_active: bool) -> None:
    _ensure_app_users_ready()
    execute(
        "update app_users set is_active = :is_active, updated_at = now() where id = :id",
        {"id": user_id, "is_active": is_active},
    )


def update_password_hash(user_id: int, password_hash: str) -> None:
    _ensure_app_users_ready()
    execute(
        "update app_users set password_hash = :password_hash, updated_at = now() where id = :id",
        {"id": user_id, "password_hash": password_hash},
    )
