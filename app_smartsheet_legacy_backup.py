from pathlib import Path
from urllib.parse import quote, unquote
import base64
import pandas as pd
import streamlit as st
from datetime import date
from order_parser import extract_text_from_pdf, parse_order_text
from email_parser import parse_email_text
from io import BytesIO
from email_client import fetch_recent_load_emails
from email_parser import parse_email_text

from smartsheet_client import (
    DispatchSmartsheetClient,
    SMARTSHEET_CUSTOMER_SHEET_ID,
    SMARTSHEET_DRIVER_SHEET_ID,
    SMARTSHEET_WAREHOUSE_SHEET_ID,
)


st.set_page_config(
    page_title="Calitrans Dispatch Center",
    page_icon="🚚",
    layout="wide",
)


# -----------------------------
# Helpers
# -----------------------------
def create_loads_from_email_pdfs(limit=10):
    client = DispatchSmartsheetClient()
    emails = fetch_recent_load_emails(limit=limit)

    created_loads = []
    skipped = []

    for email_item in emails:
        subject = email_item.get("subject", "")
        attachments = email_item.get("attachments", [])

        for attachment in attachments:
            filename = attachment.get("filename", "")

            if not filename.lower().endswith(".pdf"):
                continue

            try:
                pdf_file = BytesIO(attachment["content"])
                pdf_file.name = filename

                pdf_text = extract_text_from_pdf(pdf_file)
                parsed_data = parse_order_text(pdf_text)

                booking_number = parsed_data.get("Booking Number", "").strip()

                if not booking_number:
                    skipped.append(f"{filename}: no booking number found")
                    continue

                new_row = {
                    "TYPE": parsed_data.get("TYPE", ""),
                    "Date": normalize_smartsheet_date(parsed_data.get("Date", "")),
                    "Customer": parsed_data.get("Customer", ""),
                    "Booking Number": booking_number,
                    "Reference Number": parsed_data.get("Reference Number", ""),
                    "Container Number": parsed_data.get("Container Number", ""),
                    "Chassis": parsed_data.get("Chassis", ""),
                    "Port": parsed_data.get("Port", ""),
                    "Warehouse": parsed_data.get("Warehouse", ""),
                    "Document Cutoff": normalize_smartsheet_date(parsed_data.get("Document Cutoff", "")),
                    "Delivery Need Date": normalize_smartsheet_date(parsed_data.get("Delivery Need Date", "")),
                    "Status": "New",
                    "Dispatcher Notes": (
                        parsed_data.get("Dispatcher Notes", "")
                        + f"\nCreated from email PDF\nEmail Subject: {subject}\nPDF: {filename}"
                    ),
                }

                created_row = client.add_row(new_row)
                client.attach_file_to_row(created_row.id, pdf_file)

                created_loads.append({
                    "booking": booking_number,
                    "filename": filename,
                    "row_id": created_row.id,
                })

            except Exception as exc:
                skipped.append(f"{filename}: {exc}")

    return created_loads, skipped
def load_css():
    css_path = Path("theme.css")
    if css_path.exists():
        st.markdown(css_path.read_text(encoding="utf-8"), unsafe_allow_html=True)
def normalize_smartsheet_date(value):
    parsed = pd.to_datetime(value, errors="coerce")

    if pd.isna(parsed):
        return ""

    return parsed.strftime("%Y-%m-%d")

def image_to_base64(path: str) -> str:
    file_path = Path(path)
    if not file_path.exists():
        return ""
    return base64.b64encode(file_path.read_bytes()).decode("utf-8")
PROCESSED_EMAILS_FILE = "processed_emails.txt"


def load_processed_email_ids():
    try:
        with open(PROCESSED_EMAILS_FILE, "r") as file:
            return set(line.strip() for line in file.readlines())
    except FileNotFoundError:
        return set()


def save_processed_email_id(email_id):
    with open(PROCESSED_EMAILS_FILE, "a") as file:
        file.write(f"{email_id}\n")

@st.cache_data(ttl=60)
def load_dispatch_data() -> pd.DataFrame:
    client = DispatchSmartsheetClient()
    sheet = client.get_sheet()
    return client.rows_to_dataframe(sheet)


@st.cache_data(ttl=300)
def load_master_data():
    client = DispatchSmartsheetClient()

    customer_df = client.get_sheet_as_dataframe(SMARTSHEET_CUSTOMER_SHEET_ID)
    driver_df = client.get_sheet_as_dataframe(SMARTSHEET_DRIVER_SHEET_ID)
    warehouse_df = client.get_sheet_as_dataframe(SMARTSHEET_WAREHOUSE_SHEET_ID)

    return customer_df, driver_df, warehouse_df

def refresh_data():
    st.cache_data.clear()

