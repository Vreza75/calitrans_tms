"""Framework-neutral application layer (Phase 1 backend boundary).

Modules under `application/` must not import streamlit, touch
st.session_state, use st.cache_data, render UI, or perform IMAP/AI/PDF
work during a read. Streamlit pages and FastAPI routers both call into
these same services instead of maintaining separate implementations.

Legacy service modules under `services/` are heavier (many still import
streamlit for rendering) - where an application module needs to reuse
their business logic, it does so via a lazy `import` inside a function
body, never at module top level, so importing `application.*` itself
never pulls in streamlit.
"""
