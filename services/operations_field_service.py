from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any, Iterable

from services.workflow_constants import normalize_service_flow


SHARED_OPERATION_FIELDS = [
    "TYPE",
    "Customer",
    "Booking Number",
    "Reference Number",
    "Container Number",
    "Container Numbers",
    "Container Qty",
    "Size",
    "Port",
    "Terminal",
    "Port PIN",
    "Warehouse",
    "Address",
    "Delivery Need Date",
    "Document Cutoff",
    "LFD",
    "Contact Name",
    "Contact Title",
    "Contact Company",
    "Contact Email",
    "Contact Phone",
]

_IDENTIFIER_STOP_WORDS = {
    "attached",
    "booking",
    "calitrans",
    "container",
    "dispatch",
    "export",
    "hello",
    "import",
    "new",
    "order",
    "please",
    "shipment",
    "team",
}
_JOB_TITLE_RE = re.compile(
    r"\b(?:account(?:ing)?|coordinator|customer\s+service|director|dispatch(?:er)?|"
    r"manager|representative|specialist|supervisor|operations|sales|shipping)\b",
    re.I,
)
_COMPANY_SUFFIX_RE = re.compile(
    r"\b(?:inc|incorporated|llc|ltd|limited|corp|corporation|company|co\.?|group|"
    r"industries|industrial|logistics|polymer|packaging|supply|foods?)\b",
    re.I,
)
_LOCATION_BAD_PREFIX_RE = re.compile(
    r"^(?:on\s+[a-z]+|and\s+return|deliver\s+the|by\s+\d|no\s+later|please\b)",
    re.I,
)
_EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
_PHONE_RE = re.compile(
    r"(?<!\d)(\+?1?[\s.( -]*\d{3}[\s.) -]*\d{3}[\s.-]*\d{4}"
    r"(?:\s*(?:x|ext\.?|extension)\s*\d{1,6})?)(?!\d)",
    re.I,
)
_CONTAINER_RE = re.compile(r"\b[A-Z]{4}\d{7}\b", re.I)


@dataclass
class FieldCandidate:
    field: str
    value: Any
    source: str
    method: str
    confidence: float
    evidence: str
    valid: bool = False
    validation_status: str = "unvalidated"
    rejection_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _clean(value: Any) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value).replace("\xa0", " ")).strip(" \t\r\n:;,-")


def _evidence(text: str, start: int, end: int, radius: int = 55) -> str:
    return _clean(text[max(0, start - radius) : min(len(text), end + radius)])


def _candidate(
    field: str,
    value: Any,
    source: str,
    method: str,
    confidence: float,
    text: str,
    start: int,
    end: int,
) -> FieldCandidate:
    value = _clean(value) if not isinstance(value, list) else value
    valid, reason = validate_field_value(
        field,
        value,
        source=source,
        method=method,
        evidence=_evidence(text, start, end),
    )
    return FieldCandidate(
        field=field,
        value=value,
        source=source,
        method=method,
        confidence=confidence,
        evidence=_evidence(text, start, end),
        valid=valid,
        validation_status="valid" if valid else "rejected",
        rejection_reason=reason,
    )


