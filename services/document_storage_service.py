# services/document_storage_service.py

from __future__ import annotations

"""Phase 6B: staged, atomic file storage for document attachments.

Storage assumption (see docs/architecture/DOCUMENT_LIFECYCLE.md's
"Storage architecture" section for the full analysis - NOT assumed
durable, explicitly flagged): local disk, one directory
(config.DOCUMENT_STORAGE_DIR), a single filesystem. Path.rename is only
atomic when source and destination share a filesystem, which nesting the
staging subdirectory under the same storage root guarantees. Whether
that directory itself survives an app restart/redeploy on this
project's actual deployment target is NOT verified in this pass - see
the docs file.

Framework-neutral - no Streamlit import. Paired with
repositories/document_repo.py (the documents-row side) and the
'document.file.finalize' outbox event handler in
services/outbox_processor.py (the actual move-the-file side, run
asynchronously/retryably by the same outbox infrastructure Phase 6 built
for SMS).
"""

import hashlib
import time
import uuid
from pathlib import Path

from config import DOCUMENT_STORAGE_DIR

_STAGING_SUBDIR = ".staging"


def _storage_root() -> Path:
    root = Path(DOCUMENT_STORAGE_DIR)
    root.mkdir(parents=True, exist_ok=True)
    return root


def _staging_dir() -> Path:
    staging = _storage_root() / _STAGING_SUBDIR
    staging.mkdir(parents=True, exist_ok=True)
    return staging


def _sanitize_original_filename(raw_name: str) -> str:
    """Metadata only - never used to build a filesystem path on its own
    (see stage_upload's final_storage_key, which always prefixes a
    generated token first). Path(...).name already strips any leading
    directory component using forward-slash separators (recognized on
    both POSIX and Windows) - "../../secret.txt" -> "secret.txt",
    "folder/file.pdf" -> "file.pdf". This adds defense in depth against
    the residual edge cases .name doesn't fully normalize: a bare "."
    or ".." (POSIX may return ".." itself for input "..") and any
    backslash-containing name (a literal, inert character on POSIX, but
    rejected outright here rather than relied upon to stay inert)."""
    name = Path(raw_name or "").name
    if not name or name in {".", ".."} or "/" in name or "\\" in name:
        return "upload"
    return name


def stage_upload(uploaded_file, *, load_id: int) -> tuple[str, str, str, str]:
    """Write `uploaded_file`'s bytes to a staging path with a globally
    unique name - never the user-supplied filename, which is not a safe
    or collision-free storage identifier on its own (two uploads with
    the same original filename to the same load must not silently
    overwrite each other's stored bytes, which the pre-Phase-6B
    `load_{id}_{filename}` scheme could do).

    Returns (staging_relative_path, final_storage_key,
    sanitized_original_filename, checksum) - both path/key are relative
    to DOCUMENT_STORAGE_DIR (portable across a redeploy that changes the
    absolute root)."""
    original_filename = _sanitize_original_filename(uploaded_file.name)
    token = uuid.uuid4().hex

    uploaded_file.seek(0)
    data = uploaded_file.read()
    checksum = hashlib.sha256(data).hexdigest()

    staging_name = f"{token}.staging"
    staging_path = _staging_dir() / staging_name
    staging_path.write_bytes(data)

    final_storage_key = f"load_{load_id}_{token}_{original_filename}"

    return str(Path(_STAGING_SUBDIR) / staging_name), final_storage_key, original_filename, checksum


def _sha256_of_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def finalize(*, staging_relative_path: str, final_storage_key: str, expected_checksum: str) -> tuple[bool, str]:
    """Atomically move a staged file to its final path - the
    'document.file.finalize' outbox event handler
    (services/outbox_processor.py) calls this.

    Idempotent: if the final file already exists with a matching
    checksum, this is treated as already-succeeded rather than an error
    - a retry after a crash between the rename and the outbox result
    commit must not fail (the file really is there, correctly) or try to
    re-move a staged file that no longer exists (it was already moved).
    A final file existing with a DIFFERENT checksum is refused, not
    overwritten - that would silently corrupt an unrelated document that
    happens to occupy the same computed key (astronomically unlikely
    given the uuid4 token, but refusing beats overwriting either way).

    Returns (success, error) - error is "" on success."""
    root = _storage_root()
    staging_path = root / staging_relative_path
    final_path = root / final_storage_key

    if final_path.exists():
        if _sha256_of_file(final_path) == expected_checksum:
            return True, ""
        return False, "A file already exists at the final path with a different checksum - refusing to overwrite."

    if not staging_path.exists():
        return False, f"Staged file not found: {staging_relative_path}"

    staging_path.rename(final_path)
    return True, ""


def reclaim_orphaned_staging_files(*, older_than_hours: int = 24) -> int:
    """Sweep the staging directory for files older than the threshold -
    a document whose DB transaction never committed (so no outbox event
    exists to finalize it) leaves its staged file here forever otherwise.
    Not run automatically; an operator/cron invokes this deliberately,
    same pattern as repositories.outbox_repo.reclaim_stuck_processing.

    24h default is well beyond the outbox processor's own maximum
    retry-exhaustion time (5 attempts, capped 1h backoff each - a few
    hours worst case), so this does not race a finalize event that is
    still legitimately retrying."""
    cutoff = time.time() - older_than_hours * 3600
    removed = 0
    for path in _staging_dir().glob("*.staging"):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
                removed += 1
        except OSError:
            continue
    return removed
