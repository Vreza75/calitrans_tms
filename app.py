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


def main() -> None:
    load_css()
    show_header()
    route_selected_page()


if __name__ == "__main__":
    main()
