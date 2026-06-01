from __future__ import annotations

import pandas as pd
import streamlit as st

from db_client import execute, read_df


def _refresh() -> None:
    st.cache_data.clear()
    st.rerun()


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
    execute(
        """
        insert into drivers (carrier_id, driver_name, phone, email, truck_number)
        values (:carrier_id, :driver_name, :phone, :email, :truck_number)
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
            d.driver_name,
            c.company_name as carrier,
            d.phone,
            d.email,
            d.truck_number,
            d.created_at
        from drivers d
        left join carriers c on c.id = d.carrier_id
        order by d.driver_name
        """
    )
    st.dataframe(drivers, use_container_width=True, hide_index=True)

    carriers = read_df("select id, company_name from carriers order by company_name")
    carrier_options = {"No Carrier": None}
    carrier_options.update({row["company_name"]: int(row["id"]) for _, row in carriers.iterrows()})

    with st.expander("Add driver", expanded=False):
        with st.form("driver_form", clear_on_submit=True):
            driver_name = st.text_input("Driver Name *")
            carrier_label = st.selectbox("Carrier", list(carrier_options.keys()))
            phone = st.text_input("Phone")
            email = st.text_input("Email")
            truck_number = st.text_input("Truck Number")
            submitted = st.form_submit_button("Save Driver")

        if submitted:
            if not driver_name.strip():
                st.error("Driver Name is required.")
            else:
                _upsert_driver(
                    {
                        "carrier_id": carrier_options[carrier_label],
                        "driver_name": driver_name.strip(),
                        "phone": phone.strip() or None,
                        "email": email.strip() or None,
                        "truck_number": truck_number.strip() or None,
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
