"""Framework-neutral email message segmentation.

One canonical implementation of "what part of this email is the customer's
*current* active request, versus quoted reply history, versus a forwarded
block" - used by both field extraction (services/email_parser.py) and
classification (services/operations_inbox_service.py), so those two layers
can never disagree about which text is active.

No streamlit import, no database access, no external AI calls. Pure text in,
structured result out - safe to unit test in isolation.

## Design

Three prior patch cycles each fixed one observed email shape (a bare Date:
label, a Sent:/Cc:/Bcc: label, an email address) by adding an isolated
signal check, and each time a structurally adjacent shape (a coherent
From:/To:/Subject: envelope with none of those signals) reproduced the same
underlying defect in a different guise. This module replaces that pattern
with a single structural distinction, applied uniformly everywhere a
label-shaped block is evaluated:

- A contiguous run of label-shaped lines (blank-line tolerant) is scanned
  as one BLOCK, never line-by-line with a shrinking look-ahead window.
- A block is EMAIL ENVELOPE metadata when it has a From:/De: line and
  either an email address, or 2+ *other* envelope-shaped labels
  (Sent/To/Subject/Date/Cc/Bcc, or their Spanish equivalents) - never
  from a single label alone, and never from keyword content *inside* a
  label's own value (a Subject: line's value is metadata, not active body
  text - see _classify_label_line/_scan_label_block).
- A block containing an OPERATIONAL-ONLY field (Equipment, Container
  Number, Booking Number, Pickup/Delivery Date, Port, Terminal, Full
  Return, Empty Pickup, Reference Number, LFD, Cutoff - fields no email
  envelope ever carries) is always OPERATIONAL, regardless of how many
  envelope-shaped labels are also present in the same block.
- A bare From:+To: location pair with nothing else present is treated as
  plausible operational content by default (never classified as an
  envelope) - this was the original defect this module was created to
  fix, and the new design preserves it as a direct structural consequence
  (2 envelope-shaped labels alone is below the 3-label/email threshold)
  rather than as a separate special case.

See docs/reviews/OPERATIONS_INBOX_COHERENT_ENVELOPE_ROOT_CAUSE_FIX.md for
the full decision table this module implements.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

ScopeType = Literal["new_message", "reply", "forward", "forwarded_only", "unknown"]

# --- Envelope vs. operational label vocabulary ------------------------------

_ENVELOPE_LABELS = {"from", "sent", "to", "cc", "bcc", "subject", "date"}
_SPANISH_ENVELOPE_LABELS = {"de", "enviado", "para", "asunto", "fecha"}
_SPANISH_TO_ENGLISH_LABEL = {
    "de": "from",
    "enviado": "sent",
    "para": "to",
    "asunto": "subject",
    "fecha": "date",
}

# Fields that identify a block as an active transportation/operational
# request rather than email transport metadata - no email envelope ever
# carries a field labeled "Equipment:" or "Booking Number:". This
# vocabulary is only ever matched against a label-shaped LINE's own label
# (never against another label's *value*, e.g. never against what a
# Subject: line says) - see _classify_label_line.
_OPERATIONAL_ONLY_LABELS = {
    "equipment", "equipo",
    "container number", "container numbers", "numero de contenedor",
    "booking number", "numero de reserva", "numero de booking",
    "pickup date", "fecha de recogida", "fecha de recoleccion",
    "delivery date", "fecha de entrega",
    "port", "puerto",
    "terminal",
    "full return", "full return terminal", "retorno completo",
    "empty return", "empty return terminal",
    "empty pickup", "recogida vacia", "recogida vacía",
    "reference number", "numero de referencia",
    "lfd",
    "cutoff",
    "origin", "origen",
    "destination", "destino",
}

_LABEL_LINE_RE = re.compile(r"^([A-Za-zÀ-ÿ][A-Za-zÀ-ÿ /]{1,30})\s*:\s*(.*)$")
_EMAIL_IN_VALUE_RE = re.compile(r"[\w.+-]+@[\w.-]+\.\w+")

_GMAIL_WROTE_RE = re.compile(r"^\s*On .{5,180}\bwrote:\s*$", re.I)
_SPANISH_WROTE_RE = re.compile(r"^\s*El .{5,180}\bescribi[oó]:\s*$", re.I)
_FORWARD_SEPARATOR_RE = re.compile(
    r"^\s*-{2,}\s*(?:Original Message|Mensaje\s+original|Forwarded\s+message)\s*-{2,}\s*$",
    re.I,
)
_UNDERSCORE_SEPARATOR_RE = re.compile(r"^\s*_{6,}\s*$")

# Iteration bound for descending through nested forward wrappers (Phase 7) -
# guards against pathological/adversarial input with an unbounded chain of
# separators; no legitimate email chain nests this deep.
_MAX_FORWARD_NESTING_DEPTH = 5


SegmentationStatus = Literal["ok", "collapsed", "depth_limit_reached"]


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
    # "ok": either no reply/forward structure was found (new_message - an
    #   empty classification_text here only ever means the raw input itself
    #   was empty), or structure was found and produced real active text.
    # "collapsed": a reply/forward/forwarded_only structure was recognized
    #   but produced empty classification_text - callers must NEVER
    #   silently substitute the raw body as if it were active content in
    #   this case (Invariant 7); it means segmentation could not identify
    #   any authoritative active text, not that there is none to find.
    # "depth_limit_reached": nested-forward traversal hit
    #   _MAX_FORWARD_NESTING_DEPTH - text is still the best available
    #   non-empty candidate (never empty solely because of the limit, see
    #   select_innermost_actionable_forward), but callers should treat this
    #   as lower-confidence/needing review.
    segmentation_status: SegmentationStatus = "ok"


# --- Administrative wrapper normalization (Phase 8) -------------------------

_DASH_NORMALIZE_RE = re.compile(r"[‐-―−]")  # unicode dashes -> "-"
_WHITESPACE_COLLAPSE_RE = re.compile(r"[ \t]+")
_REPEATED_PUNCT_RE = re.compile(r"([.:!,-])\1+")

_ADMINISTRATIVE_ONLY_RE = re.compile(
    r"^(?:fyi(?:\s*[,-]?\s*thanks)?|please\s+handle|please\s+process|"
    r"please\s+see\s+below|see\s+below|"
    r"(?:please\s+)?see\s+attached(?:\s+below)?|fwd|"
    r"forwarding(?:\s+for\s+(?:your\s+)?(?:review|action|handling))?|forwarded|"
    r"please\s+review|for\s+your\s+action|for\s+your\s+review|"
    r"para\s+su\s+atenci[oó]n|favor\s+revisar|favor\s+atender)[.:!,-]*$",
    re.I,
)

# A wrapper phrase is administrative-only only when it also carries no
# independent operational action term of its own (Phase 8/Invariant 8) -
# "Fwd: please cancel booking ABC123" must never be swallowed as a zero-
# intent wrapper merely because it starts with "Fwd". Checked in addition
# to (not instead of) _ADMINISTRATIVE_ONLY_RE's own strict full-line match,
# which already rejects most of these via its punctuation-only trailing
# requirement - this is a second, explicit, independently-maintainable
# safeguard against the same failure mode.
_OPERATIONAL_ACTION_TERM_RE = re.compile(
    r"\b(?:cancel|update|change|create|book|quote|rate|deliver|pickup|pick\s+up|"
    r"return|move|revise|hold|release|"
    r"cambiar|cancelar|actualizar|reservar|cotizar)\b",
    re.I,
)


def _normalize_administrative_text(line: str) -> str:
    normalized = _DASH_NORMALIZE_RE.sub("-", line)
    normalized = _WHITESPACE_COLLAPSE_RE.sub(" ", normalized)
    normalized = _REPEATED_PUNCT_RE.sub(r"\1", normalized)
    return normalized.strip()


def _is_administrative_line(line: str) -> bool:
    normalized = _normalize_administrative_text(line)
    if not normalized:
        return True
    if _OPERATIONAL_ACTION_TERM_RE.search(normalized):
        return False
    return bool(_ADMINISTRATIVE_ONLY_RE.match(normalized))


def _is_administrative_or_empty(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return True
    lines = [line.strip() for line in stripped.splitlines() if line.strip()]
    if not lines:
        return True
    return len(lines) <= 2 and all(_is_administrative_line(line) for line in lines)


# --- Block-level label parsing (Phase 4) ------------------------------------


def _label_key(label: str) -> str:
    return _SPANISH_TO_ENGLISH_LABEL.get(label, label)


def _classify_label_line(line: str) -> tuple[str | None, str, str]:
    """Classify a single line's label (not its value) as "envelope",
    "operational", or None (not a recognized label at all - including any
    line that isn't shaped like `Label: value` in the first place). Returns
    (kind, normalized_label, value). This is the only place vocabulary is
    ever matched against a *label* - operational-vocabulary words appearing
    inside another label's *value* (e.g. "Subject: Quote Request") are
    never inspected here or anywhere else in this module."""
    match = _LABEL_LINE_RE.match(line.strip())
    if not match:
        return None, "", ""
    raw = re.sub(r"\s+", " ", match.group(1).strip().lower())
    value = match.group(2).strip()
    if raw in _ENVELOPE_LABELS or raw in _SPANISH_ENVELOPE_LABELS:
        return "envelope", _label_key(raw), value
    if raw in _OPERATIONAL_ONLY_LABELS:
        return "operational", raw, value
    return None, raw, value


@dataclass(frozen=True)
class _BlockScan:
    end_index: int
    kind: Literal["envelope", "operational", "none"]
    envelope_labels: tuple[str, ...]
    has_email: bool
    envelope_prefix_end: int
    envelope_prefix_is_coherent: bool


def _scan_label_block(lines: list[str], start_index: int) -> _BlockScan:
    """Scan the maximal contiguous run of label-shaped lines (blank-line
    tolerant) beginning at start_index and classify the whole run as one
    unit - never line-by-line with a shrinking look-ahead window, which is
    what caused every prior patch cycle's leak (a header-shaped line loses
    the evidence its own classification depends on the moment an earlier
    line in the same block has already been consumed).

    A second From:/De: line ends the run without being consumed - a
    genuine envelope is never repeated, so a second From: line starts
    unrelated content (the forwarded message's own operational lane, or a
    new nested block) rather than continuing the same envelope.

    Two different questions need two different answers from the same scan,
    which is why this returns both `kind` and a separate envelope-prefix
    result:

    - "Is this whole run safe to treat as active content, never quoted
      history?" (used by _find_reply_marker) - here ANY operational-only
      label anywhere in the run must veto envelope classification for the
      *entire* run, since the goal is protecting real operational content
      from being wrongly clipped as a reply header. `kind` answers this.
    - "How much of this run, starting from the top, is a coherent email
      envelope I should strip?" (used by _strip_envelope_block, only ever
      called on confirmed forwarded content) - here an operational-only
      label ends the *envelope* at that point without retroactively
      un-classifying the coherent envelope-labeled prefix that came before
      it (e.g. "From: Dispatch\nTo: Imports\nSubject: Port Update\nPort:
      Bayport Container Terminal" - the first three lines are still a
      genuine envelope even though "Port:" immediately follows with no
      blank line; only "Port:" and beyond is body content).
      `envelope_prefix_end`/`envelope_prefix_is_coherent` answer this.
    """
    cursor = start_index
    envelope_labels: set[str] = set()
    has_operational_label = False
    has_email = False
    has_from = False
    seen_from_already = False

    prefix_end = start_index
    prefix_has_from = False
    prefix_envelope_labels: set[str] = set()
    prefix_has_email = False
    prefix_closed = False

    while cursor < len(lines):
        if lines[cursor].strip() == "":
            # A blank line is only tolerated as a separator WITHIN a run of
            # envelope-shaped fields (some clients format headers with a
            # blank line between each field) - never as a bridge into
            # whatever follows the envelope, even when that next line also
            # happens to be label-shaped (e.g. a genuine "Booking Number:"
            # body line right after a stripped envelope must never be
            # folded into the same block merely because a blank line
            # separates them).
            peek = cursor + 1
            while peek < len(lines) and lines[peek].strip() == "":
                peek += 1
            peek_kind = _classify_label_line(lines[peek])[0] if peek < len(lines) else None
            if peek_kind != "envelope":
                break
            if has_operational_label and _scan_label_block(lines, peek).kind == "envelope":
                # Codex H1 (blank-line variant): once this run has already
                # established itself as operational content, a blank line
                # must never auto-bridge into a run that is, on its own, a
                # COHERENT email envelope (from + 2 more labels, or an
                # email) - that is a later, genuinely separate reply
                # envelope, not a continuation field of the current
                # request (Invariant 1). A blank followed by a run that is
                # only envelope-shaped-so-far but not yet coherent (e.g.
                # kind == "none"/"operational") still bridges, preserving
                # the existing ambiguity-preserves-content behavior.
                break
            cursor = peek
            continue
        kind, label, value = _classify_label_line(lines[cursor])
        if kind is None:
            break
        if kind == "operational":
            has_operational_label = True
            if not prefix_closed:
                prefix_closed = True
                prefix_end = cursor
            cursor += 1
            continue
        # kind == "envelope"
        if label == "from":
            if seen_from_already:
                break
            seen_from_already = True
            has_from = True
        envelope_labels.add(label)
        if _EMAIL_IN_VALUE_RE.search(value):
            has_email = True
        if not prefix_closed:
            if label == "from":
                prefix_has_from = True
            prefix_envelope_labels.add(label)
            if _EMAIL_IN_VALUE_RE.search(value):
                prefix_has_email = True
        cursor += 1

    if not prefix_closed:
        prefix_end = cursor

    if has_operational_label:
        block_kind: Literal["envelope", "operational", "none"] = "operational"
    elif has_from and (len(envelope_labels) >= 3 or has_email):
        block_kind = "envelope"
    else:
        block_kind = "none"

    prefix_is_coherent = prefix_has_from and (len(prefix_envelope_labels) >= 3 or prefix_has_email)

    return _BlockScan(
        end_index=cursor,
        kind=block_kind,
        envelope_labels=tuple(sorted(envelope_labels)),
        has_email=has_email,
        envelope_prefix_end=prefix_end,
        envelope_prefix_is_coherent=prefix_is_coherent,
    )


def _strip_envelope_block(lines: list[str], start_index: int) -> int:
    """If a coherent email envelope block begins at the first non-blank
    line at or after start_index, return the index immediately after its
    envelope-labeled prefix (stopping at the first operational-only label,
    if any, rather than requiring the entire contiguous run to be free of
    operational vocabulary - see _scan_label_block's envelope_prefix_*
    fields); otherwise return start_index unchanged (nothing stripped)."""
    anchor = start_index
    while anchor < len(lines) and lines[anchor].strip() == "":
        anchor += 1
    if anchor >= len(lines):
        return start_index
    scan = _scan_label_block(lines, anchor)
    if not scan.envelope_prefix_is_coherent:
        return start_index
    return scan.envelope_prefix_end


def _find_block_start(lines: list[str], candidate_index: int) -> int:
    """Walk backward from candidate_index to the true start of the
    contiguous label-shaped block it belongs to (Codex H1: scanning that
    starts partway through a block - e.g. at a From: line preceded by an
    unconsumed Equipment: line - sees only a truncated suffix and can
    misclassify an operational-label-first block as a coherent envelope,
    or vice versa). Stops at the first line that is not label-shaped
    (envelope or operational), at a forward/reply separator, at a Gmail/
    Spanish wrote marker, or at the start of the text - the same
    connectivity rules the forward scan already uses, applied in reverse.
    A blank line is only crossed backward when the line before it is
    itself label-shaped, mirroring _scan_label_block's forward rule."""
    index = candidate_index
    while index > 0:
        prev = index - 1
        if lines[prev].strip() == "":
            back = prev - 1
            while back >= 0 and lines[back].strip() == "":
                back -= 1
            if back < 0:
                break
            if _FORWARD_SEPARATOR_RE.match(lines[back]) or _UNDERSCORE_SEPARATOR_RE.match(lines[back]):
                break
            if _GMAIL_WROTE_RE.match(lines[back]) or _SPANISH_WROTE_RE.match(lines[back]):
                break
            if _classify_label_line(lines[back])[0] is None:
                break
            # Only cross the blank backward when a forward scan starting at
            # `back` would itself bridge across this same blank to reach
            # (or pass) `index` - i.e. the two sides are connected by the
            # exact same rule _scan_label_block uses going forward
            # (including the H1 operational-then-coherent-envelope
            # restriction above). Reusing _scan_label_block as the single
            # source of truth keeps forward and backward connectivity
            # decisions from ever disagreeing with each other.
            if _scan_label_block(lines, back).end_index <= index:
                break
            index = back
            continue
        if _FORWARD_SEPARATOR_RE.match(lines[prev]) or _UNDERSCORE_SEPARATOR_RE.match(lines[prev]):
            break
        if _GMAIL_WROTE_RE.match(lines[prev]) or _SPANISH_WROTE_RE.match(lines[prev]):
            break
        if _classify_label_line(lines[prev])[0] is None:
            break
        index = prev
    return index


