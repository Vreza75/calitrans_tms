# utils/ttl_cache.py

from __future__ import annotations

"""Framework-neutral replacement for st.cache_data(ttl=...) - process-wide,
per-argument memoization with expiry, no streamlit dependency. Same
consumer-facing contract st.cache_data already had:

  - keyed on (args, kwargs) - callers need hashable arguments, same
    requirement st.cache_data already imposed.
  - returns a deep copy on every read (not the same object the first
    caller got) - st.cache_data does this too, specifically so one caller
    mutating its result in place can't corrupt what every other caller
    (and the next call within the TTL window) sees.
  - exposes .clear() on the wrapped function for targeted invalidation,
    matching the interface existing call sites already use (see
    services/operations_inbox_service.py's refresh_data()).
"""

import copy
import threading
import time
from functools import wraps
from typing import Any, Callable, TypeVar

F = TypeVar("F", bound=Callable[..., Any])


def ttl_cache(ttl_seconds: float) -> Callable[[F], F]:
    def decorator(func: F) -> F:
        lock = threading.Lock()
        store: dict[Any, tuple[float, Any]] = {}

        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            key = (args, tuple(sorted(kwargs.items())))
            now = time.monotonic()
            with lock:
                cached = store.get(key)
                if cached is not None and now - cached[0] < ttl_seconds:
                    return copy.deepcopy(cached[1])

            result = func(*args, **kwargs)
            with lock:
                store[key] = (now, result)
            return copy.deepcopy(result)

        def clear() -> None:
            with lock:
                store.clear()

        wrapper.clear = clear  # type: ignore[attr-defined]
        return wrapper  # type: ignore[return-value]

    return decorator
