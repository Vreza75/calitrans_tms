from __future__ import annotations

import streamlit as st

from db_client import read_df


def render_email_imports() -> None:
    """Render email import history from the email_imports table."""
    st.subheader("Email Imports / Diagnostics")
    st.caption("Admin view for mailbox troubleshooting, skipped messages, raw import metadata, and parser issues. Dispatchers should work daily email from Operations Inbox.")

    try:
        imports = read_df(
            """
            select
                gmail_message_id,
                subject,
                sender,
                received_at,
                pdf_filename,
                parsed_status,
                created_load_id,
                created_at
            from email_imports
            order by created_at desc
            """
        )

        st.dataframe(
            imports,
            use_container_width=True,
            hide_index=True,
        )

    except Exception as exc:
        st.error(f"Could not load email imports: {exc}")
