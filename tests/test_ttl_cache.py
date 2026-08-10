from __future__ import annotations

import time

import pandas as pd

from utils.ttl_cache import ttl_cache


def test_caches_result_within_ttl_window() -> None:
    calls = []

    @ttl_cache(ttl_seconds=60)
    def load():
        calls.append(1)
        return "value"

    assert load() == "value"
    assert load() == "value"
    assert len(calls) == 1


def test_expires_after_ttl_elapses() -> None:
    calls = []

    @ttl_cache(ttl_seconds=0.05)
    def load():
        calls.append(1)
        return "value"

    load()
    time.sleep(0.1)
    load()

    assert len(calls) == 2


def test_different_arguments_are_cached_independently() -> None:
    calls = []

    @ttl_cache(ttl_seconds=60)
    def load(x):
        calls.append(x)
        return x * 2

    assert load(1) == 2
    assert load(2) == 4
    assert load(1) == 2  # still cached
    assert calls == [1, 2]


def test_clear_forces_recomputation() -> None:
    calls = []

    @ttl_cache(ttl_seconds=60)
    def load():
        calls.append(1)
        return "value"

    load()
    load.clear()
    load()

    assert len(calls) == 2


def test_returned_dataframe_is_a_copy_not_the_cached_reference() -> None:
    """Same safety guarantee st.cache_data gave: a caller mutating its
    result in place must not corrupt what the next call within the TTL
    window (or a concurrent caller) sees."""

    @ttl_cache(ttl_seconds=60)
    def load():
        return pd.DataFrame({"a": [1, 2, 3]})

    first = load()
    first["a"] = [99, 99, 99]

    second = load()

    assert list(second["a"]) == [1, 2, 3]


def test_returned_dict_is_a_copy_not_the_cached_reference() -> None:
    @ttl_cache(ttl_seconds=60)
    def load():
        return {"count": 1}

    first = load()
    first["count"] = 999

    second = load()

    assert second["count"] == 1