def validate_field_value(
    field: str,
    value: Any,
    *,
    source: str = "",
    method: str = "",
    evidence: str = "",
) -> tuple[bool, str]:
    if field == "Container Numbers":
        values = value if isinstance(value, list) else [item for item in str(value or "").split(",") if item.strip()]
        if not values:
            return False, "blank"
        if all(_CONTAINER_RE.fullmatch(_clean(item).upper()) for item in values):
            return True, ""
        return False, "one or more container numbers have an invalid structure"

    text = _clean(value)
    if not text:
        return False, "blank"

    lowered = text.lower()
    if field == "Booking Number":
        if lowered in _IDENTIFIER_STOP_WORDS or any(term in lowered for term in ("calitrans", "new booking")):
            return False, "common word, greeting, or company name is not a booking identifier"
        if not re.fullmatch(r"[A-Z0-9][A-Z0-9._/-]{3,44}", text, re.I):
            return False, "booking identifier has an invalid structure"
        if not re.search(r"\d", text):
            return False, "booking identifier has no numeric component"
        return True, ""

    if field == "Reference Number":
        if (
            lowered in _IDENTIFIER_STOP_WORDS
            or lowered in {"lymer", "polymer"}
            or lowered.startswith(("for ", "booking ", "the ", "with ", "using ", "following "))
        ):
            return False, "common word or partial-word match is not a reference"
        if not re.fullmatch(r"[A-Z0-9][A-Z0-9._/ -]{1,44}", text, re.I):
            return False, "reference has an invalid structure"
        explicit_context = method in {"reference_label", "document_label"} or bool(
            re.search(r"(?<!\w)(?:po|ref(?:erence)?|order|shipment|customer\s+ref)\b", evidence, re.I)
        )
        if not re.search(r"\d", text) and not explicit_context:
            return False, "letter-only reference lacks a standalone explicit label"
        return True, ""

    if field in {"Container Number"}:
        return (
            (True, "")
            if _CONTAINER_RE.fullmatch(text.upper())
            else (False, "container number must contain four letters and seven digits")
        )

    if field in {"Customer", "Contact Company"}:
        if re.match(
            r"^(?:customer|warehouse|booking|reference|container|port|terminal|address)\s*:",
            text,
            re.I,
        ):
            return False, "labeled operational field cannot be used as a company/customer"
        if _JOB_TITLE_RE.fullmatch(text) or (
            _JOB_TITLE_RE.search(text) and len(text.split()) <= 5 and not _COMPANY_SUFFIX_RE.search(text)
        ):
            return False, "job title cannot be used as a company/customer"
        if "@" in text or _PHONE_RE.search(text):
            return False, "contact detail cannot be used as a company/customer"
        if lowered in _IDENTIFIER_STOP_WORDS or lowered.startswith(("hello ", "dear ")):
            return False, "greeting or operational keyword cannot be used as a company/customer"
        return (True, "") if len(text) >= 2 else (False, "customer value is too short")

    if field == "Contact Title":
        if re.search(r"\b(?:booking|dispatch)\s+order\b", text, re.I):
            return False, "document heading cannot be used as a contact title"
        return (True, "") if _JOB_TITLE_RE.search(text) else (False, "value does not look like a contact title")

    if field == "Contact Name":
        if (
            _JOB_TITLE_RE.fullmatch(text)
            or _COMPANY_SUFFIX_RE.search(text)
            or re.search(r"\d|@", text)
            or re.search(r"\b(?:booking|dispatch)\s+order\b", text, re.I)
            or re.match(
                r"^(?:customer|warehouse|booking|reference|container|port|terminal|address)\s*:",
                text,
                re.I,
            )
            or lowered in {
                "contact",
                "contact name",
                "contact phone",
                "contact email",
                "phone",
                "email",
            }
        ):
            return False, "value does not look like a person name"
        words = text.split()
        return ((True, "") if 2 <= len(words) <= 5 else (False, "value does not look like a person name"))

    if field == "Contact Email":
        return ((True, "") if _EMAIL_RE.fullmatch(text) else (False, "invalid email address"))

    if field == "Contact Phone":
        return ((True, "") if _PHONE_RE.fullmatch(text) else (False, "invalid phone number"))

    if field in {"Warehouse", "Port", "Terminal"}:
        if _LOCATION_BAD_PREFIX_RE.search(text):
            return False, "scheduling or instruction text is not a location"
        if "@" in text or _PHONE_RE.search(text):
            return False, "contact detail is not a location"
        return ((True, "") if len(text) >= 3 else (False, "location is too short"))

    if field == "Address":
        return (
            (True, "")
            if re.search(r"\b\d{1,6}\s+\S+", text)
            else (False, "address lacks a street number")
        )

    if field == "Size":
        return (
            (True, "")
            if re.fullmatch(r"(?:20|40|45)\s*'?\s*(?:FT|HC|HQ|GP|RF|STD|DV)?", text, re.I)
            else (False, "invalid container size")
        )

    if field == "Container Qty":
        return ((True, "") if re.fullmatch(r"\d{1,2}", text) and int(text) > 0 else (False, "invalid container quantity"))

    if field == "Port PIN":
        return ((True, "") if re.fullmatch(r"[A-Z0-9-]{4,20}", text, re.I) else (False, "invalid port PIN"))

    if field == "TYPE":
        normalized = normalize_service_flow(text, default="")
        return (
            (True, "")
            if normalized in {"Import", "Export", "Local Import", "Local Export"}
            else (False, "unsupported service flow")
        )

    return True, ""


