from __future__ import annotations

import pandas as pd
import streamlit as st

from database.db_client import execute, read_df


def _refresh() -> None:
    st.cache_data.clear()
    st.rerun()


def _blank_to_none(value):
    text = str(value or "").strip()
    return text or None


def _optional_date(value):
    text = _blank_to_none(value)
    if not text:
        return None

    parsed = pd.to_datetime(text, errors="coerce")
    if pd.isna(parsed):
        return None
    return parsed.date()


def _optional_int(value):
    text = _blank_to_none(value)
    if not text:
        return None

    try:
        return int(float(text.replace(",", "")))
    except Exception:
        return None


def _optional_decimal(value):
    text = _blank_to_none(value)
    if not text:
        return None

    try:
        return float(text.replace("$", "").replace(",", ""))
    except Exception:
        return None


def _upsert_customer(row: dict) -> None:
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
        row,
    )


def _upsert_warehouse(row: dict) -> None:
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
        row,
    )


def _upsert_carrier(row: dict) -> None:
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
        row,
    )


def _upsert_driver(row: dict) -> None:
    # motive_password is deliberately not written here (Phase 1 backend-
    # boundary security cleanup): the column still exists on `drivers`
    # (dropping it is a separate, approved migration) but the app no
    # longer collects or persists a new plaintext value for it. Existing
    # values are untouched by this statement and should be rotated
    # manually. See docs/architecture/BACKEND_BOUNDARY_PHASE_1.md,
    # "Known limitations".
    execute(
        """
        with updated as (
            update drivers
            set
                carrier_id = :carrier_id,
                phone = :phone,
                email = :email,
                dashcam_number = :dashcam_number,
                connector_type = :connector_type,
                driver_license = :driver_license,
                dob = :dob,
                license_plate = :license_plate,
                vin = :vin,
                truck_year = :truck_year,
                truck_make = :truck_make,
                truck_weight = :truck_weight,
                truck_value = :truck_value,
                hire_date = :hire_date,
                motive_id = :motive_id,
                status = coalesce(:status, 'Active'),
                notes = :notes
            where lower(driver_name) = lower(:driver_name)
              and coalesce(truck_number, '') = coalesce(:truck_number, '')
            returning id
        )
        insert into drivers (
            carrier_id,
            driver_name,
            phone,
            email,
            truck_number,
            dashcam_number,
            connector_type,
            driver_license,
            dob,
            license_plate,
            vin,
            truck_year,
            truck_make,
            truck_weight,
            truck_value,
            hire_date,
            motive_id,
            status,
            notes
        )
        select
            :carrier_id,
            :driver_name,
            :phone,
            :email,
            :truck_number,
            :dashcam_number,
            :connector_type,
            :driver_license,
            :dob,
            :license_plate,
            :vin,
            :truck_year,
            :truck_make,
            :truck_weight,
            :truck_value,
            :hire_date,
            :motive_id,
            coalesce(:status, 'Active'),
            :notes
        where not exists (select 1 from updated)
        """,
        row,
    )


def render_customers_admin() -> None:
    st.subheader("Customer List")

    customers = read_df(
        """
        select id, company_name, contact_name, email, phone, created_at
        from customers
        order by company_name
        """
    )
    st.dataframe(customers, use_container_width=True, hide_index=True)

    with st.expander("Add or update customer", expanded=False):
        with st.form("customer_form", clear_on_submit=True):
            company_name = st.text_input("Company Name *")
            contact_name = st.text_input("Contact Name")
            email = st.text_input("Email")
            phone = st.text_input("Phone")
            submitted = st.form_submit_button("Save Customer")

        if submitted:
            if not company_name.strip():
                st.error("Company Name is required.")
            else:
                _upsert_customer(
                    {
                        "company_name": company_name.strip(),
                        "contact_name": contact_name.strip() or None,
                        "email": email.strip() or None,
                        "phone": phone.strip() or None,
                    }
                )
                st.success("Customer saved.")
                _refresh()


def render_warehouses_admin() -> None:
    st.subheader("Warehouses and Addresses")

    warehouses = read_df(
        """
        select id, warehouse_name, address, city, state, zip_code, contact_name, phone, created_at
        from warehouses
        order by warehouse_name
        """
    )
    st.dataframe(warehouses, use_container_width=True, hide_index=True)

    with st.expander("Add or update warehouse", expanded=False):
        with st.form("warehouse_form", clear_on_submit=True):
            warehouse_name = st.text_input("Warehouse Name *")
            address = st.text_input("Address")
            col1, col2, col3 = st.columns(3)
            city = col1.text_input("City")
            state = col2.text_input("State", value="TX")
            zip_code = col3.text_input("ZIP")
            contact_name = st.text_input("Contact Name")
            phone = st.text_input("Phone")
            submitted = st.form_submit_button("Save Warehouse")

        if submitted:
            if not warehouse_name.strip():
                st.error("Warehouse Name is required.")
            else:
                _upsert_warehouse(
                    {
                        "warehouse_name": warehouse_name.strip(),
                        "address": address.strip() or None,
                        "city": city.strip() or None,
                        "state": state.strip() or None,
                        "zip_code": zip_code.strip() or None,
                        "contact_name": contact_name.strip() or None,
                        "phone": phone.strip() or None,
                    }
                )
                st.success("Warehouse saved.")
                _refresh()


