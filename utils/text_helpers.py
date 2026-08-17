from __future__ import annotations

import json
from typing import Any

_NULL_LIKE_TOKENS = {"nan", "none", "nat", "null"}


def safe_str(value: Any) -> str:
    """Coerce any value to a display-safe string: strip whitespace and
    collapse pandas/JSON null-like tokens (NaN, None, NaT, null) to "".
    Extracted from 13 near-identical copies of this exact function found
    across ai_core/, repositories/, services/, and ui_components/."""
    value_str = str(value or "").strip()
    if value_str.lower() in _NULL_LIKE_TOKENS:
        return ""
    return value_str


def json_dump(data: dict) -> str:
    """json.dumps with a None-safe default and str() fallback for
    non-JSON-serializable values (e.g. Decimal, datetime)."""
    return json.dumps(data or {}, default=str)
