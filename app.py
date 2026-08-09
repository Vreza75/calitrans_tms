from __future__ import annotations

from ui_components.app_shell import (
    configure_streamlit_page,
    load_css,
    load_local_env_file,
    show_header,
)

load_local_env_file()
configure_streamlit_page()

from pages_app.router import route_selected_page
from ui_components.auth_gate import render_logout_control, require_login


def main() -> None:
    principal = require_login()
    load_css()
    show_header()
    render_logout_control()
    route_selected_page(principal)


if __name__ == "__main__":
    main()