def render_carriers_admin() -> None:
    st.subheader("Carriers")

    carriers = read_df(
        """
        select id, company_name, contact_name, email, phone, mc_number, created_at
        from carriers
        order by company_name
        """
    )
    st.dataframe(carriers, use_container_width=True, hide_index=True)

    with st.expander("Add or update carrier", expanded=False):
        with st.form("carrier_form", clear_on_submit=True):
            company_name = st.text_input("Carrier Company Name *")
            contact_name = st.text_input("Contact Name")
            email = st.text_input("Email")
            phone = st.text_input("Phone")
            mc_number = st.text_input("MC Number")
            submitted = st.form_submit_button("Save Carrier")

        if submitted:
            if not company_name.strip():
                st.error("Carrier Company Name is required.")
            else:
                _upsert_carrier(
                    {
                        "company_name": company_name.strip(),
                        "contact_name": contact_name.strip() or None,
                        "email": email.strip() or None,
                        "phone": phone.strip() or None,
                        "mc_number": mc_number.strip() or None,
                    }
                )
                st.success("Carrier saved.")
                _refresh()


def render_drivers_admin() -> None:
    st.subheader("Drivers")

    drivers = read_df(
        """
        select
            d.id,
            d.status,
            d.driver_name,
            c.company_name as carrier,
            d.phone,
            d.email,
            d.truck_number,
            d.license_plate,
            d.vin,
            d.truck_year,
            d.truck_make,
            d.truck_weight,
            d.truck_value,
            d.driver_license,
            d.dob,
            d.hire_date,
            d.dashcam_number,
            d.connector_type,
            d.motive_id,
            case
                when coalesce(d.motive_password, '') = '' then 'No'
                else 'Yes - rotate manually'
            end as legacy_motive_credential,
            d.notes,
            d.updated_at
        from drivers d
        left join carriers c on c.id = d.carrier_id
        order by
            case d.status
                when 'Active' then 0
                when 'Historical' then 1
                when 'Inactive' then 2
                else 3
            end,
            d.driver_name
        """
    )
    st.dataframe(drivers, use_container_width=True, hide_index=True)
    if (drivers["legacy_motive_credential"] == "Yes - rotate manually").any():
        st.caption(
            "Some drivers still have a legacy Motive password stored from before this "
            "field was retired. The app no longer reads, displays, or writes it - "
            "rotate those credentials directly in Motive when convenient."
        )

    carriers = read_df("select id, company_name from carriers order by company_name")
    carrier_options = {"No Carrier": None}
    carrier_options.update({row["company_name"]: int(row["id"]) for _, row in carriers.iterrows()})

    with st.expander("Add or update driver", expanded=False):
        with st.form("driver_form", clear_on_submit=True):
            driver_name = st.text_input("Driver Name *")
            carrier_label = st.selectbox("Carrier", list(carrier_options.keys()))
            status = st.selectbox("Status", ["Active", "Historical", "Inactive"])
            phone = st.text_input("Phone")
            email = st.text_input("Email")
            col1, col2, col3 = st.columns(3)
            truck_number = col1.text_input("Truck Number")
            license_plate = col2.text_input("License Plate")
            truck_year = col3.text_input("Truck Year")
            col4, col5, col6 = st.columns(3)
            truck_make = col4.text_input("Truck Make")
            vin = col5.text_input("VIN")
            truck_weight = col6.text_input("Truck Weight")
            col7, col8, col9 = st.columns(3)
            truck_value = col7.text_input("Truck Value")
            driver_license = col8.text_input("Driver License")
            dob = col9.text_input("DOB")
            col10, col11, col12 = st.columns(3)
            hire_date = col10.text_input("Hire Date")
            dashcam_number = col11.text_input("DashCam #")
            connector_type = col12.text_input("Connector Type")
            motive_id = st.text_input("Motive ID")
            # Motive Password is deliberately not collected here (Phase 1
            # security cleanup) - see docs/architecture/
            # BACKEND_BOUNDARY_PHASE_1.md, "Known limitations".
            notes = st.text_area("Notes")
            submitted = st.form_submit_button("Save Driver")

        if submitted:
            if not driver_name.strip():
                st.error("Driver Name is required.")
            else:
                _upsert_driver(
                    {
                        "carrier_id": carrier_options[carrier_label],
                        "driver_name": driver_name.strip(),
                        "phone": _blank_to_none(phone),
                        "email": _blank_to_none(email),
                        "truck_number": _blank_to_none(truck_number),
                        "dashcam_number": _blank_to_none(dashcam_number),
                        "connector_type": _blank_to_none(connector_type),
                        "driver_license": _blank_to_none(driver_license),
                        "dob": _optional_date(dob),
                        "license_plate": _blank_to_none(license_plate),
                        "vin": _blank_to_none(vin),
                        "truck_year": _optional_int(truck_year),
                        "truck_make": _blank_to_none(truck_make),
                        "truck_weight": _optional_decimal(truck_weight),
                        "truck_value": _optional_decimal(truck_value),
                        "hire_date": _optional_date(hire_date),
                        "motive_id": _blank_to_none(motive_id),
                        "status": status,
                        "notes": _blank_to_none(notes),
                    }
                )
                st.success("Driver saved.")
                _refresh()


def render_master_data_admin() -> None:
    st.subheader("Master Data / Admin")

    tab1, tab2, tab3, tab4 = st.tabs(
        ["Customers", "Warehouses", "Carriers", "Drivers"]
    )

    with tab1:
        render_customers_admin()

    with tab2:
        render_warehouses_admin()

    with tab3:
        render_carriers_admin()

    with tab4:
        render_drivers_admin()
