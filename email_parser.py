from __future__ import annotations

import re
from email.utils import parseaddr
from typing import Any

FIELDS = [
    "TYPE", "Customer", "Booking Number", "Reference Number", "Container Number",
    "Size", "Port", "Warehouse", "Address", "Delivery Need Date",
    "Document Cutoff", "LFD", "Contact Name", "Contact Email", "Contact Phone",
    "Contact Company", "Dispatcher Notes",
]

LABEL_ALIASES = {
    "TYPE": ["Order Type", "Type", "Load Type", "Move Type", "Shipment Type"],
    "Customer": ["Customer", "Customer Name", "Account", "Bill To"],
    "Booking Number": ["Booking Number", "Booking #", "Booking", "Booking Ref", "Booking Reference", "Booking No", "BKG", "BKG Ref"],
    "Reference Number": ["Reference Number", "Reference #", "Reference", "Ref", "Ref #", "Load Reference", "Shipment Reference", "PME Ref", "PINC Ref", "Customer Ref"],
    "Container Number": ["Container Number", "Container #", "Container", "Cntr", "Container No"],
    "Size": ["Size", "Container Size", "Equipment", "Container Type", "Ctr QTY/Size", "Ctr Qty/Size", "Qty/Size"],
    "Port": ["Port", "Terminal", "Port/Terminal", "Pickup", "Pickup Location", "Pickup From", "Origin", "Origin Location", "Rail Ramp", "Ramp", "POL"],
    "Warehouse": ["Warehouse", "Delivery Warehouse", "Delivery Location", "Deliver To", "Delivery To", "Destination", "Destination Location", "Consignee", "Loading At", "Load At"],
    "Address": ["Address", "Delivery Address", "Warehouse Address", "Destination Address", "Consignee Address"],
    "Delivery Need Date": ["Delivery Need Date", "Delivery Date Needed", "Delivery Date", "Requested Date", "Need Date", "Appointment Date", "Loading / Date", "Load Date"],
    "Document Cutoff": ["Document Cutoff", "Cutoff Date", "Doc Cutoff", "Doc Cut-Off", "Port Cut", "Port Cutoff", "Port Cut-Off", "Cargo Cutoff", "Cargo Cut-Off"],
    "LFD": ["LFD", "Last Free Day"],
    "Contact Name": ["Contact", "Contact Name"],
    "Contact Email": ["Contact Email", "Email", "E-mail"],
    "Contact Phone": ["Contact Phone", "Phone", "Tel", "Telephone", "Mobile", "Cell"],
    "Contact Company": ["Contact Company", "Company Name", "Organization"],
    "Dispatcher Notes": ["Notes", "Dispatcher Notes", "Instructions", "Special Instructions"],
}

OWN_COMPANY_DOMAINS = {"calitranscorp.com"}
OWN_COMPANY_TERMS = (
    "calitrans",
    "cali trans",
    "calitrans corp",
    "calitrans corporation",
    "calitrans dispatch",
)

EMAIL_HEADER_LABELS = {"from", "to", "cc", "bcc", "sent", "subject", "date", "reply-to"}

REPLY_HEADER_LABELS = {"from", "sent", "to", "cc", "bcc", "subject", "date"}

LATEST_SIGNATURE_MARKERS = [
    r"^\s*--\s*$",
    r"^\s*(?:thanks|thank you|best|best regards|warm regards|regards|sincerely|respectfully)[,!\s]*$",
    r"^\s*\[[^\]]*(?:image|logo|jpg|jpeg|png|gif|http)[^\]]*\]\s*$",
    r"^\s*cid:image",
    r"^\s*important note\s*:",
    r"^\s*\*{0,2}confidentiality notice\*{0,2}\s*$",
]

FREE_EMAIL_DOMAINS = {
    "aol.com",
    "gmail.com",
    "hotmail.com",
    "icloud.com",
    "live.com",
    "me.com",
    "msn.com",
    "outlook.com",
    "proton.me",
    "protonmail.com",
    "yahoo.com",
}

MONTH_NAMES = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}


def _clean(value: Any) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value).replace("\xa0", " ")).strip().strip("-").strip().strip("*").strip()


def _email_domain(email_address: str) -> str:
    email_address = str(email_address or "").strip().lower()
    if "@" not in email_address:
        return ""
    return email_address.rsplit("@", 1)[-1].strip(" >.")


def _is_own_company_value(value: str) -> bool:
    text = str(value or "").lower()
    if not text:
        return False
    if any(f"@{domain}" in text or _email_domain(text.strip("<> ")) == domain for domain in OWN_COMPANY_DOMAINS):
        return True
    squashed = re.sub(r"[^a-z0-9@.]+", " ", text)
    return any(term in squashed for term in OWN_COMPANY_TERMS)


def _is_external_email(email_address: str) -> bool:
    domain = _email_domain(email_address)
    return bool(domain and domain not in OWN_COMPANY_DOMAINS)


