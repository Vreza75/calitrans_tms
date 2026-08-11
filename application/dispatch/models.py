# application/dispatch/models.py

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DispatchTransitionResult:
    ok: bool
    reason: str
    status: str
    closeout_stage: str
