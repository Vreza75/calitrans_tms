from pathlib import Path
import base64
import pandas as pd
import streamlit as st

from smartsheet_client import DispatchSmartsheetClient
from validators import validate_dispatch_rows
from profittools_export import export_ready_loads


st.set_page_config(
    page_title="Calitrans Dispatch Center",
    page_icon="🚚",
    layout="wide",
)


def load_css():
    st.markdown(Path("theme.css").read_text(encoding="utf-8"), unsafe_allow_html=True)


def image_to_base64(path: str) -> str:
    file_path = Path(path)
    if not file_path.exists():
        return ""
    return base64.b64encode(file_path.read_bytes()).decode("utf-8")


load_css()


@st.cache_data(ttl=60)
def load_dispatch_data() -> pd.DataFrame:
    client = DispatchSmartsheetClient()
    sheet = client.get_sheet()
    return client.rows_to_dataframe(sheet)


def refresh_data():
    st.cache_data.clear()


logo_b64 = image_to_base64("assets/calitrans_logo.png")
logo_html = (
    f'<img class="brand-logo" src="data:image/png;base64,{logo_b64}" />'
    if logo_b64
    else '<div class="brand-logo"><b>CALITRANS</b></div>'
)

st.markdown(f"""
<div class="hero">
  <div class="hero-inner">
    <div class="brand-row">
      {logo_html}
    </div>
    <h1>🚚 Calitrans Dispatch Center</h1>
    <p>Live TMS-style dispatch board powered by Smartsheet</p>
    <div class="hero-slogan">Movemos su carga,<br>impulsamos a Colombia.</div>
  </div>
</div>
""", unsafe_allow_html=True)


try:
    df = load_dispatch_data()
except Exception as exc:
    st.error(f"Could not load Smartsheet data: {exc}")
    st.stop()

if df.empty:
    st.warning("No rows found in the dispatch sheet.")
    st.stop()

required_columns = [
    "_row_id", "Load ID", "Customer", "Pickup", "Delivery",
    "Status", "Driver", "Truck", "Dispatcher Notes"
]
for col in required_columns:
    if col not in df.columns:
        df[col] = None


active_statuses = ["Ready to Dispatch", "Assigned", "En Route to Pickup", "Hold/Need Info"]
active_count = len(df[df["Status"].isin(active_statuses)])
ready_count = len(df[df["Status"] == "Ready to Dispatch"])
assigned_count = len(df[df["Status"] == "Assigned"])
hold_count = len(df[df["Status"] == "Hold/Need Info"])
exported_count = len(df[df["Status"] == "Exported to ProfitTools"])


def kpi_card(icon, label, value, sub, css_class):
    st.markdown(f"""
    <div class="kpi-card">
      <div class="kpi-icon {css_class}">{icon}</div>
      <div>
        <div class="kpi-label {css_class}">{label}</div>
        <div class="kpi-value">{value}</div>
        <div class="kpi-sub">{sub}</div>
      </div>
    </div>
    """, unsafe_allow_html=True)


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

display_columns = [
    "_row_id",
    "Load ID",
    "Customer",
    "Pickup",
    "Delivery",
    "Status",
    "Driver",
    "Truck",
    "Dispatcher Notes",
]
display_columns = [col for col in display_columns if col in df.columns]


def show_load_table(data: pd.DataFrame, title: str):
    st.markdown(f'<div class="section-title">{title}</div>', unsafe_allow_html=True)

    editable_columns = [
        "Status",
        "Driver",
        "Truck",
        "Dispatcher Notes",
    ]

    driver_options = [""] + sorted([str(x) for x in df["Driver"].dropna().unique() if str(x).strip()])

    column_config = {
        "_row_id": st.column_config.TextColumn("_row_id", disabled=True),
        "Load ID": st.column_config.TextColumn("Load ID", disabled=True),
        "Customer": st.column_config.TextColumn("Customer", disabled=True),
        "Pickup": st.column_config.TextColumn("Pickup", disabled=True),
        "Delivery": st.column_config.TextColumn("Delivery", disabled=True),
        "Status": st.column_config.SelectboxColumn(
            "Status",
            options=[
                "Ready to Dispatch",
                "Assigned",
                "En Route to Pickup",
                "Hold/Need Info",
                "Cancelled",
                "Exported to ProfitTools",
            ],
        ),
        "Driver": st.column_config.SelectboxColumn("Driver", options=driver_options),
        "Truck": st.column_config.TextColumn("Truck"),
        "Dispatcher Notes": st.column_config.TextColumn("Dispatcher Notes"),
    }

    edited_data = st.data_editor(
        data[display_columns],
        use_container_width=True,
        hide_index=True,
        disabled=[col for col in display_columns if col not in editable_columns],
        column_config=column_config,
        key=f"{title}_editor",
        height=390,
    )

    if st.button(f"💾 Save {title} Changes", key=f"{title}_save"):
        client = DispatchSmartsheetClient()
        changes_saved = 0

        original = data[display_columns].reset_index(drop=True)
        edited = edited_data.reset_index(drop=True)

        for i in range(len(edited)):
            row_id = int(edited.loc[i, "_row_id"])
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

        if changes_saved:
            st.success(f"Saved changes to {changes_saved} row(s).")
            refresh_data()
            st.rerun()
        else:
            st.info("No changes detected.")


tab_board, tab_ready, tab_problem, tab_export = st.tabs([
    "📋 Load Board",
    "✅ Ready to Dispatch",
    "⚠️ Problem Loads",
    "📤 ProfitTools Export",
])

with tab_board:
    show_load_table(df, "Load Board")

with tab_ready:
    show_load_table(df[df["Status"] == "Ready to Dispatch"], "Ready to Dispatch")

with tab_problem:
    show_load_table(
        df[df["Status"].isin(["Hold/Need Info", "Problem Load", "Cancelled"])],
        "Problem Loads",
    )

with tab_export:
    show_load_table(
        df[df["Status"] == "Exported to ProfitTools"],
        "ProfitTools Export",
    )

st.markdown("""
<div class="footer-band">
🇨🇴 Orgullosamente Colombianos · Calitrans Dispatch Center
</div>
""", unsafe_allow_html=True)