def _normalize_text(text: str) -> str:
    normalized = str(text or "").replace("\xa0", " ")
    normalized = normalized.replace("\u2013", "-").replace("\u2014", "-")
    normalized = normalized.replace("\u200b", "")
    normalized = re.sub(r"<br\s*/?>", "\n", normalized, flags=re.I)
    return normalized


def _looks_like_reply_header(lines: list[str], index: int) -> bool:
    line = lines[index].strip()
    match = re.match(r"^([A-Z][A-Z -]{1,18}|[a-z][a-z -]{1,18})\s*:", line, re.I)
    if not match:
        return False
    label = match.group(1).strip().lower()
    if label not in REPLY_HEADER_LABELS:
        return False
    window = "\n".join(lines[index : min(len(lines), index + 8)])
    header_hits = len(re.findall(r"(?im)^\s*(?:from|sent|to|cc|bcc|subject|date)\s*:", window))
    if label == "from":
        return header_hits >= 2
    return header_hits >= 3


def _looks_like_outlook_separator(line: str) -> bool:
    return bool(
        re.match(r"^\s*-{2,}\s*Original Message\s*-{2,}\s*$", line, re.I)
        or re.match(r"^\s*_{6,}\s*$", line)
        or re.match(r"^\s*On .{5,180}\bwrote:\s*$", line, re.I)
    )


def _drop_security_banners(text: str) -> str:
    lines = []
    for line in str(text or "").splitlines():
        if re.match(r"^\s*CAUTION:\s+This email originated from outside", line, re.I):
            continue
        lines.append(line)
    return "\n".join(lines).strip()


def _clip_quoted_thread(text: str) -> str:
    lines = _normalize_text(text).splitlines()
    clipped: list[str] = []
    for index, line in enumerate(lines):
        if _looks_like_outlook_separator(line) or _looks_like_reply_header(lines, index):
            break
        clipped.append(line)
    return _drop_security_banners("\n".join(clipped)).strip()


def _signature_marker_index(lines: list[str]) -> int | None:
    nonblank_seen = 0
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped:
            nonblank_seen += 1
        if nonblank_seen == 0:
            continue
        for marker in LATEST_SIGNATURE_MARKERS:
            if re.match(marker, stripped, re.I):
                return index
        if nonblank_seen >= 2 and re.search(r"\b(?:LOGISTICS COORDINATOR|CUSTOMER SERVICE|OPERATIONS|PETRASCO SERVICES INC)\b", stripped, re.I):
            return max(0, index - 2)
    return None


def _clean_signature_block(signature: str) -> str:
    cleaned_lines: list[str] = []
    for line in str(signature or "").splitlines():
        line = _normalize_text(line).strip()
        if not line:
            cleaned_lines.append("")
            continue
        if re.match(r"^\s*(?:important note|confidentiality notice)\s*:?", line, re.I):
            break
        if re.match(r"^\s*this message is private and confidential", line, re.I):
            break
        if re.match(r"^\s*please note that,? in addition", line, re.I):
            break
        if re.match(r"^\s*\[?https?://", line, re.I) or re.match(r"^\s*<https?://", line, re.I):
            continue
        if "exclaimer.net" in line.lower() or "imprintmessageid=" in line.lower():
            continue
        if re.fullmatch(r"[,|]+", line):
            continue
        line = re.sub(r"<mailto:[^>]+>", "", line, flags=re.I)
        cleaned_lines.append(line)
    return "\n".join(cleaned_lines).strip()


def _split_latest_message_and_signature(body: str) -> tuple[str, str]:
    latest = _clip_quoted_thread(body)
    if not latest:
        return "", ""
    lines = latest.splitlines()
    marker_index = _signature_marker_index(lines)
    if marker_index is None:
        return latest.strip(), ""
    message = "\n".join(lines[:marker_index]).strip()
    signature = _clean_signature_block("\n".join(lines[marker_index:]).strip())
    return message, signature


def extract_latest_email_body(body: str | None, include_signature: bool = True) -> str:
    """Return only the newest message body from a replied/forwarded email thread."""
    message, signature = _split_latest_message_and_signature(body or "")
    if include_signature and signature:
        return f"{message}\n\n{signature}".strip() if message else signature
    return message.strip()


def _title_from_token(value: str) -> str:
    words = re.split(r"[\s._-]+", str(value or "").strip())
    cleaned = [word for word in words if word]
    return " ".join(word[:1].upper() + word[1:].lower() for word in cleaned)


def _domain_company(email_address: str) -> str:
    email_address = str(email_address or "").strip().lower()
    if "@" not in email_address:
        return ""
    domain = email_address.rsplit("@", 1)[-1]
    if not domain or domain in FREE_EMAIL_DOMAINS or domain in OWN_COMPANY_DOMAINS:
        return ""
    parts = [part for part in domain.split(".") if part]
    if not parts:
        return ""
    base = parts[-2] if len(parts) > 1 and parts[-1] in {"com", "net", "org", "co", "us"} else parts[0]
    return _title_from_token(base)