def clean_df(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = df.columns.astype(str).str.strip()

    for col in required_columns:
        if col not in df.columns:
            df[col] = None

    df["TYPE"] = df["TYPE"].astype(str).str.strip()
    df["Status"] = df["Status"].astype(str).str.strip()
    df["Booking Number"] = df["Booking Number"].astype(str).str.strip()

    return df


def filter_table(df, search_text="", status_filter="All"):
    filtered = df.copy()

    if status_filter != "All" and "Status" in filtered.columns:
        filtered = filtered[filtered["Status"].astype(str).str.strip() == status_filter]

    if search_text:
        search_text = search_text.lower()
        filtered = filtered[
            filtered.astype(str)
            .apply(lambda row: row.str.lower().str.contains(search_text).any(), axis=1)
        ]

    return filtered


def normalize_type(value):
    return (
        str(value)
        .strip()
        .lower()
        .replace("exports", "export")
        .replace("imports", "import")
    )


def get_type_rows(df, type_name):
    if "TYPE" not in df.columns:
        return df.iloc[0:0]

    normalized_type = df["TYPE"].apply(normalize_type)
    target = normalize_type(type_name)

    return df[normalized_type == target]


def get_booking_summary(data: pd.DataFrame):
    data = data.copy()

    date_col = "Date" if "Date" in data.columns else "Created Date"

    if date_col in data.columns:
        data["Date"] = data[date_col]
        data = data.sort_values("Date")

    data["Booking Number"] = data["Booking Number"].astype(str).str.strip()
    data = data[data["Booking Number"] != ""]
    data = data[data["Booking Number"].str.lower() != "none"]

    if data.empty:
        return pd.DataFrame(columns=summary_columns)

    summary = (
        data.groupby("Booking Number", as_index=False)
        .agg({
            "Date": "first",
            "Customer": "first",
            "Warehouse": "first",
            "Reference Number": "first",
        })
    )

    return summary[[col for col in summary_columns if col in summary.columns]]


def show_booking_summary(data: pd.DataFrame, title: str):
    st.markdown(f'<div class="section-title">{title}</div>', unsafe_allow_html=True)

    summary_df = get_booking_summary(data)

    if summary_df.empty:
        st.info("No bookings found.")
        return

    header = st.columns([1.2, 1.5, 2, 2, 1.5])
    header[0].markdown("**Date**")
    header[1].markdown("**Booking #**")
    header[2].markdown("**Customer**")
    header[3].markdown("**Warehouse**")
    header[4].markdown("**Reference #**")

    for _, row in summary_df.iterrows():
        booking = str(row["Booking Number"])
        current_tab = st.session_state.get("current_tab", "load_board")
        booking_url = f"?booking={quote(booking)}&tab={current_tab}"

        col1, col2, col3, col4, col5 = st.columns([1.2, 1.5, 2, 2, 1.5])

        col1.write(row.get("Date", ""))
        col2.markdown(
            f'<a href="{booking_url}">{booking}</a>',
            unsafe_allow_html=True,
        )
        col3.write(row.get("Customer", ""))
        col4.write(row.get("Warehouse", ""))
        col5.write(row.get("Reference Number", ""))


def save_day_changes(original_df, edited_df, editable_columns):
    client = DispatchSmartsheetClient()
    changes_saved = 0

    original = original_df.reset_index(drop=True)
    edited = edited_df.reset_index(drop=True)

    for i in range(len(edited)):
        row_id = int(original.loc[i, "_row_id"])
        updates = {}

        for col in editable_columns:
            if col not in original.columns or col not in edited.columns:
                continue

            old_value = original.loc[i, col]
            new_value = edited.loc[i, col]

            old_value = "" if pd.isna(old_value) else old_value
            new_value = "" if pd.isna(new_value) else new_value

            if old_value != new_value:
                updates[col] = new_value

        if updates:
            client.update_row_fields(row_id, updates)
            changes_saved += 1

    return changes_saved


# -----------------------------
# Column Config
# -----------------------------

summary_columns = [
    "Date",
    "Booking Number",
    "Customer",
    "Warehouse",
    "Reference Number",
]
detail_columns = [
    "Date",
    "Delivery Need Date",
    "Container Number",
    "Warehouse",
    "Address",
    "Status",
    "LFD",
    "Driver Name",
    "Truck Assigned",
    "Chassis",
    "Size",
    "Booking Number",
    "Billing Notes",
    "Dispatcher Notes",
]

editable_day_columns = [
    "Status",
    "Driver Name",
    "Truck Assigned",
    "Chassis",
    "Dispatcher Notes",
]

day_display_columns = [
    "Date",
    "Delivery Need Date",
    "Container Number",
    "Warehouse",
    "Status",
    "LFD",
    "Driver Name",
    "Truck Assigned",
    "Chassis",
    "Size",
    "Booking Number",
    "Dispatcher Notes",
]

required_columns = list(set(
    summary_columns
    + detail_columns
    + editable_day_columns
    + [
        "_row_id",
        "TYPE",
        "Load ID",
        "Customer",
        "Reference Number",
        "Port",
        "Document Cutoff",
        "Created Date",
    ]
))

otr_status_options = [
    "New",
    "Hold/Need Info",
    "Ready to Dispatch",
    "Assigned",
    "En Route to Pickup",
    "At Pickup",
    "Loaded",
    "En Route To Delivery",
    "Delivered",
    "POD Received",
    "Ready for ProfitTools",
    "Exported to ProfitTools",
    "Invoiced",
    "Closed",
    "Cancelled",
    "Returning Empty",
]


# -----------------------------
# Load Page Assets
# -----------------------------

load_css()

banner_b64 = image_to_base64("assets/header_banner.png")

if banner_b64:
    st.markdown(
        f"""
        <div class="banner-wrapper">
            <img class="header-banner" src="data:image/png;base64,{banner_b64}" />
        </div>
        """,
        unsafe_allow_html=True,
    )


# -----------------------------
# Load Smartsheet Data
# -----------------------------

try:
    df = load_dispatch_data()
except Exception as exc:
    st.error(f"Could not load Smartsheet data: {exc}")
    st.stop()

if df.empty:
    st.warning("No rows found in the dispatch sheet.")
    st.stop()

df = clean_df(df)

customer_df, driver_df, warehouse_df = load_master_data()

# -----------------------------
# Booking Detail Page
# -----------------------------

selected_booking = st.query_params.get("booking", None)

if selected_booking:
    selected_booking = unquote(str(selected_booking))

    booking_df = df[
        df["Booking Number"]
        .astype(str)
        .str.strip()
        .eq(selected_booking.strip())
    ].copy()

    main_customer = (
        booking_df["Customer"].dropna().astype(str).iloc[0]
        if not booking_df.empty and "Customer" in booking_df.columns
        else ""
    )

    st.markdown("<div style='margin-top:-35px'></div>", unsafe_allow_html=True)
    st.markdown(f"### Main Customer: {main_customer}")
    st.title(f"📦 Booking Details: {selected_booking}")

    delivery_col = "Delivery Need Date"

    if delivery_col not in booking_df.columns:
        st.warning("Missing Smartsheet column: Delivery Need Date")
    else:
        booking_df[delivery_col] = pd.to_datetime(
            booking_df[delivery_col],
            errors="coerce",
        )

        delivery_dates = (
            booking_df[delivery_col]
            .dropna()
            .sort_values()
            .dt.date
            .unique()
        )

        st.markdown("### 📅 Weekly Live Status")

        if len(delivery_dates) == 0:
            st.info("No delivery need dates found for this booking.")
        else:
            delivery_tabs = st.tabs([
                pd.to_datetime(d).strftime("%A %B %d")
                for d in delivery_dates
            ])

            for tab, delivery_date in zip(delivery_tabs, delivery_dates):
                with tab:
                    day_df = booking_df[
                        booking_df[delivery_col].dt.date == delivery_date
                    ].copy()

                    if day_df.empty:
                        st.info("No loads for this day.")
                        continue

                    visible_day_columns = [
                        col for col in day_display_columns
                        if col in day_df.columns
                    ]

                    driver_master_name_col = "Driver Name"
                    driver_master_truck_col = "Truck Assigned"

                    dispatch_driver_col = "Driver Name"
                    dispatch_truck_col = "Truck Assigned"

                    driver_df[driver_master_name_col] = driver_df[driver_master_name_col].fillna("").astype(str).str.strip()
                    driver_df[driver_master_truck_col] = driver_df[driver_master_truck_col].fillna("").astype(str).str.strip()

                    driver_options = [""] + sorted(
                        driver_df[driver_master_name_col]
                        [driver_df[driver_master_name_col] != ""]
                        .unique()
                    )

                    edited_day = st.data_editor(
                        day_df[visible_day_columns],
                        use_container_width=True,
                        hide_index=True,
                        column_config={
                            "Status": st.column_config.SelectboxColumn(
                                "Status",
                                options=otr_status_options,
                            ),
                            dispatch_driver_col: st.column_config.SelectboxColumn(
                                "Driver",
                                options=driver_options,
                            ),
                        },
                        disabled=[
                            col for col in visible_day_columns
                            if col not in editable_day_columns
                        ],
                        key=f"day_editor_{delivery_date}",
                        height=330,
                    )

                    driver_truck_map = dict(
                        zip(
                            driver_df[driver_master_name_col],
                            driver_df[driver_master_truck_col],
                        )
                    )

                    if dispatch_driver_col in edited_day.columns and dispatch_truck_col in edited_day.columns:
                        edited_day[dispatch_driver_col] = edited_day[dispatch_driver_col].fillna("").astype(str).str.strip()

                        edited_day[dispatch_truck_col] = edited_day.apply(
                            lambda row: driver_truck_map.get(row[dispatch_driver_col], row[dispatch_truck_col]),
                            axis=1,
                        )     

                    if st.button(
                        f"💾 Save {pd.to_datetime(delivery_date).strftime('%A %B %d')} Changes",
                        key=f"save_day_{delivery_date}",
                    ):
                        changes_saved = save_day_changes(
                            day_df,
                            edited_day,
                            editable_day_columns,
                        )

                        if changes_saved:
                            st.success(f"Saved {changes_saved} change(s).")
                            refresh_data()
                            st.rerun()
                        else:
                            st.info("No changes detected.")

    return_tab = st.query_params.get("tab", "load_board")

   

    if return_tab:
        st.info(f"Returned from booking detail. Open tab: {return_tab.replace('_', ' ').title()}")
    if st.button("⬅ Back to Dashboard"):
        st.query_params.clear()
        st.query_params["tab"] = return_tab
        st.rerun()

    st.stop()


# -----------------------------
# KPI Cards
# -----------------------------

active_statuses = [
    "Ready to Dispatch",
    "Assigned",
    "En Route to Pickup",
    "Hold/Need Info",
]

active_count = len(df[df["Status"].isin(active_statuses)])
ready_count = len(df[df["Status"] == "Ready to Dispatch"])
assigned_count = len(df[df["Status"] == "Assigned"])
hold_count = len(df[df["Status"] == "Hold/Need Info"])
exported_count = len(df[df["Status"] == "Exported to ProfitTools"])


def kpi_card(icon, label, value, sub, css_class):
    st.markdown(
        f"""
        <div class="kpi-card">
          <div class="kpi-icon {css_class}">{icon}</div>
          <div>
            <div class="kpi-label {css_class}">{label}</div>
            <div class="kpi-value">{value}</div>
            <div class="kpi-sub">{sub}</div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


k1, k2, k3, k4, k5 = st.columns(5)

with k1:
    kpi_card("🚛", "Active Loads", active_count, "Total active loads", "kpi-blue")
with k2:
    kpi_card("✅", "Ready", ready_count, "Ready to dispatch", "kpi-green")
with k3:
    kpi_card("👤", "Assigned", assigned_count, "Currently assigned", "kpi-orange")
with k4:
    kpi_card("⚠️", "Hold / Need Info", hold_count, "Needs attention", "kpi-yellow")
with k5:
    kpi_card("📤", "Exported", exported_count, "ProfitTools exported", "kpi-blue")

st.markdown("<br>", unsafe_allow_html=True)


# -----------------------------
# Main Tabs
# -----------------------------

tabs = st.tabs([
    "➕ New Load Entry",
    "📋 Load Board",
    "🚛 OTR Imports",
    "🚛 OTR Exports",
    "🚛 OTR Local Imports",
    "📋 Files to Export",
])


with tabs[0]:
    st.subheader("➕ New Load Entry")

    parsed_pdf_data = {}

    uploaded_file = st.file_uploader(
        "Upload Order PDF",
        type=["pdf"],
        key="new_load_pdf",
    )

    if uploaded_file is not None:
        try:
            pdf_text = extract_text_from_pdf(uploaded_file)
            parsed_pdf_data = parse_order_text(pdf_text)

            st.success("PDF parsed. Review/edit fields before creating load.")
            st.write("Parsed PDF data:", parsed_pdf_data)

        except Exception as exc:
            st.warning(f"PDF could not be parsed. You can still enter the load manually. Error: {exc}")
            
    st.markdown("### Create Loads From Email PDFs")

    if st.button("Check Emails With Load PDFs"):
        processed_email_ids = load_processed_email_ids()
        emails = fetch_recent_load_emails(limit=25)

        filtered_emails = []

        for email_item in emails:
            email_id = str(email_item.get("id", ""))

            if email_id in processed_email_ids:
                continue

            subject = email_item.get("subject", "").lower()
            body = email_item.get("body", "").lower()
            attachments = email_item.get("attachments", [])

            has_pdf = any(
                attachment.get("filename", "").lower().endswith(".pdf")
                for attachment in attachments
            )

            has_booking_words = any(
                word in subject or word in body
                for word in ["booking", "bkg", "load", "work order", "confirmation"]
            )

            if has_pdf and has_booking_words:
                filtered_emails.append(email_item)

        st.session_state["email_load_candidates"] = filtered_emails

    if "email_load_candidates" in st.session_state:
        email_candidates = st.session_state["email_load_candidates"]

        if not email_candidates:
            st.info("No new load emails with PDFs found.")
        else:
            email_options = [
                f"{i + 1}. {email_item['subject']} — {email_item['from']}"
                for i, email_item in enumerate(email_candidates)
            ]

            selected_email_label = st.selectbox(
                "Select Email to Create Load From",
                [""] + email_options,
                key="selected_email_create_load",
            )

            if selected_email_label:
                selected_index = email_options.index(selected_email_label)
                selected_email = email_candidates[selected_index]

                st.write("Subject:", selected_email["subject"])
                st.write("From:", selected_email["from"])

                pdf_attachments = [
                    attachment for attachment in selected_email.get("attachments", [])
                    if attachment.get("filename", "").lower().endswith(".pdf")
                ]

                pdf_options = [
                    attachment["filename"]
                    for attachment in pdf_attachments
                ]

                selected_pdf_name = st.selectbox(
                    "Select PDF Attachment",
                    pdf_options,
                    key="selected_email_pdf_attachment",
                )

            if st.button("Create Load From Selected Email PDF"):
                selected_attachment = next(
                    attachment for attachment in pdf_attachments
                    if attachment["filename"] == selected_pdf_name
                )

                pdf_file = BytesIO(selected_attachment["content"])
                pdf_file.name = selected_attachment["filename"]

                pdf_text = extract_text_from_pdf(pdf_file)
                parsed_data = parse_order_text(pdf_text)

                new_row = {
                    "TYPE": parsed_data.get("TYPE", ""),
                    "Date": normalize_smartsheet_date(parsed_data.get("Date", "")),
                    "Customer": parsed_data.get("Customer", ""),
                    "Booking Number": parsed_data.get("Booking Number", ""),
                    "Reference Number": parsed_data.get("Reference Number", ""),
                    "Container Number": parsed_data.get("Container Number", ""),
                    "Chassis": parsed_data.get("Chassis", ""),
                    "Port": parsed_data.get("Port", ""),
                    "Warehouse": parsed_data.get("Warehouse", ""),
                    "Document Cutoff": normalize_smartsheet_date(parsed_data.get("Document Cutoff", "")),
                    "Delivery Need Date": normalize_smartsheet_date(parsed_data.get("Delivery Need Date", "")),
                    "Status": "New",
                    "Dispatcher Notes": (
                        parsed_data.get("Dispatcher Notes", "")
                        + f"\nCreated from email PDF"
                        + f"\nEmail Subject: {selected_email['subject']}"
                        + f"\nPDF: {selected_pdf_name}"
                    ),
                }

                client = DispatchSmartsheetClient()
                created_row = client.add_row(new_row)
                client.attach_file_to_row(created_row.id, pdf_file)

                save_processed_email_id(str(selected_email["id"]))

                st.success(f"Load created. Row ID: {created_row.id}")
                st.write("Created Load Data:", new_row)

                refresh_data()
                st.rerun()
                
    st.markdown("### Pull Load From Email")

    if st.button("Check Recent Load Emails"):
        st.session_state["load_emails"] = fetch_recent_load_emails(limit=10)

    if "load_emails" in st.session_state:
        email_options = [
            f"{i + 1}. {item['subject']} — {item['from']}"
            for i, item in enumerate(st.session_state["load_emails"])
        ]

        selected_email_label = st.selectbox(
            "Select Email",
            [""] + email_options,
            key="selected_load_email",
        )

        if selected_email_label:
            selected_index = email_options.index(selected_email_label)
            selected_email = st.session_state["load_emails"][selected_index]

            st.write("Subject:", selected_email["subject"])
            st.write("From:", selected_email["from"])

            if st.button("Parse Selected Email"):
                st.session_state["parsed_email_data"] = parse_email_text(
                    selected_email["subject"],
                    selected_email["body"],
                )

    if "parsed_email_data" in st.session_state:
        parsed_pdf_data.update(st.session_state["parsed_email_data"])
        st.write("Parsed Email Data:", st.session_state["parsed_email_data"])

    with st.form("new_load_form", clear_on_submit=True):
        load_type_options = ["OTR Import", "OTR Export", "OTR Local imports"]
        parsed_type = parsed_pdf_data.get("TYPE", "OTR Import")

        load_type = st.selectbox(
            "TYPE",
            load_type_options,
            index=load_type_options.index(parsed_type) if parsed_type in load_type_options else 0,
            key="new_load_type",
        )

        date_value = parsed_pdf_data.get("Date") or date.today()

        load_date = st.date_input(
            "Date",
            value=pd.to_datetime(date_value, errors="coerce").date()
            if pd.notna(pd.to_datetime(date_value, errors="coerce"))
            else date.today(),
            key="new_load_date",
        )

        customer_options = sorted(
            customer_df["Customer Name"].dropna().astype(str).str.strip().unique()
        )

        parsed_customer = parsed_pdf_data.get("Customer", "")
        if parsed_customer and parsed_customer not in customer_options:
            customer_options = [parsed_customer] + customer_options

        customer_list = [""] + customer_options

        customer = st.selectbox(
            "Customer",
            customer_list,
            index=customer_list.index(parsed_customer) if parsed_customer in customer_list else 0,
            key="new_load_customer",
        )

        warehouse_options = sorted(
            warehouse_df["Warehouse"].dropna().astype(str).str.strip().unique()
        )

        parsed_warehouse = parsed_pdf_data.get("Warehouse", "")
        if parsed_warehouse and parsed_warehouse not in warehouse_options:
            warehouse_options = [parsed_warehouse] + warehouse_options

        warehouse_list = [""] + warehouse_options

        warehouse = st.selectbox(
            "Warehouse",
            warehouse_list,
            index=warehouse_list.index(parsed_warehouse) if parsed_warehouse in warehouse_list else 0,
            key="new_load_warehouse",
        )

        booking_number = st.text_input(
            "Booking Number",
            value=parsed_pdf_data.get("Booking Number", ""),
            key="new_load_booking_number",
        )

        reference_number = st.text_input(
            "Reference Number",
            value=parsed_pdf_data.get("Reference Number", ""),
            key="new_load_reference_number",
        )

        container_number = st.text_input(
            "Container Number",
            value=parsed_pdf_data.get("Container Number", ""),
            key="new_load_container_number",
        )

        chassis = st.text_input(
            "Chassis",
            value=parsed_pdf_data.get("Chassis", ""),
            key="new_load_chassis",
        )

        port = st.text_input(
            "Port",
            value=parsed_pdf_data.get("Port", ""),
            key="new_load_port",
        )

        document_cutoff_value = parsed_pdf_data.get("Document Cutoff") or date.today()

        document_cutoff = st.date_input(
            "Document Cutoff",
            value=pd.to_datetime(document_cutoff_value, errors="coerce").date()
            if pd.notna(pd.to_datetime(document_cutoff_value, errors="coerce"))
            else date.today(),
            key="new_load_document_cutoff",
        )

        delivery_need_date_value = parsed_pdf_data.get("Delivery Need Date") or date.today()

        delivery_need_date = st.date_input(
            "Delivery Need Date",
            value=pd.to_datetime(delivery_need_date_value, errors="coerce").date()
            if pd.notna(pd.to_datetime(delivery_need_date_value, errors="coerce"))
            else date.today(),
            key="new_load_delivery_need_date",
        )

        notes = st.text_area(
            "Dispatcher Notes",
            value=parsed_pdf_data.get("Dispatcher Notes", ""),
            key="new_load_dispatcher_notes",
        )

        submitted = st.form_submit_button("Create Load")

    if submitted:
        new_row = {
            "TYPE": load_type,
            "Date": str(load_date),
            "Customer": customer,
            "Booking Number": booking_number,
            "Reference Number": reference_number,
            "Container Number": container_number,
            "Chassis": chassis,
            "Port": port,
            "Warehouse": warehouse,
            "Document Cutoff": str(document_cutoff),
            "Delivery Need Date": str(delivery_need_date),
            "Status": "New",
            "Dispatcher Notes": notes,
        }

        client = DispatchSmartsheetClient()
        created_row = client.add_row(new_row)

        if uploaded_file is not None:
            client.attach_file_to_row(created_row.id, uploaded_file)

        st.success(f"Load created. Row ID: {created_row.id}")
        refresh_data()
        st.rerun()
    st.subheader("➕ New Load Entry")
    
    parsed_pdf_data = {}

    if uploaded_file is not None:
        try:
            pdf_text = extract_text_from_pdf(uploaded_file)
            parsed_pdf_data = parse_order_text(pdf_text)

            st.success("PDF parsed. Review/edit fields before creating load.")
            st.write("Parsed PDF data:", parsed_pdf_data)

        except Exception as exc:
            st.warning(f"PDF could not be parsed. You can still enter the load manually. Error: {exc}")
        st.markdown("### Pull Load From Email")

        if st.button("Check Recent Load Emails"):
            st.session_state["load_emails"] = fetch_recent_load_emails(limit=10)

        if "load_emails" in st.session_state:
            email_options = [
                f"{i + 1}. {item['subject']} — {item['from']}"
                for i, item in enumerate(st.session_state["load_emails"])
            ]

            selected_email_label = st.selectbox(
                "Select Email",
                [""] + email_options,
                key="selected_load_email",
            )

            if selected_email_label:
                selected_index = email_options.index(selected_email_label)
                selected_email = st.session_state["load_emails"][selected_index]

                st.write("Subject:", selected_email["subject"])
                st.write("From:", selected_email["from"])

                if st.button("Parse Selected Email"):
                    parsed_email_data = parse_email_text(
                        selected_email["subject"],
                        selected_email["body"],
                    )

                    st.session_state["parsed_email_data"] = parsed_email_data

                    st.success("Email parsed. Review/edit fields below.")
                    st.write("Parsed Email Data:", parsed_email_data)

    if "parsed_email_data" in st.session_state:
        parsed_pdf_data.update(st.session_state["parsed_email_data"])

        with st.form("new_load_form", clear_on_submit=True):
            load_type_options = ["OTR Import", "OTR Export", "OTR Local imports"]
            parsed_type = parsed_pdf_data.get("TYPE", "OTR Import")

            load_type = st.selectbox(
                "TYPE",
                load_type_options,
                index=load_type_options.index(parsed_type) if parsed_type in load_type_options else 0,
                key="new_load_type",
            )

            date_value = parsed_pdf_data.get("Date") or date.today()

            date = st.date_input(
                "Date",
                value=pd.to_datetime(date_value, errors="coerce").date()
                if pd.notna(pd.to_datetime(date_value, errors="coerce"))
                else date.today(),
                key="new_load_date",
            )

            customer_options = sorted(
                customer_df["Customer Name"]
                .dropna()
                .astype(str)
                .str.strip()
                .unique()
            )

        parsed_customer = parsed_pdf_data.get("Customer", "")
        if parsed_customer and parsed_customer not in customer_options:
            customer_options = [parsed_customer] + customer_options

        customer = st.selectbox(
            "Customer",
            [""] + customer_options,
            index=([""] + customer_options).index(parsed_customer) if parsed_customer in customer_options else 0,
            key="new_load_customer",
        )

        warehouse_options = sorted(
            warehouse_df["Warehouse"]
            .dropna()
            .astype(str)
            .str.strip()
            .unique()
        )

        parsed_warehouse = parsed_pdf_data.get("Warehouse", "")
        if parsed_warehouse and parsed_warehouse not in warehouse_options:
            warehouse_options = [parsed_warehouse] + warehouse_options

        warehouse = st.selectbox(
            "Warehouse",
            [""] + warehouse_options,
            index=([""] + warehouse_options).index(parsed_warehouse) if parsed_warehouse in warehouse_options else 0,
            key="new_load_warehouse",
        )

        if warehouse:
            selected_warehouse = warehouse_df[
                warehouse_df["Warehouse"].astype(str).str.strip() == warehouse
            ]

            if not selected_warehouse.empty:
                warehouse_row = selected_warehouse.iloc[0]

                warehouse_address = warehouse_row.get("Address", "")
                warehouse_state = warehouse_row.get("State", "")
                warehouse_zip = warehouse_row.get("Zip", "")
                warehouse_contact = warehouse_row.get("Contact Name", "")
                warehouse_email = warehouse_row.get("Contact Email", "")
                warehouse_phone = warehouse_row.get("Contact Phone", "")
                warehouse_notes = warehouse_row.get("Warehouse Notes", "")

                st.caption(
                    f"Address: {warehouse_address}, {warehouse_state} {warehouse_zip} | "
                    f"Contact: {warehouse_contact} | Email: {warehouse_email} | "
                    f"Phone: {warehouse_phone} | Notes: {warehouse_notes}"
                )

        booking_number = st.text_input(
            "Booking Number",
            value=parsed_pdf_data.get("Booking Number", ""),
            key="new_load_booking_number",
        )

        reference_number = st.text_input(
            "Reference Number",
            value=parsed_pdf_data.get("Reference Number", ""),
            key="new_load_reference_number",
        )

        container_number = st.text_input(
            "Container Number",
            value=parsed_pdf_data.get("Container Number", ""),
            key="new_load_container_number",
        )

        chassis = st.text_input(
            "Chassis",
            value=parsed_pdf_data.get("Chassis", ""),
            key="new_load_chassis",
        )

        port = st.text_input(
            "Port",
            value=parsed_pdf_data.get("Port", ""),
            key="new_load_port",
        )

        document_cutoff_value = parsed_pdf_data.get("Document Cutoff") or date.today()

        document_cutoff = st.date_input(
            "Document Cutoff",
            value=pd.to_datetime(document_cutoff_value, errors="coerce").date()
            if pd.notna(pd.to_datetime(document_cutoff_value, errors="coerce"))
            else date.today(),
            key="new_load_document_cutoff",
        )

        delivery_need_date_value = parsed_pdf_data.get("Delivery Need Date") or date.today()

        delivery_need_date = st.date_input(
            "Delivery Need Date",
            value=pd.to_datetime(delivery_need_date_value, errors="coerce").date()
            if pd.notna(pd.to_datetime(delivery_need_date_value, errors="coerce"))
            else date.today(),
            key="new_load_delivery_need_date",
        )

        notes = st.text_area(
            "Dispatcher Notes",
            value=parsed_pdf_data.get("Dispatcher Notes", ""),
            key="new_load_dispatcher_notes",
        )

        submitted = st.form_submit_button("Create Load")
            
        if submitted:
            new_row = {
                "TYPE": load_type,
                "Date": str(date),
                "Customer": customer,
                "Booking Number": booking_number,
                "Reference Number": reference_number,
                "Container Number": container_number,
                "Chassis": chassis,
                "Port": port,
                "Warehouse": warehouse,
                "Document Cutoff": str(document_cutoff),
                "Delivery Need Date": str(delivery_need_date),
                "Status": "New",
                "Dispatcher Notes": notes,
            }

            client = DispatchSmartsheetClient()
            created_row = client.add_row(new_row)

            st.success(f"Load created. Row ID: {created_row.id}")
            st.write("New row data:", new_row)

            if uploaded_file is not None:
                client.attach_file_to_row(created_row.id, uploaded_file)

            refresh_data()
            st.rerun()

def safe_selectbox_value(options, parsed_value):
    parsed_value = str(parsed_value or "").strip()

    if parsed_value and parsed_value not in options:
        options = [parsed_value] + options

    return options, 0 if parsed_value else 0

with tabs[1]:
    st.subheader("📋 Load Board")

    col1, col2, col3 = st.columns([3, 2, 1])

    with col1:
        search_text = st.text_input(
            "Search Load Board",
            placeholder="Search Booking #, Customer, Warehouse, Status...",
            key="load_board_search",
        )

    with col2:
        status_filter = st.selectbox(
            "Status",
            ["All"] + otr_status_options,
            key="load_board_status",
        )

    with col3:
        st.write("")
        st.write("")
        st.button("🔍 Search", key="load_board_search_btn")
    st.session_state["current_tab"] = "load_board"
    filtered_df = filter_table(df, search_text, status_filter)
    show_booking_summary(filtered_df, "Load Board")


with tabs[2]:
    st.subheader("🚛 OTR Imports")

    col1, col2, col3 = st.columns([3, 2, 1])

    with col1:
        search_text = st.text_input(
            "Search OTR Imports",
            placeholder="Search Booking #, Customer, Warehouse...",
            key="otr_import_search",
        )

    with col2:
        status_filter = st.selectbox(
            "Status",
            ["All"] + otr_status_options,
            key="otr_import_status",
        )

    with col3:
        st.write("")
        st.write("")
        st.button("🔍 Search", key="otr_import_search_btn")
    st.session_state["current_tab"] = "otr_imports"
    otr_import_df = get_type_rows(df, "OTR Import")
    filtered_df = filter_table(otr_import_df, search_text, status_filter)
    show_booking_summary(filtered_df, "OTR Imports")


with tabs[3]:
    st.subheader("🚛 OTR Exports")

    col1, col2, col3 = st.columns([3, 2, 1])

    with col1:
        search_text = st.text_input(
            "Search OTR Exports",
            placeholder="Search Booking #, Customer, Warehouse...",
            key="otr_export_search",
        )

    with col2:
        status_filter = st.selectbox(
            "Status",
            ["All"] + otr_status_options,
            key="otr_export_status",
        )

    with col3:
        st.write("")
        st.write("")
        st.button("🔍 Search", key="otr_export_search_btn")
    st.session_state["current_tab"] = "otr_exports"
    otr_export_df = get_type_rows(df, "OTR Export")
    filtered_df = filter_table(otr_export_df, search_text, status_filter)
    show_booking_summary(filtered_df, "OTR Exports")


with tabs[4]:
    st.subheader("🚛 OTR Local Imports")

    col1, col2, col3 = st.columns([3, 2, 1])

    with col1:
        search_text = st.text_input(
            "Search OTR Local Imports",
            placeholder="Search Booking #, Customer, Warehouse...",
            key="otr_local_import_search",
        )

    with col2:
        status_filter = st.selectbox(
            "Status",
            ["All"] + otr_status_options,
            key="otr_local_import_status",
        )

    with col3:
        st.write("")
        st.write("")
        st.button("🔍 Search", key="otr_local_import_search_btn")
    st.session_state["current_tab"] = "otr_local_imports"
    otr_local_import_df = get_type_rows(df, "OTR Local Import")
    filtered_df = filter_table(otr_local_import_df, search_text, status_filter)
    show_booking_summary(filtered_df, "OTR Local Imports")


with tabs[5]:
    st.subheader("📋 Files to Export")

    export_df = df[df["Status"] == "Ready for ProfitTools"].copy()

    show_booking_summary(export_df, "Files to Export")


st.markdown(
    """
    <div class="footer-band">
    🇨🇴 Orgullosamente Colombianos · Calitrans Dispatch Center
    </div>
    """,
    unsafe_allow_html=True,
)