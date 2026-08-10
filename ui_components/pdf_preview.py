# ui_components/pdf_preview.py

from __future__ import annotations

import base64

import streamlit as st


@st.cache_data(show_spinner=False, ttl=900, max_entries=32)
def build_pdf_preview_html(file_bytes: bytes, *, height: int = 650) -> str:
    """Base64 data-URI iframe embed. Streamlit's native st.pdf needs the
    streamlit-pdf component package, which in turn needs a pyproject.toml
    asset_dir declaration this project doesn't have - this dependency-free
    embed works with the app as-is."""
    encoded = base64.b64encode(file_bytes).decode("utf-8")
    return (
        f'<iframe src="data:application/pdf;base64,{encoded}" '
        f'width="100%" height="{int(height)}" '
        'style="border:1px solid rgba(128,128,128,0.35);border-radius:8px;"></iframe>'
    )


def render_pdf_preview(file_bytes: bytes, *, key: str | None = None, height: int = 650) -> None:
    if not file_bytes:
        st.info("No PDF content is available to preview.")
        return
    st.markdown(build_pdf_preview_html(file_bytes, height=height), unsafe_allow_html=True)