def _add_pattern_candidates(
    candidates: list[FieldCandidate],
    *,
    field: str,
    text: str,
    source: str,
    method: str,
    confidence: float,
    patterns: Iterable[str],
    flags: int = re.I | re.M,
) -> None:
    for pattern in patterns:
        for match in re.finditer(pattern, text, flags):
            value = match.group("value")
            candidates.append(
                _candidate(field, value, source, method, confidence, text, match.start(), match.end())
            )


def _extract_signature_candidates(signature: str) -> list[FieldCandidate]:
    candidates: list[FieldCandidate] = []
    lines = [(index, _clean(line)) for index, line in enumerate(signature.splitlines()) if _clean(line)]
    for index, line in lines:
        line_start = signature.find(line)
        if _EMAIL_RE.fullmatch(line):
            candidates.append(_candidate("Contact Email", line, "email_signature", "signature_email", 0.96, signature, line_start, line_start + len(line)))
        phone = _PHONE_RE.search(line)
        if phone:
            candidates.append(_candidate("Contact Phone", phone.group(1), "email_signature", "signature_phone", 0.96, signature, line_start, line_start + len(line)))
        if _JOB_TITLE_RE.search(line) and not _COMPANY_SUFFIX_RE.search(line):
            candidates.append(_candidate("Contact Title", line, "email_signature", "signature_title", 0.94, signature, line_start, line_start + len(line)))
            continue
        if _COMPANY_SUFFIX_RE.search(line):
            candidates.append(_candidate("Contact Company", line, "email_signature", "signature_company", 0.95, signature, line_start, line_start + len(line)))
            candidates.append(_candidate("Customer", line, "email_signature", "signature_company", 0.86, signature, line_start, line_start + len(line)))
            continue
        if not any(candidate.field == "Contact Name" and candidate.valid for candidate in candidates):
            candidates.append(_candidate("Contact Name", line, "email_signature", "signature_name", 0.90, signature, line_start, line_start + len(line)))
    return candidates


