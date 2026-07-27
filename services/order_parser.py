import logging
import re
import pdfplumber

from services.email_parser import parse_email_text

logger = logging.getLogger(__name__)


def _append_note(existing, note):
    existing = str(existing or "").strip()
    note = str(note or "").strip()
    if not note:
        return existing
    return note if not existing else f"{existing}; {note}"


def _container_correction(text):
    match = re.search(
        r"\bcontainer\s+([A-Z]{4}\d{7})\b.{0,80}?\b(?:instead\s+of|not|rather\s+than)\s+([A-Z]{4}\d{7})\b",
        text or "",
        re.IGNORECASE | re.DOTALL,
    )
    if match:
        return match.group(1).upper(), match.group(2).upper()
    match = re.search(
        r"\b([A-Z]{4}\d{7})\b.{0,80}?\b(?:instead\s+of|not|rather\s+than)\s+([A-Z]{4}\d{7})\b",
        text or "",
        re.IGNORECASE | re.DOTALL,
    )
    if match:
        return match.group(1).upper(), match.group(2).upper()
    return "", ""


def extract_text_from_pdf(uploaded_file):
    text = ""

    uploaded_file.seek(0)

    with pdfplumber.open(uploaded_file) as pdf:
        for page in pdf.pages:
            page_text = page.extract_text()
            if page_text:
                text += page_text + "\n"

    uploaded_file.seek(0)
    return text.strip()


def _flat_world_section_address(text, section_label):
    """Flat World's rate confirmation wraps a location's street address and
    city/state/zip across two lines, each with unrelated trailing text
    (Phone:/a ready-time fragment) that a single-line pattern would either
    swallow or stop short of. section_label scopes the match to one
    location block (e.g. "Pickup Full Information") since the document
    repeats "Address:" for each location."""
    match = re.search(
        re.escape(section_label) + r":.*?Address:\s*([^\n]+?)\s+Phone:[^\n]*\n\s*([A-Za-z][^\n]*?,\s*[A-Z]{2}\s+\d{5})",
        text,
        re.IGNORECASE | re.DOTALL,
    )
    if not match:
        return ""
    street = match.group(1).strip()
    city_state_zip = match.group(2).strip()
    if street and city_state_zip:
        return f"{street}, {city_state_zip}"
    return street or city_state_zip


