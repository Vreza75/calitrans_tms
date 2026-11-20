from __future__ import annotations

from pydantic import BaseModel

from application.drivers.models import ActiveDriver


class DriverOut(BaseModel):
    driver_name: str
    phone: str
    truck_number: str

    @classmethod
    def from_domain(cls, driver: ActiveDriver) -> "DriverOut":
        return cls(**driver.__dict__)