def generate_field_candidates(
    *,
    subject: str = "",
    newest_message: str = "",
    signature: str = "",
    quoted_history: str = "",
    sender: str = "",
    document_text: str = "",
) -> list[FieldCandidate]:
    candidates: list[FieldCandidate] = []
    sources = [
        ("email_subject", str(subject or "")),
        ("email_body", str(newest_message or "")),
        ("quoted_history", str(quoted_history or "")),
        ("document", str(document_text or "")),
    ]

    for source, text in sources:
        if not text:
            continue
        source_confidence = 0.96 if source == "document" else (0.92 if source == "email_body" else 0.78)
        if source == "quoted_history":
            source_confidence = 0.45

        _add_pattern_candidates(
            candidates,
            field="Booking Number",
            text=text,
            source=source,
            method="booking_label",
            confidence=source_confidence,
            patterns=[
                r"^[ \t]*(?:booking(?:[ \t]+(?:number|no\.?|#|ref(?:erence)?))?|bkg|reserva)[ \t]*[:#-][ \t]*(?P<value>[A-Z0-9][A-Z0-9._/-]{3,44})[ \t]*$",
                r"\b(?:booking|reserva)(?:[ \t]+(?:number|no\.?|#|ref(?:erence)?|n[uú]mero))?[ \t]+(?P<value>[A-Z0-9][A-Z0-9._/-]{3,44})\b",
            ],
        )
        _add_pattern_candidates(
            candidates,
            field="Reference Number",
            text=text,
            source=source,
            method="document_label" if source == "document" else "reference_label",
            confidence=source_confidence,
            patterns=[
                r"(?<!\w)(?:customer[ \t]+ref(?:erence)?|reference|referencia|ref|po|order|orden|shipment)"
                r"[ \t]*(?:number|no\.?|#)?(?:[ \t]*[:#-][ \t]*|[ \t]+)"
                r"(?P<value>[A-Z0-9][A-Z0-9._/-]*(?:[ \t]+[A-Z0-9][A-Z0-9._/-]*){0,2})",
            ],
        )
        for match in _CONTAINER_RE.finditer(text):
            candidates.append(_candidate("Container Number", match.group(0).upper(), source, "container_pattern", source_confidence, text, match.start(), match.end()))
        _add_pattern_candidates(
            candidates,
            field="Size",
            text=text,
            source=source,
            method="equipment_pattern",
            confidence=source_confidence - 0.03,
            patterns=[r"\b(?P<value>(?:20|40|45)\s*'?\s*(?:FT|HC|HQ|GP|RF|STD|DV))\b"],
        )
        _add_pattern_candidates(
            candidates,
            field="Customer",
            text=text,
            source=source,
            method="document_label" if source == "document" else "customer_label",
            confidence=source_confidence,
            patterns=[r"^\s*(?:customer|customer\s+name|cliente|account|bill\s+to|local\s+client)\s*[:#-]\s*(?P<value>[^\r\n]+)$"],
        )
        _add_pattern_candidates(
            candidates,
            field="Warehouse",
            text=text,
            source=source,
            method="warehouse_label",
            confidence=source_confidence,
            patterns=[
                r"^\s*(?:warehouse|almac[eé]n|bodega|delivery\s+warehouse|pickup\s+warehouse|loading\s+at)\s*[:#-]\s*(?P<value>[^\r\n]+)$",
                r"\bat\s+our\s+(?P<value>[A-Z][A-Za-z0-9&'. -]{1,60}?\s+warehouse)\b",
                r"\bfrom\s+the\s+(?P<value>[A-Z][A-Za-z0-9&'. -]{1,60}?(?:crossdock|warehouse|facility|yard))\b",
                r"\bdeliver\s+to\s+(?:the\s+)?(?P<value>[A-Z][A-Za-z0-9&'. -]{1,60}?\s+warehouse)\b",
            ],
        )
        _add_pattern_candidates(
            candidates,
            field="Port",
            text=text,
            source=source,
            method="port_context",
            confidence=source_confidence,
            patterns=[
                r"^\s*(?:port|puerto|terminal|export\s+terminal|pickup\s+terminal|full\s+return)\s*[:#-]\s*(?P<value>[^\r\n]+)$",
                r"\b(?:return|deliver)(?:\s+the\s+loaded\s+container)?\s+to\s+(?P<value>(?:Barbours\s+Cut|Bayport)(?:\s+Container\s+Terminal|\s+Terminal)?)\b",
                r"\b(?P<value>(?:Barbours\s+Cut|Bayport)\s+Container\s+Terminal)\b",
            ],
        )
        _add_pattern_candidates(
            candidates,
            field="Port PIN",
            text=text,
            source=source,
            method="document_label" if source == "document" else "port_pin_label",
            confidence=source_confidence,
            patterns=[r"^\s*(?:port\s+pin|pin)\s*[:#-]\s*(?P<value>[A-Z0-9-]{4,20})\s*$"],
        )
        _add_pattern_candidates(
            candidates,
            field="Address",
            text=text,
            source=source,
            method="address_label",
            confidence=source_confidence,
            patterns=[r"^\s*(?:address|delivery\s+address|warehouse\s+address|pickup\s+address)\s*[:#-]\s*(?P<value>[^\r\n]+)$"],
        )

        lowered = text.lower()
        flow = ""
        if "local import" in lowered:
            flow = "Local Import"
        elif "local export" in lowered:
            flow = "Local Export"
        elif "importación local" in lowered or "importacion local" in lowered:
            flow = "Local Import"
        elif "exportación local" in lowered or "exportacion local" in lowered:
            flow = "Local Export"
        elif "exportación" in lowered or "exportacion" in lowered:
            flow = "Export"
        elif "importación" in lowered or "importacion" in lowered:
            flow = "Import"
        elif re.search(r"\bexport\b", lowered):
            flow = "Export"
        elif re.search(r"\bimport\b", lowered):
            flow = "Import"
        if flow:
            start = lowered.find(flow.lower())
            candidates.append(_candidate("TYPE", flow, source, "service_flow_phrase", source_confidence - 0.04, text, start, start + len(flow)))

    candidates.extend(_extract_signature_candidates(signature))

    sender_email = _EMAIL_RE.search(sender or "")
    if sender_email:
        candidates.append(_candidate("Contact Email", sender_email.group(0).lower(), "sender_identity", "sender_email", 0.98, sender, sender_email.start(), sender_email.end()))
        display = _clean((sender or "")[: sender_email.start()].strip("<>\"' "))
        if display:
            candidates.append(_candidate("Contact Name", display, "sender_identity", "sender_name", 0.96, sender, 0, sender_email.start()))

    containers = []
    for candidate in candidates:
        if candidate.field == "Container Number" and candidate.valid and candidate.value not in containers:
            containers.append(candidate.value)
    if containers:
        joined = ", ".join(containers)
        candidates.append(
            FieldCandidate(
                field="Container Numbers",
                value=containers,
                source="all_sources",
                method="container_collection",
                confidence=max(c.confidence for c in candidates if c.field == "Container Number" and c.valid),
                evidence=joined,
                valid=True,
                validation_status="valid",
            )
        )
    return candidates