def parse_order_text(text):
    is_gmt = "Global Marine Transportation" in text or "GMT Work order" in text
    is_flat_world = "Flat World" in text or "DRAYAGE RATE CONFIRMATION" in text
    is_cma_cgm = "CMA CGM" in text and "Booking Confirmation" in text

    parsed = {
        "TYPE": "",
        "Date": "",
        "Booking Number": "",
        "Reference Number": "",
        "Container Number": "",
        "Customer": "",
        "Size": "",
        "Port": "",
        "Warehouse": "",
        "Address": "",
        "Document Cutoff": "",
        "Delivery Need Date": "",
        "LFD": "",
        "Dispatcher Notes": "",
        "Status": "New",
    }

    if is_gmt:
        parsed.update({
            "TYPE": "Export",
            "Customer": "Global Marine Transportation",
            "Booking Number": find_pattern(text, [
                r"SS Line Booking No\.:\s*([^\n]+)",
            ]),
            "Reference Number": find_pattern(text, [
                r"Shippers Ref:\s*([^\n]+)",
                r"Load Facility Ref:\s*([^\n]+)",
                r"GMT Work order #:\s*([^\n]+)",
            ]),
            "Date": find_pattern(text, [
                r"Order Date:\s*([^\n]+)",
            ]),
            "Delivery Need Date": find_pattern(text, [
                r"Est\. Load Date\s*([^\n]+)",
            ]),
            "Document Cutoff": find_pattern(text, [
                r"Port Cut-Off Date:\s*([^\n]+)",
            ]),
            "Port": find_pattern(text, [
                r"Port of Lading:\s*([^\n]+)",
            ]),
            "Warehouse": find_pattern(text, [
                r"FIRST PICK-UP LOCATION:\s*-+\s*([^\n]+)",
            ]),
            "Dispatcher Notes": "Parsed from GMT Work Order",
        })
        return parsed

    if is_flat_world:
        ready_date = find_pattern(text, [
            r"Ready Date:\s*(\d{1,2}/\d{1,2}/\d{2,4})",
            r"Ready Date:\s*([^\n]+?)\s+Customer PO:",
            r"Ready Date:\s*([^\n]+)",
        ])
        parsed.update({
            "TYPE": "Import",
            "Customer": "Flat World Global Logistics",
            "Booking Number": find_pattern(text, [
                r"Load #:\s*([^\n]+)",
            ]),
            "Reference Number": find_pattern(text, [
                r"Customer PO:\s*([^\n]+)",
            ]),
            "Container Number": find_pattern(text, [
                r"Container #:\s*([A-Z]{4}\d{7})",
            ]),
            "Date": ready_date,
            "Delivery Need Date": ready_date,
            # "Pickup Full Information" is the origin the driver picks up
            # from; "Return Empty Information" is labeled for an empty
            # container return, but on this Local Import rate confirmation
            # it is actually the second (delivery) location. Port maps to
            # origin and Warehouse maps to destination downstream, so pickup
            # goes to Port and the second location goes to Warehouse - not
            # the reverse.
            "Port": find_pattern(text, [
                r"Pickup Full Information:.*?Name:\s*([^\n]+?)\s+Contact:",
            ]),
            "Warehouse": find_pattern(text, [
                r"Return Empty Information:.*?Name:\s*([^\n]+?)\s+Contact:",
            ]),
            "Address": _flat_world_section_address(text, "Pickup Full Information"),
            "Dispatcher Notes": "Parsed from Flat World Carrier Confirmation",
        })
        return parsed
    if is_cma_cgm:
        vessel = find_pattern(text, [
            r"Vessel\s*\n([A-Z0-9\s]+)",
            r"HOUSTON,\s*TX.*?\d{2}-[A-Z]{3}-\d{4}\s+([A-Z\s]+)\s+[A-Z0-9]+",
        ])

        voyage = find_pattern(text, [
            r"Voyage\s*\n([A-Z0-9]+)",
            r"SWANSEA\s+([A-Z0-9]+)",
        ])

        size = find_pattern(text, [
            r"(\d+\s*x\s*40'?HC)",
        ])

        quote = find_pattern(text, [
            r"Quote:\s*([A-Z0-9]+)",
        ])

        parsed.update({
            "TYPE": "OTR Export",
            "Customer": find_pattern(text, [
                r"To:\s*([^\n]+)",
                r"Booking Party:\s*([^\n]+)",
            ]),
            "Booking Number": find_pattern(text, [
                r"Booking Number:\s*([A-Z0-9]+)",
                r"Booking Ref\.\s*([A-Z0-9]+)",
            ]),
            "Reference Number": quote,
            "Date": find_pattern(text, [
                r"Booking Confirmation Date:\s*([^\n]+)",
            ]),
            "Document Cutoff": find_pattern(text, [
                r"Port Cut-Off.*?(\d{2}-[A-Z]{3}-\d{4}\s+\d{2}:\d{2})",
            ]),
            "Port": "HOUSTON, TX",
            "Warehouse": find_pattern(text, [
                r"Preferred Depot:\s*([^\n]+(?:\n[^\n]+)?)",
                r"(BAYPORT CONTAINER\s+TERMINAL)",
            ]),
            "Dispatcher Notes": (
                "Parsed from CMA CGM Booking Confirmation\n"
                f"Quote: {quote}\n"
                f"Vessel: {vessel}\n"
                f"Voyage: {voyage}\n"
                f"Size: {size}"
            ),
            "Status": "New",
        })
        return parsed

    corrected_container, replaced_container = _container_correction(text)
    def extract_labeled_fields(text):
        fields = {}

        label_map = {
            "customer": "Customer",
            "booking": "Booking Number",
            "container": "Container Number",
            "origin": "Port",
            "port": "Port",
            "destination": "Warehouse",
            "warehouse": "Warehouse",
            "lfd": "LFD",
        }

        for line in text.splitlines():

            line = line.strip()

            if ":" not in line:
                continue

            key, value = line.split(":",1)

            key = key.lower().strip()

            value = value.strip()

            if key in label_map:
                fields[label_map[key]] = value

        return fields
    parsed.update({
        "Booking Number": find_pattern(text, [
            r"Booking Number[:\s]+([A-Z0-9\-]+)",
            r"Booking[:\s]+([A-Z0-9\-]+)",
            r"SS Line Booking No\.:\s*([^\n]+)",
            r"Load #:\s*([^\n]+)",
        ]),
        "Container Number": find_pattern(text, [
            r"Container Number[:\s]+([A-Z]{4}\d{7})",
            r"Container #:\s*([A-Z]{4}\d{7})",
            r"Container[:\s]+([A-Z]{4}\d{7})",
            r"\b([A-Z]{4}\d{7})\b",
        ]),
        "Reference Number": find_pattern(text, [
            r"Reference Number[:\s]+([A-Z0-9\-]+)",
            r"Reference #[:\s]+([A-Z0-9\-]+)",
            r"Ref[:#\s]+([A-Z0-9\-]+)",
            r"\b([0-9]{6,})\s*/\s*[A-Z]{4}\d{7}\b",
        ]),
        "Customer": find_pattern(text, [
            r"Customer:\s*([^\n]+)"
            r"Consignee[:\s]+(.+)",
            r"(Flat\s*World\s*Global\s*Logistics)",
        ]),
        "Port": find_pattern(text, [
            r"Port:\s*([^\n]+)"
            r"Terminal[:\s]+(.+)",
            r"Port of Lading:\s*([^\n]+)",
        ]),
        "Warehouse": find_pattern(text, [
            r"Warehouse:\s*([^\n]+)"
            r"Delivery Location[:\s]+(.+)",
            r"Destination[:\s]+(.+)",
            r"Deliver To[:\s]+(.+)",
        ]),
        "Address": find_pattern(text, [
            r"Delivery Address[:\s]+(.+)",
            r"Warehouse Address[:\s]+(.+)",
            r"Destination:\s*([^\n]+)",
        ]),
        "Size": find_pattern(text, [
            r"\b(?:\d+\s*x\s*)?((?:20|40|45)\s*'?\s*(?:ft|hc|hq|std)?)\b",
        ]),
        "Document Cutoff": find_pattern(text, [
            r"Document Cutoff[:\s]+(.+)",
            r"Doc Cutoff[:\s]+(.+)",
            r"Port Cut-Off Date:\s*([^\n]+)",
        ]),
        "LFD": find_pattern(text, [
            r"LFD:\s*([^\n]+)",
            r"Last Free Day[:\s]+(.+)",
        ]),
        "Dispatcher Notes": "Parsed from generic order PDF",
    })
    
    email_fields = extract_labeled_fields(text)
    # 1) Read clean labeled fields from email/PDF body.
    # These are high-confidence because they come from lines like:
    # Customer: USA Exports
    # Origin: Barbours Cut
    # Destination: ABC Distribution

    # 2) Run older email parser as fallback only.
    try:
        email_style_parsed = parse_email_text("", text)
    except Exception as exc:
        logger.exception(
            "email-style document fallback parser failed",
            extra={"stage": "parse_order_text_email_fallback"},
        )
        email_style_parsed = {}
        failures = list(parsed.get("_parser_failures") or [])
        failures.append(f"parse_order_text_email_fallback: {type(exc).__name__}")
        parsed["_parser_failures"] = failures

    for field, value in email_style_parsed.items():
        if field == "Dispatcher Notes":
            parsed["Dispatcher Notes"] = _append_note(parsed.get("Dispatcher Notes", ""), value)
        elif value and not parsed.get(field):
            parsed[field] = value

    # 3) FINAL OVERRIDE:
    # Clean labeled fields must win over regex / fallback parser.
    for field, value in email_fields.items():
        if value:
            parsed[field] = value

    # 4) Container correction should still win.
    if corrected_container:
        parsed["Container Number"] = corrected_container
        parsed["Dispatcher Notes"] = _append_note(
            parsed.get("Dispatcher Notes", ""),
            f"Container correction noted: use {corrected_container} instead of {replaced_container}.",
        )

    if not parsed.get("Customer") and re.search(r"\bflat\s*world\b|@flatworldgs\.com\b", text or "", re.IGNORECASE):
        parsed["Customer"] = "Flat World Global Logistics"

    # 5) Clean bad values caused by older regex parser.
    # Port/Warehouse should not contain multiple labels or full instructions.
    for clean_field in ["Port", "Warehouse"]:
        value = str(parsed.get(clean_field, "") or "")
        if "\n" in value or "Customer:" in value or "Booking:" in value or "Container:" in value or "Instructions:" in value:
            parsed[clean_field] = email_fields.get(clean_field, "")

    # 6) Infer TYPE using route direction.
    body = text.lower()
    port_value = str(parsed.get("Port", "") or "").lower()
    warehouse_value = str(parsed.get("Warehouse", "") or "").lower()

    import_ports = [
        "barbours cut",
        "bayport",
        "port houston",
        "terminal",
    ]

    if (
        "pickup from port" in body
        or "pickup from port houston" in body
        or any(p in port_value for p in import_ports)
    ) and parsed.get("Warehouse"):
        parsed["TYPE"] = "Import"

    elif (
        "deliver to port" in body
        or "return full" in body
        or "return to port" in body
        or "empty pickup" in body
        or "export booking" in body
    ):
        parsed["TYPE"] = "Export"

    elif parsed.get("Port") and parsed.get("Warehouse"):
        parsed["TYPE"] = "Import"


    return parsed
def find_pattern(text, patterns):
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
        if match:
            return match.group(1).strip()
    return ""
