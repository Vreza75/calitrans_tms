from __future__ import annotations

import pandas as pd
import streamlit as st

from validators import validate_dispatch_rows


def render_validation(df: pd.DataFrame) -> None:
    """Render the dispatch/load validation page."""
    st.subheader("Validation")
    st.caption("Review loads that are missing required dispatch, driver, or ProfitTools information.")

    issues = validate_dispatch_rows(df)

    if issues.empty:
        st.success("No validation issues found.")
        return

    st.warning(f"{len(issues)} validation issue(s) found.")
    st.dataframe(issues, use_container_width=True, hide_index=True)

    csv_data = issues.to_csv(index=False).encode("utf-8")
    st.download_button(
        "Download validation issues CSV",
        data=csv_data,
        file_name="calitrans_validation_issues.csv",
        mime="text/csv",
    )
