# Global Shell & Design Token Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Modernize CaliTrans TMS's global shell (theme, header, sidebar, shared components) with a proper design-token system, while keeping the CaliTrans brand palette and formalizing the existing load-status colors into a reusable badge component.

**Architecture:** All visual changes live in CSS custom properties defined once in `theme.css` (3 layers: primitive → semantic → component tokens), consumed by `ui_components/app_shell.py` (header/sidebar/shared component classes) and a new `ui_components/status_badge.py` component. No page-specific layout files are touched — they inherit the new tokens automatically because they already use the shared CSS classes.

**Tech Stack:** Streamlit (Python), CSS injected via `st.markdown(..., unsafe_allow_html=True)`. No new dependencies.

## Global Constraints

- Keep the existing CaliTrans brand colors — navy `#061B3A`, blue `#003B8E`, yellow `#FFD200`, red `#CE1126`, green `#16803C`, orange `#F97316`. Do not introduce a different palette.
- Do not change the color-to-status mapping in `services/dispatch_workflow_service.STATUS_COLORS` — it stays the single source of truth for which color a given load status gets.
- Do not change any Python function signature that other files call (`load_css()`, `show_header()`, `render_sidebar()`, `render_status_legend()`, `render_ops_metric_card()`) — only their internals/styling.
- No page-specific layout files (`pages_app/*.py`) are touched in this plan.
- Verification is visual (no CSS unit-testing framework in this project) — after each task, run `python -m compileall` on changed files and confirm the running `streamlit run app.py` instance shows no new traceback in its log.

---

### Task 1: Design tokens — rewrite `theme.css`, remove duplication from `app_shell.py`

