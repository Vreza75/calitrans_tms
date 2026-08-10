# Document Review Panel Module

## Files

Place these into your project:

```text
ui_components/__init__.py
ui_components/document_review_panel.py
```

## Purpose

This panel replaces raw PDF/document JSON with a dispatcher-friendly comparison:

- Email body fields
- Document/PDF fields
- Final merged values
- Conflicts
- Parser confidence
- Warnings
- Approve / Needs Review / Reject actions

## Integration example

In `pages_app/operations_inbox.py`, import:

```python
from ui_components.document_review_panel import render_document_review_panel
```

Where you currently call:

```python
_render_operations_pdf_panel(...)
```

you can gradually replace or supplement it with:

```python
attachments = ops.extract_operations_attachments(parsed, record)  # if available

if attachments:
    selected_attachment = attachments[0]
    doc_review = render_document_review_panel(
        intake_id=int(selected_id),
        parsed=parsed,
        attachment=selected_attachment,
        email_parsed=parsed,
        document_parsed=selected_attachment.get("parsed_data", {}),
        expanded=True,
        allow_edit=True,
    )

    if doc_review["action"] == "save":
        updated = dict(parsed)
        updated.update(doc_review["final_values"])
        updated["_document_review"] = {
            "status": "approved",
            "conflicts": doc_review["conflicts"],
            "filename": selected_attachment.get("filename", ""),
        }
        ops.store_operations_parsed_data(
            int(selected_id),
            updated,
            action_required="Document fields approved by dispatcher.",
        )
        st.success("Document fields saved.")
        st.cache_data.clear()
        st.rerun()
```

## Recommended next step

After integration, move attachment helpers into:

```text
services/attachment_service.py
```

Then the document review panel can use a clean service instead of old `app.py` helper functions.