def _sender_identity(sender: str) -> dict[str, str]:
    raw_sender = str(sender or "")
    display_name, email_address = parseaddr(raw_sender)
    email_match = re.search(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", raw_sender, re.I)
    if email_match:
        if not email_address or " " in email_address or "@" not in email_address:
            email_address = email_match.group(0)
        if not display_name:
            display_name = raw_sender[:email_match.start()].strip().strip("<>").strip().strip("\"'")
    display_name = _clean(display_name).strip("\"'")
    email_address = _clean(email_address).lower().strip("<>")
    local_part = email_address.split("@", 1)[0] if email_address else ""
    contact_name = display_name
    if _is_own_company_value(email_address):
        contact_name = ""
    if not contact_name and local_part and not re.search(r"^(dispatch|info|orders?|ops|operations|accounting|billing)$", local_part, re.I):
        contact_name = _title_from_token(local_part)
    return {
        "Contact Name": contact_name,
        "Contact Email": email_address,
        "Contact Company": _domain_company(email_address),
    }


def _first_email(text: str, prefer_external: bool = True) -> str:
    matches = [match.group(0).lower() for match in re.finditer(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", text or "", re.I)]
    if prefer_external:
        for email_address in matches:
            if _is_external_email(email_address):
                return email_address
    return matches[0] if matches else ""


def _first_phone(text: str) -> str:
    mobile_match = re.search(
        r"\b(?:mobile|cell)\b\s*[:#.-]?\s*(\+?1?[\s.( -]*\d{3}[\s.) -]*\d{3}[\s.-]*\d{4}(?:\s*(?:x|ext\.?|extension)\s*\d{1,6})?)",
        text or "",
        re.I,
    )
    if mobile_match:
        return _clean(mobile_match.group(1))
    direct_match = re.search(
        r"(?im)^\s*(?:d|direct)\s*[:#.-]?\s*(\+?1?[\s.( -]*\d{3}[\s.) -]*\d{3}[\s.-]*\d{4}(?:\s*(?:x|ext\.?|extension)\s*\d{1,6})?)",
        text or "",
    )
    if direct_match:
        return _clean(direct_match.group(1))
    match = re.search(
        r"(?:\b(?:phone|tel|telephone|mobile|cell|direct)\b\s*[:#-]?\s*)?(\+?1?[\s.( -]*\d{3}[\s.) -]*\d{3}[\s.-]*\d{4}(?:\s*(?:x|ext\.?|extension)\s*\d{1,6})?)",
        text or "",
        re.I,
    )
    return _clean(match.group(1)) if match else ""


def _signature_blocks(body: str) -> list[str]:
    body = _normalize_text(body)
    markers = [
        r"(?im)^--\s*$",
        r"(?im)^thanks[,!\s]*$",
        r"(?im)^thank you[,!\s]*$",
        r"(?im)^best[,!\s]*$",
        r"(?im)^best regards[,!\s]*$",
        r"(?im)^warm regards[,!\s]*$",
        r"(?im)^regards[,!\s]*$",
        r"(?im)^sincerely[,!\s]*$",
    ]
    blocks: list[str] = []
    for marker in markers:
        matches = list(re.finditer(marker, body))
        for match in matches:
            candidate = _clip_quoted_thread(body[match.end():])
            if candidate:
                blocks.append(candidate)

    lines = [line.strip() for line in body.splitlines() if line.strip()]
    fallback = _clip_quoted_thread("\n".join(lines[-10:]))
    if fallback:
        blocks.append(fallback)
    return blocks


def _signature_score(signature: str, sender_identity: dict[str, str]) -> int:
    signature = signature or ""
    if not signature:
        return -100

    score = 0
    if _is_own_company_value(signature):
        score -= 25
    if _first_phone(signature):
        score += 2
    if _first_email(signature):
        score += 2
    company = _signature_company(signature, sender_identity.get("Contact Name", ""))
    if company and not _is_own_company_value(company):
        score += 4

    sender_email = sender_identity.get("Contact Email", "")
    if sender_email and sender_email.lower() in signature.lower():
        score += 6
    sender_company = sender_identity.get("Contact Company", "")
    if sender_company and re.search(re.escape(sender_company), signature, re.I):
        score += 5
    sender_name = sender_identity.get("Contact Name", "")
    if sender_name:
        sender_words = [word for word in re.split(r"\s+", sender_name) if len(word) > 1]
        score += sum(1 for word in sender_words if re.search(rf"\b{re.escape(word)}\b", signature, re.I))

    return score


def _signature_block(body: str, sender_identity: dict[str, str] | None = None) -> str:
    sender_identity = sender_identity or {}
    blocks = _signature_blocks(body)
    if not blocks:
        return ""
    scored = sorted(
        enumerate(blocks),
        key=lambda item: (_signature_score(item[1], sender_identity), -item[0]),
        reverse=True,
    )
    best = scored[0][1]
    if _is_own_company_value(best):
        for _, block in scored:
            if not _is_own_company_value(block):
                return block
    return best


def _looks_like_person_name(value: str) -> bool:
    value = _clean(value)
    if not value or "@" in value or re.search(r"\d", value):
        return False
    words = value.split()
    return 2 <= len(words) <= 4 and all(len(word.strip(".,|")) >= 2 for word in words)


def _signature_contact_name(signature: str) -> str:
    for line in [line.strip(" |") for line in signature.splitlines() if line.strip()]:
        if _looks_like_person_name(line):
            return _clean(line)
    return ""


def _signature_company(signature: str, contact_name: str = "") -> str:
    company_terms = r"\b(llc|inc|corp|corporation|co\.?|company|logistics|transport|transportation|trucking|freight|warehouse|warehousing|shipping|brokerage|forwarding)\b"
    legal_terms = r"\b(llc|inc|corp|corporation|co\.?|company|ltd|limited)\b"
    title_terms = r"\b(coordinator|manager|director|specialist|dispatch|dispatcher|operations|sales|accounting|customer service|csr|rep|representative)\b"
    contact_name = _clean(contact_name).lower()
    for line in [line.strip(" |") for line in signature.splitlines() if line.strip()]:
        cleaned = _clean(line)
        lowered = cleaned.lower()
        if not cleaned or lowered == contact_name:
            continue
        if _is_own_company_value(cleaned):
            continue
        if "@" in cleaned or _first_phone(cleaned):
            continue
        if re.search(r"\b(?:website|linkedin|youtube|terms|conditions)\b", cleaned, re.I):
            continue
        if re.search(title_terms, cleaned, re.I) and not re.search(legal_terms, cleaned, re.I):
            continue
        if re.search(company_terms, cleaned, re.I):
            return cleaned
    return ""


def _find_labeled_value(text: str, aliases: list[str]) -> str:
    all_aliases = {alias.lower() for values in LABEL_ALIASES.values() for alias in values}
    all_aliases.update({"vessel", "loading / wk", "loading / date", "port cut", "etd", "eta", "pod", "pol"})

    def is_known_label(value: str) -> bool:
        cleaned = re.sub(r"[*_`~:#-]+", " ", _clean(value)).strip().lower()
        cleaned = re.sub(r"\s+", " ", cleaned)
        return cleaned in all_aliases

    for alias in aliases:
        if alias.strip().lower() in EMAIL_HEADER_LABELS:
            continue
        pattern = rf"(?im)^\s*[*_`~\s]*{re.escape(alias)}\s*[*_`~\s]*(?:[:#]|-\s)\s*(.+?)\s*$"
        match = re.search(pattern, text)
        if match:
            return _clean(match.group(1))

    lines = str(text or "").splitlines()
    for index, line in enumerate(lines):
        for alias in aliases:
            if alias.strip().lower() in EMAIL_HEADER_LABELS:
                continue
            label_pattern = rf"^\s*[*_`~\s]*{re.escape(alias)}\s*[*_`~\s]*(?:[:#])?\s*$"
            if not re.match(label_pattern, line, re.I):
                continue
            for candidate in lines[index + 1 : min(len(lines), index + 7)]:
                value = _clean(candidate)
                if not value:
                    continue
                if is_known_label(value):
                    continue
                return value
    return ""


def _infer_type(text: str) -> str:
    lowered = text.lower()
    if "local import" in lowered:
        return "Import Local"
    if "local export" in lowered:
        return "Export Local"
    if re.search(r"\bpol\b", lowered) and re.search(r"\bpod\b", lowered):
        return "Export"
    if "booking confirmation" in lowered and re.search(r"\b(?:loading at|port cut|cargo cut)\b", lowered):
        return "Export"
    if "export" in lowered:
        return "Export"
    if "import" in lowered or "rail ramp" in lowered or "live unload" in lowered or "whse" in lowered:
        return "Import"
    return ""


def _append_note(existing: str, note: str) -> str:
    existing = _clean(existing)
    note = _clean(note)
    if not note:
        return existing
    return note if not existing else f"{existing}; {note}"


def _first_container(text: str) -> str:
    match = re.search(r"\b[A-Z]{4}\d{7}\b", text, re.I)
    return match.group(0).upper() if match else ""


def _subject_reference(subject: str) -> str:
    subject = _normalize_text(subject)
    patterns = [
        r"(?:^|/|\b)([A-Z]{1,6}\d{5,}[A-Z0-9-]*)\b",
        r"\b(?:ref(?:erence)?|po|order|load|shipment)\s*(?:#|number|no\.?)?\s*[:#-]\s*([A-Z0-9][A-Z0-9-]{4,})\b",
        r"/\s*([A-Z0-9][A-Z0-9-]{4,})\s*(?:/|$)",
    ]
    for pattern in patterns:
        match = re.search(pattern, subject, re.I)
        if match:
            token = _clean(match.group(1)).upper()
            if _is_reference_candidate(token):
                return token
    return ""


def _is_reference_candidate(value: str) -> bool:
    value = re.sub(r"\s+", " ", _clean(value)).upper()
    if not value or len(value) > 45:
        return False
    if _is_own_company_value(value):
        return False
    if re.search(r"\b(?:RESPONSIBLE|VIOLATION|VIOLATIONS|TICKET|TICKETS|OVERWEIGHT|AXLE)\b", value, re.I):
        return False
    if value.lower() in {"trucking", "shipment", "booking", "container"}:
        return False
    if not re.search(r"\d", value):
        return False
    if len(value.split()) > 4:
        return False
    return bool(re.fullmatch(r"[A-Z0-9][A-Z0-9._/ -]{3,44}", value))


def _subject_equipment(subject: str) -> str:
    match = re.search(r"\b(\d+)\s*x\s*(20|40|45)\s*'?\s*(HC|HQ|FT|STD|DRY|RF)?\b", subject or "", re.I)
    if not match:
        return ""
    suffix = _clean(match.group(3) or "")
    equipment = f"{match.group(1)} x {match.group(2)}"
    if suffix:
        equipment = f"{equipment}' {suffix.upper()}"
    return equipment


def _sent_month_year(text: str) -> tuple[int, int] | None:
    match = re.search(
        r"(?im)^\s*(?:sent|date)\s*:\s*(?:[A-Z][a-z]+,\s*)?([A-Z][a-z]+)\s+\d{1,2},\s*(\d{4})\b",
        text or "",
    )
    if not match:
        return None
    month = MONTH_NAMES.get(match.group(1).lower())
    year = int(match.group(2))
    return (month, year) if month else None


def _iso_date(month: int, day: int, year: int) -> str:
    return f"{year:04d}-{month:02d}-{day:02d}"


def _normalize_short_date(value: str, text: str) -> str:
    value = _clean(value)
    match = re.fullmatch(r"(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?", value)
    if not match:
        return value
    month = int(match.group(1))
    day = int(match.group(2))
    year_text = match.group(3)
    if year_text:
        year = int(year_text)
        if year < 100:
            year += 2000
    else:
        sent_context = _sent_month_year(text)
        year = sent_context[1] if sent_context else 0
    if year:
        return _iso_date(month, day, year)
    return value


def _delivery_request_date(text: str) -> str:
    sent_context = _sent_month_year(text)
    match = re.search(
        r"\b(?:schedule|deliver|delivery)\b.{0,120}?\b(?:on|for)\s+(?:the\s+)?(\d{1,2})(?:st|nd|rd|th)\b",
        text or "",
        re.I | re.S,
    )
    if match and sent_context:
        month, year = sent_context
        return _iso_date(month, int(match.group(1)), year)

    match = re.search(
        r"\b(?:schedule|deliver|delivery)\b.{0,120}?\b(?:on|for)\s+(\d{1,2}/\d{1,2}(?:/\d{2,4})?)\b",
        text or "",
        re.I | re.S,
    )
    if match:
        return _normalize_short_date(match.group(1), text)
    return ""


def _sentence_excerpt(text: str, start_index: int, max_length: int = 160) -> str:
    before = text.rfind("\n", 0, start_index)
    after = text.find("\n", start_index)
    if before == -1:
        before = max(0, start_index - max_length // 2)
    if after == -1:
        after = min(len(text), start_index + max_length)
    return _clean(text[before:after])[:max_length]


def _append_schedule_notes(parsed: dict[str, str], combined: str) -> None:
    equipment = _subject_equipment(combined.splitlines()[0] if combined else "")
    if equipment:
        parsed["Dispatcher Notes"] = _append_note(parsed["Dispatcher Notes"], f"Equipment: {equipment}")

    schedule_match = re.search(r"\b(?:schedule|deliver|delivery)\b.{0,120}?\b(?:port|terminal|warehouse|whse|pickup|deliver)\b", combined or "", re.I | re.S)
    if schedule_match:
        parsed["Dispatcher Notes"] = _append_note(
            parsed["Dispatcher Notes"],
            f"Schedule request: {_sentence_excerpt(combined, schedule_match.start())}",
        )

    erd_match = re.search(r"(?im)^\s*ERD\s*[:#-]?\s*(\d{1,2}/\d{1,2}(?:/\d{2,4})?)\s*$", combined or "")
    if erd_match:
        parsed["Dispatcher Notes"] = _append_note(
            parsed["Dispatcher Notes"],
            f"ERD: {_normalize_short_date(erd_match.group(1), combined)}",
        )

    requested_time = re.search(
        r"\b(?:about|around|at|by)\s+(\d{1,2}(?::\d{2})?\s*(?:am|pm)?\s*(?:-|to)\s*\d{1,2}(?::\d{2})?\s*(?:am|pm)?)\b",
        combined or "",
        re.I,
    )
    if not requested_time:
        requested_time = re.search(r"\b(?:about|around|at|by)\s+(\d{1,2}(?::\d{2})?\s*(?:am|pm))\b", combined or "", re.I)
    if requested_time:
        parsed["Dispatcher Notes"] = _append_note(
            parsed["Dispatcher Notes"],
            f"Requested time: {_clean(requested_time.group(1))}",
        )


def _split_location_detail(value: str) -> tuple[str, str, str]:
    value = _clean(value).rstrip(".")
    if not value:
        return "", "", ""

    parts = [part.strip(" .") for part in re.split(r"\s+(?:-|\?)\s+", value, maxsplit=1) if part.strip(" .")]
    primary = parts[0] if parts else value
    note = parts[1] if len(parts) > 1 else ""
    looks_like_address = bool(
        re.search(r"\b\d{1,6}\s+[^,]+,\s*[^,]+,\s*[A-Z]{2}\s+\d{5}(?:-\d{4})?\b", primary, re.I)
        or re.search(r"\b\d{1,6}\s+\w+", primary)
    )
    if looks_like_address:
        warehouse = note or primary
        return warehouse, primary, note
    return primary, "", note


def _location_block_after_label(text: str, aliases: list[str], max_lines: int = 6) -> tuple[str, str]:
    all_aliases = {alias.lower() for values in LABEL_ALIASES.values() for alias in values}
    all_aliases.update({"vessel", "loading / wk", "loading / date", "port cut", "etd", "eta", "pod", "pol"})
    lines = str(text or "").splitlines()
    for index, line in enumerate(lines):
        if not any(re.match(rf"^\s*[*_`~\s]*{re.escape(alias)}\s*[*_`~\s]*(?:[:#])?\s*$", line, re.I) for alias in aliases):
            continue
        block: list[str] = []
        for candidate in lines[index + 1 : min(len(lines), index + 1 + max_lines)]:
            value = _clean(candidate)
            if not value:
                if block:
                    break
                continue
            label_candidate = re.sub(r"[*_`~:#-]+", " ", value).strip().lower()
            label_candidate = re.sub(r"\s+", " ", label_candidate)
            if label_candidate in all_aliases:
                break
            if re.match(r"^\s*(?:from|sent|to|cc|subject|date)\s*:", value, re.I):
                break
            block.append(value)
        if block:
            warehouse = block[0]
            address = " ".join(block[1:]).strip()
            return warehouse, address
    return "", ""


def _labeled_note_value(text: str, aliases: list[str]) -> str:
    return _find_labeled_value(text, aliases)


def _append_labeled_notes(parsed: dict[str, str], combined: str) -> None:
    notes = [
        ("Origin", _labeled_note_value(combined, ["Origin", "Pickup", "Pickup From", "Pickup Location"])),
        ("Destination", _labeled_note_value(combined, ["Destination", "Deliver To", "Delivery To", "Delivery Location"])),
        ("Commodity", _labeled_note_value(combined, ["Commodity", "Cargo", "Product"])),
        ("Weight", _labeled_note_value(combined, ["Weight", "Gross Weight"])),
    ]

    equipment_match = re.search(
        r"(?im)^\s*[*_`~\s]*(\d+\s*x\s*(?:20|40|45)\s*'?\s*(?:ft|hc|hq|std|container)?)\s*[*_`~\s]*:?\s*$",
        combined,
    )
    if equipment_match:
        notes.append(("Equipment", _clean(equipment_match.group(1))))

    for label, value in notes:
        if value:
            parsed["Dispatcher Notes"] = _append_note(parsed["Dispatcher Notes"], f"{label}: {value}")


def _append_contact_notes(parsed: dict[str, str]) -> None:
    for field, label in [
        ("Contact Name", "Contact"),
        ("Contact Email", "Contact email"),
        ("Contact Phone", "Contact phone"),
        ("Contact Company", "Contact company"),
    ]:
        value = _clean(parsed.get(field, ""))
        if value:
            parsed["Dispatcher Notes"] = _append_note(parsed["Dispatcher Notes"], f"{label}: {value}")


def _invalid_location_value(value: str) -> bool:
    value = _clean(value)
    if not value:
        return False
    if _is_own_company_value(value):
        return True
    if "@" in value or "<" in value or ">" in value:
        return True
    if re.match(r"^(?:from|to|cc|bcc|sent|subject)\s*:", value, re.I):
        return True
    location_hint = bool(
        "," in value
        or re.search(r"\b(?:port|terminal|ramp|rail|yard|warehouse|whse|crossdock|depot|street|st\.?|road|rd\.?|avenue|ave\.?|hwy|highway)\b", value, re.I)
        or re.search(r"\b(?:AL|AK|AZ|AR|CA|CO|CT|DE|FL|GA|IL|IN|LA|MD|MI|MN|MO|NC|NJ|NM|NV|NY|OH|OK|OR|PA|SC|TN|TX|VA|WA|WI)\b", value)
    )
    if _looks_like_person_name(value) and not location_hint:
        return True
    return False


def _sanitize_parsed(parsed: dict[str, str], sender_identity: dict[str, str], signature: str) -> None:
    for field in ["Customer", "Contact Company"]:
        if _is_own_company_value(parsed.get(field, "")):
            parsed[field] = ""

    if parsed.get("Contact Email") and not _is_external_email(parsed["Contact Email"]):
        parsed["Contact Email"] = sender_identity.get("Contact Email", "") if _is_external_email(sender_identity.get("Contact Email", "")) else ""

    if _is_own_company_value(parsed.get("Contact Name", "")):
        parsed["Contact Name"] = ""

    if _invalid_location_value(parsed.get("Port", "")):
        parsed["Port"] = ""
    if _invalid_location_value(parsed.get("Warehouse", "")):
        parsed["Warehouse"] = ""

    reference = _clean(parsed.get("Reference Number", ""))
    if reference and not _is_reference_candidate(reference):
        parsed["Reference Number"] = ""

    signature_company = _signature_company(signature, parsed.get("Contact Name", ""))
    if not parsed.get("Contact Company") and signature_company and not _is_own_company_value(signature_company):
        parsed["Contact Company"] = signature_company
    if not parsed.get("Contact Company") and sender_identity.get("Contact Company"):
        parsed["Contact Company"] = sender_identity["Contact Company"]
    if not parsed.get("Customer") and parsed.get("Contact Company"):
        parsed["Customer"] = parsed["Contact Company"]


def _container_correction(text: str) -> tuple[str, str]:
    match = re.search(
        r"\bcontainer\s+([A-Z]{4}\d{7})\b.{0,80}?\b(?:instead\s+of|not|rather\s+than)\s+([A-Z]{4}\d{7})\b",
        text,
        re.I | re.S,
    )
    if match:
        return match.group(1).upper(), match.group(2).upper()

    match = re.search(
        r"\b([A-Z]{4}\d{7})\b.{0,80}?\b(?:instead\s+of|not|rather\s+than)\s+([A-Z]{4}\d{7})\b",
        text,
        re.I | re.S,
    )
    if match:
        return match.group(1).upper(), match.group(2).upper()

    return "", ""


def parse_email_text(subject: str | None = None, body: str | None = None, sender: str | None = None) -> dict[str, str]:
    """Parse load order fields from email text.

    Works as parse_email_text(body), parse_email_text(subject, body), or
    parse_email_text(subject, body, sender).
    """
    if body is None:
        body = subject or ""
        subject = ""

    subject = _normalize_text(subject or "")
    body = _normalize_text(body or "")
    latest_message, latest_signature = _split_latest_message_and_signature(body)
    latest_body = f"{latest_message}\n\n{latest_signature}".strip() if latest_signature else latest_message
    field_body = latest_message or latest_body or body
    contact_body = latest_body or body
    combined = f"{subject}\n{field_body}"
    parsed = {field: "" for field in FIELDS}

    for field, aliases in LABEL_ALIASES.items():
        parsed[field] = _find_labeled_value(combined, aliases)

    sender_identity = _sender_identity(sender or "")
    signature = latest_signature or _signature_block(contact_body, sender_identity)
    for field, value in sender_identity.items():
        if value and (not parsed[field] or _is_own_company_value(parsed[field])):
            parsed[field] = value
    signature_email = _first_email(signature)
    if signature_email and (not parsed["Contact Email"] or not _is_external_email(parsed["Contact Email"])):
        parsed["Contact Email"] = signature_email
    if not parsed["Contact Email"]:
        parsed["Contact Email"] = _first_email(contact_body)
    signature_phone = _first_phone(signature)
    if signature_phone and (not parsed["Contact Phone"] or re.search(r"\b(?:mobile|cell)\b", signature, re.I)):
        parsed["Contact Phone"] = signature_phone
    if not parsed["Contact Phone"]:
        parsed["Contact Phone"] = _first_phone(contact_body)
    if not parsed["Contact Name"]:
        parsed["Contact Name"] = _signature_contact_name(signature)
    signature_company = _find_labeled_value(signature, ["Company Name", "Organization"]) or _signature_company(signature, parsed["Contact Name"])
    if signature_company and not _is_own_company_value(signature_company):
        parsed["Contact Company"] = signature_company
    elif not parsed["Contact Company"]:
        parsed["Contact Company"] = _domain_company(parsed["Contact Email"])
    if (not parsed["Customer"] or _is_own_company_value(parsed["Customer"])) and parsed["Contact Company"]:
        parsed["Customer"] = parsed["Contact Company"]

    destination_value = _find_labeled_value(
        combined,
        ["Destination", "Destination Location", "Delivery Location", "Deliver To", "Delivery To", "Consignee"],
    )
    if destination_value:
        warehouse, address, destination_note = _split_location_detail(destination_value)
        if warehouse and (not parsed["Warehouse"] or parsed["Warehouse"] == destination_value):
            parsed["Warehouse"] = warehouse
        if address and not parsed["Address"]:
            parsed["Address"] = address
        if destination_note:
            parsed["Dispatcher Notes"] = _append_note(parsed["Dispatcher Notes"], f"Destination note: {destination_note}")

    loading_warehouse, loading_address = _location_block_after_label(combined, ["Loading At", "Load At", "Loading Location"])
    if loading_warehouse and not parsed["Warehouse"]:
        parsed["Warehouse"] = loading_warehouse
    if loading_address and not parsed["Address"]:
        parsed["Address"] = loading_address

    if not parsed["TYPE"]:
        parsed["TYPE"] = _infer_type(combined)

    corrected_container, replaced_container = _container_correction(combined)
    if corrected_container:
        parsed["Container Number"] = corrected_container
        parsed["Dispatcher Notes"] = _append_note(
            parsed["Dispatcher Notes"],
            f"Container correction noted: use {corrected_container} instead of {replaced_container}.",
        )

    subject_pair = re.search(r"\b([A-Z0-9-]{5,})\s*/\s*([A-Z]{4}\d{7})\b", subject or "", re.I)
    if subject_pair:
        if not parsed["Reference Number"]:
            parsed["Reference Number"] = subject_pair.group(1).upper()
        if not parsed["Container Number"]:
            parsed["Container Number"] = subject_pair.group(2).upper()

    if not parsed["Container Number"]:
        parsed["Container Number"] = _first_container(combined)

    if not parsed["Booking Number"]:
        booking_subject = re.search(
            r"\bbooking(?:\s+(?:confirmation|ref(?:erence)?|no\.?|number))?\b[^A-Z0-9]{0,20}([A-Z0-9][A-Z0-9-]{4,})\b",
            subject,
            re.I,
        )
        if booking_subject:
            parsed["Booking Number"] = booking_subject.group(1).upper()

    if not parsed["Booking Number"]:
        match = re.search(r"\b(?:MAEU|ONEY|COSU|ZIMU|HLCU|MSCU)[A-Z0-9-]{4,}\b", combined, re.I)
        if match:
            parsed["Booking Number"] = match.group(0).upper()

    if not parsed["Reference Number"]:
        match = (
            re.search(r"(?im)^\s*(?:ref(?:erence)?|po|order|load|shipment)\s*(?:#|number|no\.?)?\s*[:#-]\s*([A-Z0-9][A-Z0-9._/-]{3,44})\s*$", combined, re.I)
            or re.search(r"\b([A-Z0-9-]{5,})\s*/\s*[A-Z]{4}\d{7}\b", subject or "", re.I)
        )
        if match and _is_reference_candidate(match.group(1)):
            parsed["Reference Number"] = re.sub(r"\s+", " ", match.group(1)).strip().upper()

    if not parsed["Reference Number"]:
        parsed["Reference Number"] = _subject_reference(subject)

    if not parsed["Customer"]:
        if re.search(r"\bflat\s*world\b|@flatworldgs\.com\b", combined, re.I):
            parsed["Customer"] = "Flat World Global Logistics"

    if not parsed["Size"]:
        match = re.search(r"\b(?:\d+\s*x\s*)?(20|40|45)\s*'?\s*(?:ft|hc|hq|std|drayage|container)?\b", combined, re.I)
        if match:
            parsed["Size"] = match.group(1)

    if not parsed["Delivery Need Date"]:
        match = (
            re.search(r"\bready\s+for\s+loading\s+on\s+(?:the\s+)?(\d{1,2}/\d{1,2}(?:/\d{2,4})?)\b", combined, re.I)
            or re.search(r"\bready\s+(?:on|for)\s+(?:the\s+)?(\d{1,2}/\d{1,2}(?:/\d{2,4})?)\b", combined, re.I)
        )
        if match:
            parsed["Delivery Need Date"] = _normalize_short_date(match.group(1), combined)
    if not parsed["Delivery Need Date"]:
        parsed["Delivery Need Date"] = _delivery_request_date(combined)

    if not parsed["Document Cutoff"]:
        match = re.search(r"\b(?:doc(?:ument)?\s*)?cut\s*off|cutoff\b", combined, re.I)
        if match:
            date_match = re.search(r"\b(?:doc(?:ument)?\s*)?(?:cut\s*off|cutoff)\s*(?:is|:)?\s*(\d{1,2}/\d{1,2}(?:/\d{2,4})?)", combined, re.I)
            if date_match:
                parsed["Document Cutoff"] = date_match.group(1)

    lowered = combined.lower()
    if "delivery order" in lowered:
        parsed["Dispatcher Notes"] = _append_note(parsed["Dispatcher Notes"], "Delivery order mentioned in email")
    if "packing list" in lowered:
        parsed["Dispatcher Notes"] = _append_note(parsed["Dispatcher Notes"], "Packing list mentioned in email")
    if "hazmat" in lowered:
        parsed["Dispatcher Notes"] = _append_note(parsed["Dispatcher Notes"], "Hazmat mentioned in email")
    if "imo" in lowered or "imos" in lowered:
        parsed["Dispatcher Notes"] = _append_note(parsed["Dispatcher Notes"], "IMO documents mentioned in email")
    if "live unload" in lowered:
        parsed["Dispatcher Notes"] = _append_note(parsed["Dispatcher Notes"], "Live unload requested")

    _append_labeled_notes(parsed, combined)
    _append_schedule_notes(parsed, combined)
    _sanitize_parsed(parsed, sender_identity, signature)
    _append_contact_notes(parsed)

    return parsed
