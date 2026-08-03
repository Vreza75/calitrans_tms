from __future__ import annotations

from application.work_items.models import AttachmentMeta, AttachmentSummary
from application.work_items.queries import get_attachment_content, get_attachment_summary


def get_attachments_for_work_item(work_item_id: int) -> AttachmentSummary:
    """Metadata only (filename, path, content type) - never reads
    attachment bytes. Bytes are read only by the explicit content/preview
    path (Streamlit's lazy PDF panel, or GET
    /api/v1/attachments/{ref}/content)."""
    return get_attachment_summary(work_item_id)


def get_attachment_bytes(work_item_id: int, attachment_ref: str) -> tuple[bytes, AttachmentMeta]:
    """Read one attachment's bytes, resolved from its opaque ref rather
    than a client-supplied file path. Only called from the explicit
    content/download route."""
    return get_attachment_content(work_item_id, attachment_ref)
