"""Regression tests for Issue 4 (transitional Streamlit UX pass): the app
shell must use Streamlit's wide layout, and theme.css must not re-narrow
it with a hardcoded pixel max-width. Source-inspection only - not a
pixel-perfect DOM/rendering test (theme.css is loaded via st.markdown as
a raw string, not something AppTest renders to real CSS)."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP_SHELL_SOURCE = (ROOT / "ui_components" / "app_shell.py").read_text(encoding="utf-8")
THEME_CSS = (ROOT / "theme.css").read_text(encoding="utf-8")


def test_app_shell_uses_wide_layout():
    assert 'layout="wide"' in APP_SHELL_SOURCE


def test_block_container_no_longer_hardcodes_a_narrow_pixel_max_width():
    assert "max-width: 1320px" not in THEME_CSS


def test_block_container_uses_percentage_width_with_flexible_gutters():
    block_container_start = THEME_CSS.index(".block-container {")
    block_container_end = THEME_CSS.index("}", block_container_start)
    block_container_rule = THEME_CSS[block_container_start:block_container_end]

    assert "max-width: 100%" in block_container_rule
    # Gutters are rem-based padding (scales with root font size), not a
    # fixed-pixel canvas width.
    assert "padding-left: 1.5rem" in block_container_rule
    assert "padding-right: 1.5rem" in block_container_rule
