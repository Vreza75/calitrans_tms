"""Smoke coverage for the root-module interface contract: every service,
repository, and page imports execute/read_df/DispatchDatabaseClient/column_exists
from db_client, and get_secret/get_config_source from config, by name. If any
of these exports goes missing or a root module fails to import, every one of
those call sites breaks at once. These tests check the contract by
introspection only - no database connection, no Streamlit runtime.
"""
import db_client
import config
import validators
import database.db_client as database_db_client


def test_root_modules_import_cleanly():
    import db_client as _db_client  # noqa: F401
    import config as _config  # noqa: F401
    import validators as _validators  # noqa: F401


def test_app_router_imports_cleanly():
    import pages_app.router as router

    assert hasattr(router, "route_selected_page")


def test_db_client_exposes_the_interface_every_caller_expects():
    for name in ("execute", "read_df", "DispatchDatabaseClient", "column_exists", "get_engine"):
        assert hasattr(db_client, name), f"db_client is missing expected export: {name}"

    assert callable(db_client.execute)
    assert callable(db_client.read_df)
    assert callable(db_client.column_exists)
    assert callable(db_client.get_engine)


def test_database_db_client_compat_shim_re_exports_the_same_objects():
    # database/db_client.py is a documented wrapper; it must stay in sync with
    # db_client.py rather than defining a second, divergent implementation.
    assert database_db_client.execute is db_client.execute
    assert database_db_client.read_df is db_client.read_df
    assert database_db_client.DispatchDatabaseClient is db_client.DispatchDatabaseClient


def test_config_exposes_the_interface_every_caller_expects():
    for name in ("get_secret", "get_config_source", "DOCUMENT_STORAGE_DIR", "EDITABLE_COLUMNS"):
        assert hasattr(config, name), f"config is missing expected export: {name}"

    assert callable(config.get_secret)
    assert callable(config.get_config_source)


def test_validators_exposes_the_interface_every_caller_expects():
    assert hasattr(validators, "validate_dispatch_rows")
    assert callable(validators.validate_dispatch_rows)