def _find_reply_marker(lines: list[str]) -> int | None:
    index = 0
    while index < len(lines):
        line = lines[index]
        if _GMAIL_WROTE_RE.match(line) or _SPANISH_WROTE_RE.match(line):
            return index
        kind, _, _ = _classify_label_line(line)
        if kind is None:
            index += 1
            continue
        # A label-shaped line (envelope OR operational) is a candidate -
        # anchor to the true start of its containing block before
        # classifying, so an operational label immediately preceding an
        # envelope-shaped line is never invisible to the classifier
        # (Invariant 1/3), regardless of which line inside the block the
        # outer scan happens to reach first.
        block_start = _find_block_start(lines, index)
        scan = _scan_label_block(lines, block_start)
        if scan.kind == "envelope":
            return block_start
        # Whether "operational" or "none", this whole block is settled -
        # skip past it so its later lines aren't independently
        # re-evaluated as new candidates (never repeatedly classify
        # overlapping suffixes of the same block).
        index = max(scan.end_index, index + 1)
    return None


def _find_forward_marker(lines: list[str]) -> int | None:
    for index, line in enumerate(lines):
        if _FORWARD_SEPARATOR_RE.match(line) or _UNDERSCORE_SEPARATOR_RE.match(line):
            return index
    return None


def select_innermost_actionable_forward(raw_text: str) -> tuple[str, str, bool]:
    """Given raw text starting at (or containing) a forward separator,
    repeatedly strip the separator and its immediately-following coherent
    envelope block, descending into a directly-nested forward wrapper (a
    forward separator with nothing actionable between it and the next one)
    until reaching actionable content, a meaningful non-forward line, or no
    further forward boundary - whichever comes first. Bounded by
    _MAX_FORWARD_NESTING_DEPTH so malformed/adversarial input with an
    unbounded chain of separators cannot loop indefinitely; real email
    chains never nest this deep.

    Returns (innermost_actionable_text, outer_forwarded_block_text,
    depth_limit_reached). The second value preserves the full text from the
    first forward marker onward, for callers that want the complete
    forwarded/quoted history independent of which inner layer was selected
    as active. When the depth limit is reached, the first value is the
    unprocessed remainder at that point (never empty when any candidate
    seen so far was non-empty - Invariant 6) rather than the result of
    running the normal nested-reply-marker clip on a not-yet-stripped,
    still-nested block, which risked discarding real content beyond the
    traversal budget.
    """
    lines = raw_text.splitlines()
    forward_index = _find_forward_marker(lines)
    if forward_index is None:
        stripped = raw_text.strip()
        return stripped, raw_text, False

    outer_block_text = "\n".join(lines[forward_index:])
    cursor = forward_index + 1
    last_nonempty_candidate = "\n".join(lines[cursor:]).strip()

    for _ in range(_MAX_FORWARD_NESTING_DEPTH):
        cursor = _strip_envelope_block(lines, cursor)
        candidate = "\n".join(lines[cursor:]).strip()
        if candidate:
            last_nonempty_candidate = candidate
        peek = cursor
        while peek < len(lines) and lines[peek].strip() == "":
            peek += 1
        if peek < len(lines) and (
            _FORWARD_SEPARATOR_RE.match(lines[peek]) or _UNDERSCORE_SEPARATOR_RE.match(lines[peek])
        ):
            # Nothing actionable between this envelope and the next forward
            # wrapper - descend into the nested forward instead of stopping
            # on a bare separator/envelope with no real content.
            cursor = peek + 1
            continue
        break
    else:
        # Exhausted the traversal budget while still finding another nested
        # forward each time - cursor points at an unstripped, potentially
        # still-nested block. Never run the normal nested-reply-marker clip
        # on that unstripped remainder here: a bare envelope sitting at the
        # depth limit would be read as "quoted history begins here" and
        # discard everything beyond the budget, which is exactly the
        # destructive terminal state Invariant 6 forbids. Preserve the
        # current remainder as-is; fall back to the last known non-empty
        # candidate only in the degenerate case where the chain ends with
        # nothing at all after the final separator.
        remainder = "\n".join(lines[cursor:]).strip()
        return (remainder or last_nonempty_candidate), outer_block_text, True

    body_lines = lines[cursor:]
    nested_reply_index = _find_reply_marker(body_lines)
    if nested_reply_index is not None:
        body_lines = body_lines[:nested_reply_index]

    return "\n".join(body_lines).strip(), outer_block_text, False