def _source_rank(field: str, source: str) -> int:
    if field == "Customer":
        return {
            "document": 60,
            "email_body": 50,
            "email_signature": 30,
            "sender_identity": 20,
            "quoted_history": 5,
        }.get(source, 10)
    if field in {"Contact Name", "Contact Title", "Contact Company", "Contact Email", "Contact Phone"}:
        return {
            "sender_identity": 60,
            "email_signature": 55,
            "email_body": 35,
            "document": 20,
            "quoted_history": 5,
        }.get(source, 10)
    return {
        "document": 55,
        "email_body": 50,
        "email_subject": 35,
        "email_signature": 15,
        "sender_identity": 15,
        "quoted_history": 5,
        "all_sources": 50,
    }.get(source, 10)


def select_field_candidates(candidates: list[FieldCandidate]) -> dict[str, Any]:
    selected: dict[str, Any] = {}
    for field in SHARED_OPERATION_FIELDS:
        valid = [
            candidate
            for candidate in candidates
            if candidate.field == field and candidate.valid and candidate.source != "quoted_history"
        ]
        if not valid:
            continue
        best = max(valid, key=lambda item: (_source_rank(field, item.source), item.confidence))
        selected[field] = best.value
    if selected.get("TYPE"):
        selected["TYPE"] = normalize_service_flow(selected["TYPE"], default=selected["TYPE"])
    return selected


def _normalized_equivalence(field: str, value: Any) -> str:
    if isinstance(value, list):
        return "|".join(sorted(_clean(item).upper() for item in value))
    text = _clean(value).lower()
    if field in {"Port", "Terminal", "Warehouse"}:
        text = re.sub(r"\b(?:container\s+terminal|terminal|warehouse|facility)\b", "", text)
    if field == "Contact Phone":
        text = re.sub(r"\D", "", text)
    return re.sub(r"[^a-z0-9]+", "", text)


