from __future__ import annotations

import pandas as pd
import streamlit as st

from application.admin.commands import create_driver, upsert_carrier, upsert_customer, upsert_warehouse
from application.auth.models import AuthenticatedActor
from application.auth.permissions import Permission, has_permission
from application.exceptions import AuthorizationError, ValidationError
from db_client import read_df


def _refresh() -> None:
    # customers/warehouses/carriers/drivers are read here via read_df(),
    # which is uncached - nothing elsewhere in the app caches these
    # tables either (see services/driver_roster_service.py), so a
    # st.cache_data.clear() here only ever wiped unrelated caches
    # (Operations Inbox, cases, attachments, TMS load data) for zero
    # functional benefit. st.rerun() alone re-queries fresh data.
    st.rerun()


def render_customers_admin(principal: AuthenticatedActor) -> None:
    st.subheader("Customer List")

    customers = read_df(
        """
        select id, company_name, contact_name, email, phone, created_at
        from customers
        order by company_name
        """
    )
    st.dataframe(customers, use_container_width=True, hide_index=True)

    can_edit = has_permission(principal, Permission.MASTER_DATA_EDIT)
    with st.expander("Add or update customer", expanded=False):
        if not can_edit:
            st.caption("Your role does not have permission to edit master data.")
        with st.form("customer_form", clear_on_submit=True):
            company_name = st.text_input("Company Name *")
            contact_name = st.text_input("Contact Name")
            email = st.text_input("Email")
            phone = st.text_input("Phone")
            submitted = st.form_submit_button("Save Customer", disabled=not can_edit)

        if submitted:
            try:
                upsert_customer(
                    actor=principal,
                    company_name=company_name,
                    contact_name=contact_name,
                    email=email,
                    phone=phone,
                )
            except (AuthorizationError, ValidationError) as exc:
                st.error(str(exc))
            else:
                st.success("Customer saved.")
                _refresh()


def render_warehouses_admin(principal: AuthenticatedActor) -> None:
    st.subheader("Warehouses and Addresses")

    warehouses = read_df(
        """
        select id, warehouse_name, address, city, state, zip_code, contact_name, phone, created_at
        from warehouses
        order by warehouse_name
        """
    )
    st.dataframe(warehouses, use_container_width=True, hide_index=True)

    can_edit = has_permission(principal, Permission.MASTER_DATA_EDIT)
    with st.expander("Add or update warehouse", expanded=False):
        if not can_edit:
            st.caption("Your role does not have permission to edit master data.")
        with st.form("warehouse_form", clear_on_submit=True):
            warehouse_name = st.text_input("Warehouse Name *")
            address = st.text_input("Address")
            col1, col2, col3 = st.columns(3)
            city = col1.text_input("City")
            state = col2.text_input("State", value="TX")
            zip_code = col3.text_input("ZIP")
            contact_name = st.text_input("Contact Name")
            phone = st.text_input("Phone")
            submitted = st.form_submit_button("Save Warehouse", disabled=not can_edit)

        if submitted:
            try:
                upsert_warehouse(
                    actor=principal,
                    warehouse_name=warehouse_name,
                    address=address,
                    city=city,
                    state=state,
                    zip_code=zip_code,
                    contact_name=contact_name,
                    phone=phone,
                )
            except (AuthorizationError, ValidationError) as exc:
                st.error(str(exc))
            else:
                st.success("Warehouse saved.")
                _refresh()


def render_carriers_admin(principal: AuthenticatedActor) -> None:
    st.subheader("Carriers")

    carriers = read_df(
        """
        select id, company_name, contact_name, email, phone, mc_number, created_at
        from carriers
        order by company_name
        """
    )
    st.dataframe(carriers, use_container_width=True, hide_index=True)

    can_edit = has_permission(principal, Permission.MASTER_DATA_EDIT)
    with st.expander("Add or update carrier", expanded=False):
        if not can_edit:
            st.caption("Your role does not have permission to edit master data.")
        with st.form("carrier_form", clear_on_submit=True):
            company_name = st.text_input("Carrier Company Name *")
            contact_name = st.text_input("Contact Name")
            email = st.text_input("Email")
            phone = st.text_input("Phone")
            mc_number = st.text_input("MC Number")
            submitted = st.form_submit_button("Save Carrier", disabled=not can_edit)

        if submitted:
            try:
                upsert_carrier(
                    actor=principal,
                    company_name=company_name,
                    contact_name=contact_name,
                    email=email,
                    phone=phone,
                    mc_number=mc_number,
                )
            except (AuthorizationError, ValidationError) as exc:
                st.error(str(exc))
            else:
                st.success("Carrier saved.")
                _refresh()


def render_drivers_admin(principal: AuthenticatedActor) -> None:
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

    can_edit = has_permission(principal, Permission.MASTER_DATA_EDIT)
    with st.expander("Add driver", expanded=False):
        if not can_edit:
            st.caption("Your role does not have permission to edit master data.")
        with st.form("driver_form", clear_on_submit=True):
            driver_name = st.text_input("Driver Name *")
            carrier_label = st.selectbox("Carrier", list(carrier_options.keys()))
            phone = st.text_input("Phone")
            email = st.text_input("Email")
            truck_number = st.text_input("Truck Number")
            submitted = st.form_submit_button("Save Driver", disabled=not can_edit)

        if submitted:
            try:
                create_driver(
                    actor=principal,
                    driver_name=driver_name,
                    carrier_id=carrier_options[carrier_label],
                    phone=phone,
                    email=email,
                    truck_number=truck_number,
                )
            except (AuthorizationError, ValidationError) as exc:
                st.error(str(exc))
            else:
                st.success("Driver saved.")
                _refresh()


def render_master_data_admin(principal: AuthenticatedActor) -> None:
    st.subheader("Master Data / Admin")

    tab1, tab2, tab3, tab4 = st.tabs(
        ["Customers", "Warehouses", "Carriers", "Drivers"]
    )

    with tab1:
        render_customers_admin(principal)

    with tab2:
        render_warehouses_admin(principal)

    with tab3:
        render_carriers_admin(principal)

    with tab4:
        render_drivers_admin(principal)
