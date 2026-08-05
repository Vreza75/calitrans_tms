"""Framework-neutral email message segmentation.

One canonical implementation of "what part of this email is the customer's
*current* active request, versus quoted reply history, versus a forwarded
block" - used by both field extraction (services/email_parser.py) and
classification (services/operations_inbox_service.py), so those two layers
can never disagree about which text is active.

No streamlit import, no database access, no external AI calls. Pure text in,
structured result out - safe to unit test in isolation.

Replaces the previous single-line "does this line look like a reply header"
heuristic (services/email_parser.py::_looks_like_reply_header), which
misclassified an active operational lane block ("From: Houston" / "To:
Dallas") as quoted-email history because two connected `Label:` lines were
treated as sufficient evidence on their own. A genuine reply/forward header
now requires either an email address in the From value, a Sent/Subject/Date
label nearby, or three or more connected metadata labels - and an
operational keyword (Equipment, Quote, Rate, Container, ...) nearby
overrides a weak header signal, since real freight lanes and email headers
never share that vocabulary.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

ScopeType = Literal["new_message", "reply", "forward", "forwarded_only", "unknown"]

_REPLY_HEADER_LABELS = {"from", "sent", "to", "cc", "bcc", "subject", "date"}
_SPANISH_REPLY_HEADER_LABELS = {"de", "enviado", "para", "asunto", "cc", "fecha"}
_SPANISH_TO_ENGLISH_LABEL = {
    "de": "from",
    "enviado": "sent",
    "para": "to",
    "asunto": "subject",
    "fecha": "date",
    "cc": "cc",
}

_EMAIL_IN_VALUE_RE = re.compile(r"[\w.+-]+@[\w.-]+\.\w+")
_OPERATIONAL_PROXIMITY_RE = re.compile(
    r"\b(?:equipment|quote|quoting|rate|container|port|pickup|delivery|size|"
    r"reference|booking|terminal|lfd|cutoff|shipment|cotiz\w*|tarifa)\b",
    re.I,
)
_HEADER_LABEL_LINE_RE = re.compile(r"^([A-Za-z][A-Za-zÀ-ÿ -]{1,18})\s*:\s*(.*)$")
_HEADER_LABEL_HITS_RE = re.compile(
    r"(?im)^\s*(?:from|sent|to|cc|bcc|subject|date|de|enviado|para|asunto|fecha)\s*:"
)
_SENT_SUBJECT_DATE_RE = re.compile(
    r"(?im)^\s*(?:sent|subject|date|enviado|asunto|fecha)\s*:"
)

_GMAIL_WROTE_RE = re.compile(r"^\s*On .{5,180}\bwrote:\s*$", re.I)
_SPANISH_WROTE_RE = re.compile(r"^\s*El .{5,180}\bescribi[oó]:\s*$", re.I)
_FORWARD_SEPARATOR_RE = re.compile(
    r"^\s*-{2,}\s*(?:Original Message|Mensaje\s+original|Forwarded\s+message)\s*-{2,}\s*$",
    re.I,
)
_UNDERSCORE_SEPARATOR_RE = re.compile(r"^\s*_{6,}\s*$")

_ADMINISTRATIVE_ONLY_RE = re.compile(
    r"^(?:fyi|please\s+handle|please\s+process|please\s+see\s+below|see\s+below|"
    r"forwarding(?:\s+for\s+(?:your\s+)?(?:review|action|handling))?|forwarded|"
    r"please\s+review|for\s+your\s+action|for\s+your\s+review|"
    r"para\s+su\s+atenci[oó]n|favor\s+revisar)[.:!,]*$",
    re.I,
)


@dataclass(frozen=True)
class MessageScope:
    raw_text: str
    active_text: str
    quoted_text: str
    forwarded_text: str
    classification_text: str
    scope_type: ScopeType
    confidence: float
    evidence: tuple[str, ...] = ()


def _label_key(label: str) -> str:
    return _SPANISH_TO_ENGLISH_LABEL.get(label, label)


def is_reply_header_line(lines: list[str], index: int) -> tuple[bool, str]:
    """Whether lines[index] is genuine reply/forward header metadata (not
    an active operational label line like "From: Houston"). Returns
    (is_header, normalized_label)."""
    line = lines[index].strip()
    match = _HEADER_LABEL_LINE_RE.match(line)
    if not match:
        return False, ""
    raw_label = match.group(1).strip().lower()
    value = match.group(2).strip()
    label = _label_key(raw_label)
    if raw_label not in _REPLY_HEADER_LABELS and raw_label not in _SPANISH_REPLY_HEADER_LABELS:
        return False, ""

    window_lines = lines[index : min(len(lines), index + 8)]
    window = "\n".join(window_lines)

    # A real header block is fundamentally characterized by a From:/De: line
    # (who sent it) - a bare "Date: 02-Jul-26" or "Subject: ..." operational
    # field with no From: anywhere nearby (e.g. a booking confirmation's own
    # "Date:" line) must never qualify on its own.
    has_from_label = bool(re.search(r"(?im)^\s*(?:from|de)\s*:", window))
    if not has_from_label:
        return False, label

    has_sent_subject_date = bool(_SENT_SUBJECT_DATE_RE.search(window))
    has_email_value = bool(_EMAIL_IN_VALUE_RE.search(value)) or bool(_EMAIL_IN_VALUE_RE.search(window))
    if has_sent_subject_date or has_email_value:
        return True, label

    if _OPERATIONAL_PROXIMITY_RE.search(window):
        return False, label

    header_hits = len(_HEADER_LABEL_HITS_RE.findall(window))
    return header_hits >= 3, label


def _find_reply_marker(lines: list[str]) -> int | None:
    for index, line in enumerate(lines):
        if _GMAIL_WROTE_RE.match(line) or _SPANISH_WROTE_RE.match(line):
            return index
        is_header, _ = is_reply_header_line(lines, index)
        if is_header:
            return index
    return None


def _find_forward_marker(lines: list[str]) -> int | None:
    for index, line in enumerate(lines):
        if _FORWARD_SEPARATOR_RE.match(line) or _UNDERSCORE_SEPARATOR_RE.match(line):
            return index
    return None


def _is_administrative_or_empty(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return True
    lines = [line.strip() for line in stripped.splitlines() if line.strip()]
    if not lines:
        return True
    return len(lines) <= 2 and all(_ADMINISTRATIVE_ONLY_RE.match(line) for line in lines)


def _forwarded_active_content(forwarded_block: str) -> tuple[str, str]:
    """Given text starting at a forward separator line, return
    (forwarded_active_text, remaining_forwarded_block). Skips the separator
    line and any genuine header block immediately following it, then clips
    at a nested reply marker if the forwarded message itself contains
    further quoted history."""
    lines = forwarded_block.splitlines()
    cursor = 1  # skip the separator line itself
    while cursor < len(lines):
        if lines[cursor].strip() == "":
            cursor += 1
            continue
        is_header, _ = is_reply_header_line(lines, cursor)
        if not is_header:
            break
        cursor += 1

    body_lines = lines[cursor:]
    nested_reply_index = _find_reply_marker(body_lines)
    if nested_reply_index is not None:
        body_lines = body_lines[:nested_reply_index]

    return "\n".join(body_lines).strip(), forwarded_block


def build_message_scope(raw_text: str) -> MessageScope:
    """Segment raw email text into active/quoted/forwarded zones and select
    the single classification_text that intent scoring and detail detection
    must both use, so quote intent from old history can never combine with
    details from the active message (or vice versa)."""
    raw_text = str(raw_text or "")
    lines = raw_text.splitlines()

    reply_index = _find_reply_marker(lines)
    forward_index = _find_forward_marker(lines)

    if reply_index is not None and (forward_index is None or reply_index <= forward_index):
        active_text = "\n".join(lines[:reply_index]).strip()
        quoted_text = "\n".join(lines[reply_index:]).strip()
        return MessageScope(
            raw_text=raw_text,
            active_text=active_text,
            quoted_text=quoted_text,
            forwarded_text="",
            classification_text=active_text,
            scope_type="reply",
            confidence=0.9,
            evidence=(f"reply marker at line {reply_index}",),
        )

    if forward_index is not None:
        top_level = "\n".join(lines[:forward_index]).strip()
        forwarded_block = "\n".join(lines[forward_index:]).strip()
        forwarded_active, _ = _forwarded_active_content(forwarded_block)

        if _is_administrative_or_empty(top_level):
            return MessageScope(
                raw_text=raw_text,
                active_text=forwarded_active,
                quoted_text="",
                forwarded_text=forwarded_block,
                classification_text=forwarded_active,
                scope_type="forwarded_only",
                confidence=0.75,
                evidence=(
                    f"forward marker at line {forward_index}",
                    "top-level text is empty/administrative - using forwarded content",
                ),
            )

        return MessageScope(
            raw_text=raw_text,
            active_text=top_level,
            quoted_text="",
            forwarded_text=forwarded_block,
            classification_text=top_level,
            scope_type="forward",
            confidence=0.85,
            evidence=(
                f"forward marker at line {forward_index}",
                "top-level text has real content - forwarded content kept as supporting only",
            ),
        )

    stripped = raw_text.strip()
    return MessageScope(
        raw_text=raw_text,
        active_text=stripped,
        quoted_text="",
        forwarded_text="",
        classification_text=stripped,
        scope_type="new_message",
        confidence=1.0,
        evidence=("no reply/forward marker found",),
    )