**Files:**
- Modify: `theme.css` (full rewrite)
- Modify: `ui_components/app_shell.py:81-275` (`load_css()` — remove duplicated rules now defined by tokens; keep only rules that don't belong in the shared stylesheet, if any)

**Interfaces:**
- Produces: CSS custom properties on `:root` — `--ct-navy-900`, `--ct-blue-700`, `--ct-blue-600`, `--ct-yellow-500`, `--ct-red-600`, `--ct-green-700`, `--ct-orange-600`, `--ct-gray-50/100/200/300/500/700/900`, `--surface-page`, `--surface-card`, `--text-primary`, `--text-muted`, `--border-default`, `--action-primary`, `--action-primary-hover`, `--action-accent`, `--space-1..6`, `--radius-sm`, `--radius-md`, `--shadow-card`, `--font-size-label/body/title/metric`. All later tasks consume these — do not rename them.

- [ ] **Step 1: Write the new `theme.css`**

Replace the full contents of `theme.css` with:

```css
<style>
:root {
    /* Primitive tokens — raw brand + neutral values */
    --ct-navy-900: #061B3A;
    --ct-blue-700: #003B8E;
    --ct-blue-600: #0B4FA8;
    --ct-yellow-500: #FFD200;
    --ct-red-600: #CE1126;
    --ct-green-700: #16803C;
    --ct-orange-600: #F97316;

    --ct-gray-50: #F6F8FB;
    --ct-gray-100: #EEF2F8;
    --ct-gray-200: #D8E0EC;
    --ct-gray-300: #C3CEDE;
    --ct-gray-500: #64748B;
    --ct-gray-700: #334155;
    --ct-gray-900: #0F172A;

    /* Semantic tokens — what things mean */
    --surface-page: var(--ct-gray-50);
    --surface-card: #FFFFFF;
    --text-primary: var(--ct-gray-900);
    --text-muted: var(--ct-gray-500);
    --border-default: var(--ct-gray-200);
    --action-primary: var(--ct-blue-700);
    --action-primary-hover: var(--ct-navy-900);
    --action-accent: var(--ct-yellow-500);

    /* Component tokens — spacing, radius, type scale */
    --space-1: 4px;
    --space-2: 8px;
    --space-3: 12px;
    --space-4: 16px;
    --space-5: 20px;
    --space-6: 24px;
    --radius-sm: 8px;
    --radius-md: 12px;
    --shadow-card: 0 4px 16px rgba(15, 23, 42, 0.06);

    --font-size-label: 0.72rem;
    --font-size-body: 0.86rem;
    --font-size-title: 1.35rem;
    --font-size-metric: 1.45rem;
}

html, body, [class*="css"] {
    font-family: "Inter", "Segoe UI", Arial, sans-serif;
}

[data-testid="stAppViewContainer"] {
    background: var(--surface-page);
}

.block-container {
    padding-top: var(--space-3);
    padding-bottom: var(--space-6);
    max-width: 1320px;
}

#MainMenu {visibility: hidden;}
footer {visibility: hidden;}

h1, h2, h3 {
    letter-spacing: 0;
    color: var(--text-primary);
}
h2, h3 {
    font-weight: 750;
}

.hero {
    position: relative;
    overflow: hidden;
    border-radius: var(--radius-sm);
    margin-bottom: var(--space-4);
    min-height: 210px;
    background:
        linear-gradient(90deg, rgba(6,27,58,.98) 0%, rgba(0,59,142,.92) 45%, rgba(6,27,58,.78) 100%),
        url("https://images.unsplash.com/photo-1500530855697-b586d89ba3ee?auto=format&fit=crop&w=1800&q=80");
    background-size: cover;
    background-position: center;
    box-shadow: 0 8px 18px rgba(6, 27, 58, .14);
}

.banner-wrapper {
    border-radius: var(--radius-sm);
    overflow: hidden;
    margin-bottom: var(--space-4);
    box-shadow: var(--shadow-card);
}
.header-banner {
    width: 100%;
    display: block;
}

/* Metric / load cards */
.metric-card, .ops-metric-card {
    border: 1px solid var(--border-default);
    border-radius: var(--radius-md);
    padding: var(--space-4);
    background: var(--surface-card);
    box-shadow: var(--shadow-card);
}
.ops-metric-card {
    padding: var(--space-3) var(--space-4);
    min-height: 74px;
    border-radius: var(--radius-sm);
}
.ops-metric-label {
    color: var(--text-muted);
    font-size: var(--font-size-label);
    font-weight: 700;
    margin-bottom: var(--space-1);
}
.ops-metric-value {
    color: var(--text-primary);
    font-size: var(--font-size-metric);
    font-weight: 800;
    line-height: 1.05;
}
.ops-metric-sub {
    color: var(--text-muted);
    font-size: var(--font-size-label);
    margin-top: var(--space-1);
}

.load-card {
    border: 1px solid var(--border-default);
    border-radius: var(--radius-md);
    padding: var(--space-3);
    margin-bottom: var(--space-2);
    background: var(--surface-card);
    box-shadow: var(--shadow-card);
}
.load-card-title {
    font-weight: 700;
    font-size: 0.95rem;
    color: var(--text-primary);
}
.load-card-small {
    color: var(--text-muted);
    font-size: var(--font-size-label);
}

/* Status badge (see ui_components/status_badge.py) */
.ct-status-badge {
    display: inline-block;
    padding: 3px 10px;
    border-radius: 999px;
    font-size: var(--font-size-label);
    font-weight: 700;
    color: var(--ct-gray-900);
    border: 1px solid rgba(15, 23, 42, 0.12);
    white-space: nowrap;
}

/* Legacy fixed pills kept for existing call sites */
.status-pill {
    display: inline-block;
    padding: 3px 8px;
    border-radius: 999px;
    font-size: var(--font-size-label);
    background: #e0f2fe;
    color: #075985;
    font-weight: 700;
}
.danger-pill {
    display: inline-block;
    padding: 3px 8px;
    border-radius: 999px;
    font-size: var(--font-size-label);
    background: #fee2e2;
    color: #991b1b;
    font-weight: 700;
}
.success-pill {
    display: inline-block;
    padding: 3px 8px;
    border-radius: 999px;
    font-size: var(--font-size-label);
    background: #dcfce7;
    color: #166534;
    font-weight: 700;
}

/* Streamlit component overrides */
div[data-testid="stExpander"] {
    border: 1px solid var(--border-default);
    border-radius: var(--radius-sm);
    background: var(--surface-card);
    box-shadow: none;
}
div[data-testid="stMetric"] {
    background: var(--surface-card);
    border: 1px solid var(--border-default);
    border-radius: var(--radius-sm);
    padding: 10px 12px;
    box-shadow: none;
}
div[data-testid="stMetricLabel"] {
    font-size: var(--font-size-label);
    color: var(--text-muted);
    font-weight: 650;
}
div[data-testid="stMetricValue"] {
    font-size: 1.35rem;
    color: var(--text-primary);
    font-weight: 750;
}
.stButton > button {
    border-radius: var(--radius-sm) !important;
    min-height: 2.35rem;
    padding: 0.45rem 0.9rem;
    font-size: var(--font-size-body);
    font-weight: 700;
    box-shadow: none !important;
    background: var(--action-primary) !important;
    color: #FFFFFF !important;
    border: 1px solid var(--action-primary) !important;
}
.stButton > button:hover {
    background: var(--action-primary-hover) !important;
    border-color: var(--action-primary-hover) !important;
}
.stTabs [data-baseweb="tab-list"] {
    gap: var(--space-2) !important;
    border-bottom: 1px solid var(--border-default) !important;
}
.stTabs [data-baseweb="tab"] {
    min-height: 38px !important;
    height: 38px !important;
    padding: 0 10px !important;
    border-radius: var(--radius-sm) var(--radius-sm) 0 0 !important;
    font-size: 0.78rem !important;
    font-weight: 650 !important;
    box-shadow: none !important;
}
.stTabs [aria-selected="true"] {
    background: #FFF8D7 !important;
    border-color: var(--action-accent) !important;
    border-bottom: 3px solid var(--action-accent) !important;
}
[data-testid="stDataFrame"] {
    border-radius: var(--radius-sm) !important;
    border: 1px solid var(--border-default) !important;
    box-shadow: none !important;
}

/* Sidebar nav */
section[data-testid="stSidebar"] .stRadio > div {
    gap: var(--space-1);
}
section[data-testid="stSidebar"] .stRadio label {
    border-radius: var(--radius-sm);
    padding: 6px var(--space-2) !important;
}
section[data-testid="stSidebar"] .stRadio label:hover {
    background: rgba(0, 59, 142, 0.06);
}

/* Operations Inbox page header (kept for pages_app/operations_inbox.py) */
.ops-header {
    margin: var(--space-1) 0 var(--space-4) 0;
    padding: 0;
}
.ops-kicker {
    color: var(--text-muted);
    font-size: var(--font-size-label);
    font-weight: 650;
    text-transform: uppercase;
    letter-spacing: 0;
    margin-bottom: var(--space-1);
}
.ops-title {
    color: var(--text-primary);
    font-size: var(--font-size-title);
    line-height: 1.2;
    font-weight: 800;
    margin: 0;
}
.ops-subtitle {
    color: var(--text-muted);
    font-size: var(--font-size-body);
    line-height: 1.45;
    margin-top: var(--space-2);
    max-width: 780px;
}
.ops-alert {
    border: 1px solid #CFE0F8;
    border-radius: var(--radius-sm);
    background: #EAF3FF;
    color: #064B91;
    padding: var(--space-3) var(--space-3);
    font-size: var(--font-size-body);
    line-height: 1.45;
}
</style>
```

- [ ] **Step 2: Remove the duplicated rules from `app_shell.py`'s `load_css()`**

In `ui_components/app_shell.py`, `load_css()` currently reads `theme.css` and then injects a second large inline `<style>` block containing the same rules (now all defined by Step 1's `theme.css`). Replace the function body so it only injects `theme.css`:

```python
def load_css() -> None:
    theme = Path("theme.css")
    if theme.exists():
        st.markdown(theme.read_text(encoding="utf-8"), unsafe_allow_html=True)
```

Remove the entire second `st.markdown("""<style>...</style>""", unsafe_allow_html=True)` call that followed it (previously lines ~86-275) — its content now lives in `theme.css` from Step 1.

- [ ] **Step 3: Verify**

Run:
```powershell
python -m compileall -q ui_components/app_shell.py
```
Expected: exit 0, no output.

Confirm the running `streamlit run app.py` instance's log shows no new traceback after this change (Streamlit auto-reloads on file save, or trigger a rerun in the browser).

- [ ] **Step 4: Commit**

```bash
git add theme.css ui_components/app_shell.py
git commit -m "Rewrite theme.css as a 3-layer design token system, remove duplicated shell CSS"
```

---

### Task 2: Status badge component

**Files:**
- Create: `ui_components/status_badge.py`
- Modify: `ui_components/status_legend.py`

**Interfaces:**
- Consumes: `services.dispatch_workflow_service.STATUS_COLORS: dict[str, str]`, `STATUS_MEANINGS: dict[str, str]` (both already exist, read-only).
- Consumes: `.ct-status-badge` CSS class from Task 1's `theme.css`.
- Produces: `render_status_badge(status: str) -> str` — returns an HTML string (does not call `st.markdown` itself, so callers can compose it inline with other HTML, matching how `status_legend.py` already builds composite HTML blocks).

- [ ] **Step 1: Create `ui_components/status_badge.py`**

```python
from __future__ import annotations

from html import escape

from services.dispatch_workflow_service import STATUS_COLORS

_DEFAULT_BADGE_COLOR = "#E5E7EB"


def render_status_badge(status: str) -> str:
    """Return pill-styled HTML for a load/case status.

    Color comes from services.dispatch_workflow_service.STATUS_COLORS — this
    function only supplies consistent pill structure (padding, radius,
    weight, border), not the color-to-status mapping. Unknown statuses fall
    back to a neutral gray rather than failing.
    """
    label = str(status or "").strip()
    if not label:
        return ""
    color = STATUS_COLORS.get(label, _DEFAULT_BADGE_COLOR)
    return (
        f'<span class="ct-status-badge" style="background:{escape(color)};">'
        f"{escape(label)}</span>"
    )
```

- [ ] **Step 2: Wire it into `status_legend.py`**

In `ui_components/status_legend.py`, replace the inline `<span style="...background:{color}...">` block inside `render_status_legend()`'s loop with a call to `render_status_badge()`, keeping the surrounding layout (meaning text next to it) the same:

```python
from ui_components.status_badge import render_status_badge


def render_status_legend() -> None:
    st.markdown("### Status Legend")
    st.caption("Dashboard row colors")

    for group_name, statuses in STATUS_LEGEND_GROUPS.items():
        st.markdown(f"**{group_name}**")
        for status in statuses:
            meaning = STATUS_MEANINGS.get(status, "")
            st.markdown(
                f"""
                <div style="display:flex; align-items:center; gap:8px; margin:6px 0 8px 0;">
                    {render_status_badge(status)}
                    <span style="font-size:12px; color:#64748b; line-height:1.2;">{meaning}</span>
                </div>
                """,
                unsafe_allow_html=True,
            )
        st.markdown("<hr style='margin:8px 0;'>", unsafe_allow_html=True)
```

Note this drops the old square color-swatch look in favor of the new pill badge showing the status name directly — meaning text moves to a plain caption beside it instead of being stacked under a bolded status name, since the badge itself now shows the status name.

- [ ] **Step 3: Verify**

Run:
```powershell
python -m compileall -q ui_components/status_badge.py ui_components/status_legend.py
```
Expected: exit 0.

Confirm no new traceback in the running app's log; visually check the Status Legend (visible on Active Status / Dispatch Board / Calendar View pages per `STATUS_LEGEND_SECTIONS` in `app_shell.py`) shows colored pills with status names.

- [ ] **Step 4: Commit**

```bash
git add ui_components/status_badge.py ui_components/status_legend.py
git commit -m "Add reusable status badge component, use it in the status legend"
```

---

### Task 3: Header & sidebar polish

**Files:**
- Modify: `ui_components/app_shell.py` (`show_header()`, `render_sidebar()`)

**Interfaces:**
- Consumes: tokens from Task 1's `theme.css` (already loaded globally by the time these render).
- No signature changes to `show_header()` / `render_sidebar()`.

- [ ] **Step 1: Confirm header/sidebar render correctly against the new tokens**

`show_header()` and `render_sidebar()`'s Python bodies are unchanged by this plan — Task 1's `theme.css` already defines `.banner-wrapper`, `.header-banner`, and the sidebar nav hover/active rules (`section[data-testid="stSidebar"] .stRadio ...`). This step is a verification-only checkpoint: run the app and visually confirm:
  - The header banner (if `assets/header_banner.png` exists) renders with the new rounded corners/shadow.
  - `st.title("CaliTrans TMS")` and the caption below it use the page's default heading styles from Task 1 (`h1` color/letter-spacing already set globally).
  - Sidebar nav radio options show a hover background when moused over.

If any of these don't look right, adjust the relevant rule in `theme.css` (not new Python) and re-verify — do not duplicate rules back into `app_shell.py`.

- [ ] **Step 2: Commit (only if `theme.css` needed further adjustment in Step 1)**

```bash
git add theme.css
git commit -m "Tune header/sidebar CSS after visual verification"
```

If no changes were needed, skip this commit — Task 1's commit already covers the styling.

---

### Task 4: Final full-app verification

**Files:** none (verification only)

- [ ] **Step 1: Full compile check**

```powershell
python -m compileall -q app.py pages_app services ui_components repositories database utils ai_agents ai_core
```
Expected: exit 0.

- [ ] **Step 2: Full test suite**

```powershell
python -m pytest -q
```
Expected: all existing tests still pass (this plan touches no tested logic, so the count should be unchanged from before this plan).

- [ ] **Step 3: Manual visual pass**

With `streamlit run app.py` running, check at least:
  - Operations Inbox (uses `.ops-header`, `.ops-metric-card`, `.load-card`)
  - Dispatch Board or Active Status (uses the status legend / status badges)
  - Any page with `st.tabs` and `st.expander` (confirm restyled tab/expander borders)

Confirm nothing regressed and the new token-driven look is applied consistently.