def values_equivalent(field: str, left: Any, right: Any) -> bool:
    return bool(left and right and _normalized_equivalence(field, left) == _normalized_equivalence(field, right))


def _is_generic_warehouse(value: Any) -> bool:
    text = _clean(value)
    return bool(
        re.fullmatch(r"(?:our\s+)?[A-Za-z]+(?:\s+[A-Za-z]+){0,2}\s+warehouse", text, re.I)
        and not _COMPANY_SUFFIX_RE.search(text)
    )


def extract_operational_fields(
    *,
    subject: str = "",
    newest_message: str = "",
    signature: str = "",
    quoted_history: str = "",
    sender: str = "",
    document_text: str = "",
) -> dict[str, Any]:
    candidates = generate_field_candidates(
        subject=subject,
        newest_message=newest_message,
        signature=signature,
        quoted_history=quoted_history,
        sender=sender,
        document_text=document_text,
    )
    selected = select_field_candidates(candidates)
    candidate_conflicts = []
    for field in SHARED_OPERATION_FIELDS:
        eligible = [
            candidate
            for candidate in candidates
            if candidate.field == field and candidate.valid and candidate.source != "quoted_history"
        ]
        if not eligible:
            continue
        top_rank = max(_source_rank(field, candidate.source) for candidate in eligible)
        top_values = {
            _normalized_equivalence(field, candidate.value)
            for candidate in eligible
            if _source_rank(field, candidate.source) == top_rank
        }
        if len(top_values) > 1:
            candidate_conflicts.append(field)
    confidence_values = [
        candidate.confidence
        for candidate in candidates
        if candidate.valid and selected.get(candidate.field) == candidate.value
    ]
    confidence = sum(confidence_values) / len(confidence_values) if confidence_values else 0.0
    return {
        "fields": selected,
        "candidates": [candidate.to_dict() for candidate in candidates],
        "confidence": round(confidence, 3),
        "conflicts": candidate_conflicts,
    }


def _candidate_validation(parsed: dict[str, Any], field: str, value: Any, source: str) -> tuple[bool, str]:
    for candidate in parsed.get("_field_candidates", []) if isinstance(parsed, dict) else []:
        if candidate.get("field") == field and candidate.get("value") == value:
            return bool(candidate.get("valid")), str(candidate.get("rejection_reason") or "")
    return validate_field_value(field, value, source=source, method="parsed_value", evidence="")


def required_fields_for_flow(parsed: dict[str, Any]) -> list[str]:
    flow = normalize_service_flow(_clean(parsed.get("TYPE") or parsed.get("Service Flow")), default="")
    required = ["TYPE", "Customer", "Booking Number"]
    if flow in {"Import", "Export"}:
        required.append("Port")
    if flow in {"Local Import", "Local Export"}:
        return required
    return required


def derive_review_state(
    parsed: dict[str, Any],
    *,
    conflicts: list[str] | None = None,
    parser_failures: list[str] | None = None,
    low_confidence_threshold: float = 0.65,
) -> dict[str, Any]:
    conflicts = list(conflicts or [])
    parser_failures = list(parser_failures or [])
    missing = []
    invalid = []
    for field in required_fields_for_flow(parsed):
        value = parsed.get(field)
        valid, _ = validate_field_value(field, value, source="final", method="parsed_value")
        if not value:
            missing.append(field)
        elif not valid:
            invalid.append(field)
    has_container = bool(parsed.get("Container Number") or parsed.get("Container Numbers") or parsed.get("Container Qty"))
    if not has_container:
        missing.append("Container Number")

    confidence = float(parsed.get("_confidence", 0) or 0)
    if not confidence:
        checked = 0
        valid_count = 0
        for field in SHARED_OPERATION_FIELDS:
            if parsed.get(field) in (None, "", []):
                continue
            checked += 1
            if validate_field_value(field, parsed.get(field), source="final", method="parsed_value")[0]:
                valid_count += 1
        confidence = (valid_count / checked) if checked else 0.0
    if conflicts:
        confidence = min(confidence, 0.55)
    if invalid:
        confidence = min(confidence, 0.45)
    if missing:
        confidence = min(confidence, 0.64)
    if parser_failures:
        confidence = min(confidence, 0.40)

    needs_review = bool(
        conflicts
        or missing
        or invalid
        or parser_failures
        or confidence < low_confidence_threshold
    )
    return {
        "needs_review": needs_review,
        "confidence": round(confidence, 3),
        "conflicts": conflicts,
        "missing_required_fields": missing,
        "invalid_selected_fields": invalid,
        "parser_failures": parser_failures,
    }


