from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ActiveDriver:
    """Phase 10B: a roster driver available for Dispatch Board assignment.
    Mirrors the three columns services.driver_roster_service.
    list_active_drivers() actually selects - no fields invented here that
    the underlying query doesn't provide."""

    driver_name: str
    phone: str
    truck_number: str
