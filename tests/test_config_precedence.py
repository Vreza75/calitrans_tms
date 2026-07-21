"""config.get_secret() layers four sources (Streamlit secrets, environment,
local .env file, local .streamlit/secrets.toml) so the same code works in
Streamlit Cloud and on a dispatcher's laptop. These tests pin down the
precedence order and the missing-value behavior using monkeypatch only -
they must never depend on this machine's real .env or secrets.toml, since
those hold live production-adjacent credentials.
"""
import config


def _isolate_all_sources(monkeypatch):
    """Force every source to 'not set' so a test can turn one back on at a time."""
    monkeypatch.setattr(config, "get_streamlit_secret", lambda name: None)
    monkeypatch.setattr(config, "_read_local_env_secret", lambda name: None)
    monkeypatch.setattr(config, "_read_local_streamlit_secret", lambda name: None)
    monkeypatch.delenv("TEST_PRECEDENCE_VALUE", raising=False)


def test_streamlit_secret_wins_over_everything_else(monkeypatch):
    _isolate_all_sources(monkeypatch)
    monkeypatch.setattr(config, "get_streamlit_secret", lambda name: "from-streamlit")
    monkeypatch.setenv("TEST_PRECEDENCE_VALUE", "from-env")
    monkeypatch.setattr(config, "_read_local_env_secret", lambda name: "from-local-env-file")
    monkeypatch.setattr(config, "_read_local_streamlit_secret", lambda name: "from-local-secrets-toml")

    assert config.get_secret("TEST_PRECEDENCE_VALUE") == "from-streamlit"
    assert config.get_config_source("TEST_PRECEDENCE_VALUE") == "streamlit secrets"


def test_env_var_wins_when_streamlit_secret_is_absent(monkeypatch):
    _isolate_all_sources(monkeypatch)
    monkeypatch.setenv("TEST_PRECEDENCE_VALUE", "from-env")
    monkeypatch.setattr(config, "_read_local_env_secret", lambda name: "from-local-env-file")
    monkeypatch.setattr(config, "_read_local_streamlit_secret", lambda name: "from-local-secrets-toml")

    assert config.get_secret("TEST_PRECEDENCE_VALUE") == "from-env"
    assert config.get_config_source("TEST_PRECEDENCE_VALUE") == "environment/.env"


def test_local_env_file_wins_when_streamlit_and_os_env_are_absent(monkeypatch):
    _isolate_all_sources(monkeypatch)
    monkeypatch.setattr(config, "_read_local_env_secret", lambda name: "from-local-env-file")
    monkeypatch.setattr(config, "_read_local_streamlit_secret", lambda name: "from-local-secrets-toml")

    assert config.get_secret("TEST_PRECEDENCE_VALUE") == "from-local-env-file"
    assert config.get_config_source("TEST_PRECEDENCE_VALUE") == "local .env file"


def test_local_secrets_toml_is_the_last_fallback(monkeypatch):
    _isolate_all_sources(monkeypatch)
    monkeypatch.setattr(config, "_read_local_streamlit_secret", lambda name: "from-local-secrets-toml")

    assert config.get_secret("TEST_PRECEDENCE_VALUE") == "from-local-secrets-toml"
    assert config.get_config_source("TEST_PRECEDENCE_VALUE") == "local .streamlit/secrets.toml"


def test_missing_optional_configuration_returns_the_supplied_default(monkeypatch):
    _isolate_all_sources(monkeypatch)

    assert config.get_secret("TEST_PRECEDENCE_VALUE", "fallback-default") == "fallback-default"
    assert config.get_config_source("TEST_PRECEDENCE_VALUE") == "missing"


def test_missing_optional_configuration_with_no_default_is_none_not_an_error(monkeypatch):
    _isolate_all_sources(monkeypatch)

    assert config.get_secret("TEST_PRECEDENCE_VALUE") is None


def test_get_int_secret_falls_back_to_default_on_non_numeric_value(monkeypatch):
    _isolate_all_sources(monkeypatch)
    monkeypatch.setenv("TEST_PRECEDENCE_VALUE", "not-a-number")

    assert config.get_int_secret("TEST_PRECEDENCE_VALUE", 993) == 993


def test_get_int_secret_parses_a_configured_value(monkeypatch):
    _isolate_all_sources(monkeypatch)
    monkeypatch.setenv("TEST_PRECEDENCE_VALUE", "587")

    assert config.get_int_secret("TEST_PRECEDENCE_VALUE", 465) == 587