def reconcile_parsed_sources(
    email_values: dict[str, Any] | None,
    document_values: dict[str, Any] | None,
    *,
    fields: list[str] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[str]]:
    email_values = dict(email_values or {})
    document_values = dict(document_values or {})
    fields = fields or SHARED_OPERATION_FIELDS
    final = {
        key: value
        for key, value in email_values.items()
        if key.startswith("_") or key not in fields
    }
    rows: list[dict[str, Any]] = []
    conflicts: list[str] = []

    for field in fields:
        email_value = email_values.get(field, "")
        document_value = document_values.get(field, "")
        email_valid, email_reason = _candidate_validation(email_values, field, email_value, "email")
        document_valid, document_reason = _candidate_validation(document_values, field, document_value, "document")

        if field == "Dispatcher Notes":
            email_valid = bool(_clean(email_value))
            document_valid = bool(_clean(document_value))

        if email_valid and document_valid and values_equivalent(field, email_value, document_value):
            if field in {"Port", "Terminal", "Warehouse"} and len(_clean(document_value)) > len(_clean(email_value)):
                final_value = document_value
            else:
                final_value = email_value
            status = "Confirmed"
        elif field == "Warehouse" and email_valid and document_valid and _is_generic_warehouse(email_value):
            final_value = document_value
            status = "Document refined email location"
        elif email_valid and document_valid:
            if field == "Dispatcher Notes":
                final_value = email_value if _clean(document_value) in _clean(email_value) else f"{_clean(email_value)}\n{_clean(document_value)}"
                status = "Combined"
            else:
                final_value = document_value
                status = "Review mismatch"
                conflicts.append(field)
        elif document_valid:
            final_value = document_value
            status = "Document corrected invalid email value" if email_value else "Document supplied"
        elif email_valid:
            final_value = email_value
            status = "Email retained"
        else:
            final_value = ""
            status = "Rejected" if email_value or document_value else "Blank"

        final[field] = final_value
        rows.append(
            {
                "Field": field,
                "Email Body": email_value,
                "Document": document_value,
                "Final Value": final_value,
                "Status": status,
                "Email Valid": email_valid,
                "Document Valid": document_valid,
                "Email Rejection": email_reason if email_value and not email_valid else "",
                "Document Rejection": document_reason if document_value and not document_valid else "",
            }
        )

    final["_reconciliation"] = {
        "conflicts": conflicts,
        "rows": rows,
    }
    source_confidences = []
    for source_values in (email_values, document_values):
        try:
            source_confidences.append(float(source_values.get("_confidence", 0) or 0))
        except (TypeError, ValueError):
            continue
    if source_confidences:
        final["_confidence"] = max(source_confidences)
    all_conflicts = list(dict.fromkeys([*conflicts, *(final.get("_candidate_conflicts") or [])]))
    review = derive_review_state(final, conflicts=all_conflicts)
    final["_needs_review"] = review["needs_review"]
    final["_confidence"] = review["confidence"]
    final["_review_reasons"] = review
    return final, rows, conflicts
