"""Phase 10B: application/drivers/queries.py::list_active_drivers - thin
wrapper over services.driver_roster_service.list_active_drivers(), proving
the DataFrame-to-DTO conversion rather than re-testing the SQL (already
covered by services/driver_roster_service.py's own tests, if any)."""
from __future__ import annotations

from unittest import mock

import pandas as pd

from application.drivers.models import ActiveDriver
from application.drivers.queries import list_active_drivers


def test_list_active_drivers_converts_dataframe_rows_to_dtos():
    df = pd.DataFrame(
        [
            {"driver_name": "Joe Driver", "phone": "5551234567", "truck_number": "T-100"},
            {"driver_name": "Ana Trucker", "phone": "5559876543", "truck_number": "T-200"},
        ]
    )
    with mock.patch("application.drivers.queries._list_active_drivers_df", return_value=df):
        result = list_active_drivers()

    assert result == [
        ActiveDriver(driver_name="Joe Driver", phone="5551234567", truck_number="T-100"),
        ActiveDriver(driver_name="Ana Trucker", phone="5559876543", truck_number="T-200"),
    ]


def test_list_active_drivers_returns_empty_list_for_empty_roster():
    with mock.patch("application.drivers.queries._list_active_drivers_df", return_value=pd.DataFrame()):
        assert list_active_drivers() == []


def test_list_active_drivers_defaults_missing_values_to_empty_string():
    df = pd.DataFrame([{"driver_name": "Joe Driver", "phone": None, "truck_number": None}])
    with mock.patch("application.drivers.queries._list_active_drivers_df", return_value=df):
        result = list_active_drivers()

    assert result == [ActiveDriver(driver_name="Joe Driver", phone="", truck_number="")]
