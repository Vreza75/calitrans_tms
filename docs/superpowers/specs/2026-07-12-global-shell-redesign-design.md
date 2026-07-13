# Global Shell & Design Token Redesign

## Context

CaliTrans TMS is a Streamlit app with a custom CaliTrans-branded theme
(`theme.css` + inline styles in `ui_components/app_shell.py`). The user wants
to modernize the app's visual design using the `ui-ux-pro-max` skill,
starting with the global shell — the elements every page inherits — rather
than any single page's layout.

Current state, verified by reading the code:

- `theme.css` (290 lines) and `ui_components/app_shell.py`'s `load_css()`
  (~200 lines of inline `<style>`) both exist and **duplicate** several
  rules — `[data-testid="stAppViewContainer"]` background and
  `.block-container` max-width/padding are each declared in both places.
- Colors are hardcoded hex values scattered across both files and repeated
  ad hoc in several `ui_components/*.py` files (e.g. `.status-pill`,
  `.danger-pill`, `.success-pill` in `app_shell.py`; inline `style=` spans in
  `status_legend.py`).
- Spacing/radius values are inconsistent: border-radius appears as 8px,
  14px, and 16px in different rules with no evident system.
- A real load-status system already exists —
  `services/dispatch_workflow_service.STATUS_COLORS` /
  `STATUS_MEANINGS` — grouped into six lifecycle stages (Intake/Verification,
  Ready/Active, Pickup/Loading, Delivered/Return, Issues/Stops,
  Billing/Closed) and rendered today via ad hoc inline HTML in
  `ui_components/status_legend.py`. This is the "logistics status language"
  the redesign should surface more prominently, not replace.
- `ui_components/ops_metric_card.py` renders metric tiles via a
  `.ops-metric-card` class already defined in `app_shell.py`.

## Decisions (from brainstorming)

- **Scope**: global shell only — `theme.css`, `ui_components/app_shell.py`
  (header, sidebar/nav, color system, typography), and the shared component
  classes (`.metric-card`, `.load-card`, `.status-pill` family, tabs,
  buttons, dataframes, expanders). Page-specific layouts (Dispatch Board,
  Orders Management, Booking Detail, etc.) are explicitly **not** touched —
  they inherit the new tokens automatically since they already use these
  shared classes.
- **Colors**: keep the existing CaliTrans brand palette (navy `#003B8E`,
  navy-dark `#061B3A`, yellow `#FFD200`, red `#CE1126`, green `#16803C`,
  orange `#F97316`). Refine *usage* (hierarchy, restraint), not the palette
  itself.
- **Style**: clean & minimal SaaS-dashboard aesthetic — generous whitespace,
  subtle shadows/borders, flat surfaces.
- **Product identity**: should read as a Transportation Management System,
  specifically via *logistics status language* — status pills/badges for
  load lifecycle states, using the existing `STATUS_COLORS`/`STATUS_MEANINGS`
  as source of truth, plus route/booking-oriented nav grouping.
- **Approach**: design tokens (3-layer: primitive → semantic → component) as
  CSS custom properties, replacing scattered hardcoded hex values, combined
  with a CSS refresh. No structural rebuild of `show_header()`/
  `render_sidebar()` markup — light polish on top of the existing structure.
- **Motivation**: modernize on principle; no specific usability complaint to
  fix.

## Design

### 1. Design tokens (new `theme.css`)

Three layers, all as CSS custom properties on `:root`:

**Primitive** — raw values, named by what they *are*:
```
--ct-navy-900, --ct-blue-700, --ct-yellow-500, --ct-red-600, --ct-green-700,
--ct-orange-600
--ct-gray-50 … --ct-gray-900   (new — a real neutral scale; today there is
                                 exactly one gray, #f6f8fb)
```