def non_envelope_label_blocks(raw_text: str) -> tuple[str, ...]:
    """Return the text of every maximal contiguous label-shaped block in
    raw_text that is NOT a coherent email envelope (_scan_label_block's own
    `kind != "envelope"`) - operational and ambiguous ("none") blocks only.
    Callers that pair two labels found anywhere in scoped text (e.g. a
    From:/To: quote lane) must search within these block boundaries, never
    across the whole text independently, so:

    - two unrelated blocks separated by prose or a blank-line boundary are
      never merged into one pairing candidate (Codex M1 - "From: Houston"
      in one paragraph and an unrelated "To: Dallas" in another must never
      pair); and
    - a genuine email envelope (From:/To:/Subject:, or any coherent
      3+-label/email block) is never mistaken for operational lane data,
      since real envelope metadata is excluded entirely rather than
      merely subjected to the same plausibility filter operational values
      get.

    Reuses _find_block_start/_scan_label_block - the same connectivity
    rules build_message_scope's own reply/forward detection already uses -
    so this can never disagree with how the rest of the module divides
    text into blocks."""
    lines = raw_text.splitlines()
    blocks: list[str] = []
    index = 0
    while index < len(lines):
        kind, _, _ = _classify_label_line(lines[index])
        if kind is None:
            index += 1
            continue
        start = _find_block_start(lines, index)
        scan = _scan_label_block(lines, start)
        if scan.kind != "envelope":
            blocks.append("\n".join(lines[start:scan.end_index]))
        index = max(scan.end_index, index + 1)
    return tuple(blocks)


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
            confidence=0.9 if active_text else 0.3,
            evidence=(f"reply marker at line {reply_index}",),
            segmentation_status="ok" if active_text else "collapsed",
        )

    if forward_index is not None:
        top_level = "\n".join(lines[:forward_index]).strip()
        forwarded_block = "\n".join(lines[forward_index:]).strip()
        forwarded_active, _, depth_limit_reached = select_innermost_actionable_forward(forwarded_block)

        if _is_administrative_or_empty(top_level):
            segmentation_status: SegmentationStatus = "ok"
            confidence = 0.75
            evidence = [
                f"forward marker at line {forward_index}",
                "top-level text is empty/administrative - using innermost forwarded content",
            ]
            if depth_limit_reached:
                segmentation_status = "depth_limit_reached"
                confidence = 0.4
                evidence.append("forward_depth_limit_reached")
            elif not forwarded_active:
                segmentation_status = "collapsed"
                confidence = 0.3
                evidence.append("segmentation_collapsed - no authoritative active text found")
            return MessageScope(
                raw_text=raw_text,
                active_text=forwarded_active,
                quoted_text="",
                forwarded_text=forwarded_block,
                classification_text=forwarded_active,
                scope_type="forwarded_only",
                confidence=confidence,
                evidence=tuple(evidence),
                segmentation_status=segmentation_status,
            )

        return MessageScope(
            raw_text=raw_text,
            active_text=top_level,
            quoted_text="",
            forwarded_text=forwarded_block,
            classification_text=top_level,
            scope_type="forward",
            confidence=0.85 if top_level else 0.3,
            evidence=(
                f"forward marker at line {forward_index}",
                "top-level text has real content - forwarded content kept as supporting only",
            ),
            segmentation_status="ok" if top_level else "collapsed",
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
