# application/admin/commands.py

from __future__ import annotations

"""Master-data (customer/warehouse/carrier/driver) mutation commands -
admin_pages.py called db_client.execute() directly with zero
authorization; page visibility alone (Master Data lives under
"Admin / Diagnostics", not offered to dispatcher/accounting at all) was
the only thing standing between a role and these upserts. Each command
here requires Permission.MASTER_DATA_EDIT before writing, independent of
which page/interface calls it - the same invariant application/loads/
commands.py established in Phase 5."""

from typing import Any

from application.auth.models import AuthenticatedActor
from application.auth.permissions import Permission, require_permission
from application.exceptions import ValidationError


def upsert_customer(*, actor: AuthenticatedActor, company_name: str, contact_name: str = "", email: str = "", phone: str = "") -> None:
    require_permission(actor, Permission.MASTER_DATA_EDIT)

    company_name = company_name.strip()
    if not company_name:
        raise ValidationError("Company Name is required.")

    from db_client import execute

    execute(
        """
        insert into customers (company_name, contact_name, email, phone)
        values (:company_name, :contact_name, :email, :phone)
        on conflict (company_name)
        do update set
            contact_name = excluded.contact_name,
            email = excluded.email,
            phone = excluded.phone
        """,
        {
            "company_name": company_name,
            "contact_name": contact_name.strip() or None,
            "email": email.strip() or None,
            "phone": phone.strip() or None,
        },
    )


def upsert_warehouse(
    *,
    actor: AuthenticatedActor,
    warehouse_name: str,
    address: str = "",
    city: str = "",
    state: str = "",
    zip_code: str = "",
    contact_name: str = "",
    phone: str = "",
) -> None:
    require_permission(actor, Permission.MASTER_DATA_EDIT)

    warehouse_name = warehouse_name.strip()
    if not warehouse_name:
        raise ValidationError("Warehouse Name is required.")

    from db_client import execute

    execute(
        """
        insert into warehouses (warehouse_name, address, city, state, zip_code, contact_name, phone)
        values (:warehouse_name, :address, :city, :state, :zip_code, :contact_name, :phone)
        on conflict (warehouse_name)
        do update set
            address = excluded.address,
            city = excluded.city,
            state = excluded.state,
            zip_code = excluded.zip_code,
            contact_name = excluded.contact_name,
            phone = excluded.phone
        """,
        {
            "warehouse_name": warehouse_name,
            "address": address.strip() or None,
            "city": city.strip() or None,
            "state": state.strip() or None,
            "zip_code": zip_code.strip() or None,
            "contact_name": contact_name.strip() or None,
            "phone": phone.strip() or None,
        },
    )


def upsert_carrier(
    *, actor: AuthenticatedActor, company_name: str, contact_name: str = "", email: str = "", phone: str = "", mc_number: str = ""
) -> None:
    require_permission(actor, Permission.MASTER_DATA_EDIT)

    company_name = company_name.strip()
    if not company_name:
        raise ValidationError("Carrier Company Name is required.")

    from db_client import execute

    execute(
        """
        insert into carriers (company_name, contact_name, email, phone, mc_number)
        values (:company_name, :contact_name, :email, :phone, :mc_number)
        on conflict (company_name)
        do update set
            contact_name = excluded.contact_name,
            email = excluded.email,
            phone = excluded.phone,
            mc_number = excluded.mc_number
        """,
        {
            "company_name": company_name,
            "contact_name": contact_name.strip() or None,
            "email": email.strip() or None,
            "phone": phone.strip() or None,
            "mc_number": mc_number.strip() or None,
        },
    )


def create_driver(
    *,
    actor: AuthenticatedActor,
    driver_name: str,
    carrier_id: int | None = None,
    phone: str = "",
    email: str = "",
    truck_number: str = "",
) -> None:
    require_permission(actor, Permission.MASTER_DATA_EDIT)

    driver_name = driver_name.strip()
    if not driver_name:
        raise ValidationError("Driver Name is required.")

    from db_client import execute

    execute(
        """
        insert into drivers (carrier_id, driver_name, phone, email, truck_number)
        values (:carrier_id, :driver_name, :phone, :email, :truck_number)
        """,
        {
            "carrier_id": carrier_id,
            "driver_name": driver_name,
            "phone": phone.strip() or None,
            "email": email.strip() or None,
            "truck_number": truck_number.strip() or None,
        },
    )
