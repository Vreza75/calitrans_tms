from __future__ import annotations

from fastapi import APIRouter, Query
from fastapi.responses import Response

from application.attachments.queries import get_attachment_bytes

router = APIRouter(prefix="/attachments", tags=["attachments"])


@router.get("/{attachment_ref}/content")
def get_attachment_content(attachment_ref: str, work_item_id: int = Query(...)) -> Response:
    """Reads attachment bytes - the only place in the API that touches
    attachment content. attachment_ref is an opaque token (see
    application/work_items/queries.py attachment_ref()), resolved back to
    a server file path internally; the path itself is never accepted from
    or returned to the client."""
    content, meta = get_attachment_bytes(work_item_id, attachment_ref)
    media_type = meta.content_type or "application/octet-stream"
    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f'inline; filename="{meta.filename}"'},
    )