**Semantic** — what things *mean*, built from primitives:
```
--surface-page       (gray-50)
--surface-card       (white)
--text-primary        (gray-900)
--text-muted          (gray-500)
--border-default      (gray-200-ish, matches current #d8e0ec family)
--action-primary       (navy-700)   — primary buttons, active nav, headers
--action-accent         (yellow-500) — reserved for highlights/active-tab
                                        state only, not general decoration
--status-active         (maps to STATUS_COLORS "Ready/Active" group)
--status-warning        (maps to "Issues/Stops" group)
--status-done            (maps to "Delivered/Return" + "Billing/Closed")
--status-neutral          (maps to "Intake/Verification")
```
Status semantic tokens are a *display layer* over the existing
`STATUS_COLORS` dict — that dict stays the single source of truth for
per-status color; the CSS tokens define how a status pill looks
structurally (padding, radius, weight), not which color a given status is.

**Component** — spacing/radius/type scale used everywhere:
```
--space-1 (4px) … --space-6 (24px)
--radius-sm (8px), --radius-md (12px)   — collapses today's 8/14/16px mix
--font-size-label (0.72rem), --font-size-body (0.86rem),
--font-size-title (1.35rem), --font-size-metric (1.45rem)
```

`theme.css` becomes the single source of these rules. The duplicated block
in `app_shell.py`'s `load_css()` is removed — `load_css()` only reads and
injects `theme.css`; app-shell-specific overrides that don't belong in the
shared stylesheet (if any remain) remain inline, not duplicated content.

### 2. Header & sidebar polish

`show_header()` and `render_sidebar()` keep their current function
signatures and structure (banner image, `st.title`, `st.caption`; sidebar
logo, `st.radio` nav, admin selectbox, refresh button, status legend). Only
the CSS driving them changes:

- Header: type scale applied to title/caption, tighter vertical rhythm,
  banner treatment cleaned up (consistent border-radius, shadow using new
  tokens).
- Sidebar: nav radio items get a defined active/hover state (today: browser
  default radio styling with no custom treatment), consistent spacing
  between logo / nav / divider / refresh button / status legend using the
  new spacing scale.

### 3. Logistics status badge system

New `ui_components/status_badge.py`:
```python
def render_status_badge(status: str) -> str:
    """Return pill-styled HTML for a load/case status.

    Color comes from services.dispatch_workflow_service.STATUS_COLORS —
    this function only supplies consistent pill structure (padding, radius,
    weight, contrast-safe text color), not the color-to-status mapping.
    """
```
- Replaces the ad hoc inline `<span style=...>` blocks in
  `ui_components/status_legend.py` and formalizes `.status-pill` /
  `.danger-pill` / `.success-pill` (currently only 3 fixed variants) into
  one component driven by the full `STATUS_COLORS` dict, so every status
  gets consistent pill treatment, not just three hardcoded ones.
- `status_legend.py` is updated to call `render_status_badge()` instead of
  building inline HTML directly — reduces duplication, doesn't change its
  public behavior (`render_status_legend()` signature unchanged).

### 4. Shared component restyle

Using the new tokens, restyle (structure/markup unchanged, only CSS):
metric cards (`.metric-card`, `.ops-metric-card`), load cards (`.load-card`),
`st.button`, `st.tabs`, `st.expander`, `st.dataframe`, `st.metric`.

## Testing

No new automated tests — this is CSS/visual work with no logic to unit
test. Verification is: run the app (`streamlit run app.py`), visually check
the shell (header, sidebar, nav active states) and at least one page that
uses each restyled shared component (a status badge, a metric card, a load
card, a tab set) to confirm nothing regressed and the new tokens render as
intended. `python -m compileall` covers syntax correctness for the one new
Python file (`status_badge.py`) and the edited `status_legend.py`/
`app_shell.py`.

## Out of scope

- Any page-specific layout change (Dispatch Board, Orders Management,
  Booking Detail, Dashboard, etc.) — they inherit new tokens automatically
  but their own structure/layout is untouched this batch.
- Changing the CaliTrans brand color palette itself.
- Dark mode.
- Any backend/service logic change.
