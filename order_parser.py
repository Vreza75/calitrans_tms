import re
import pdfplumber


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
        "Port": "",
        "Warehouse": "",
        "Document Cutoff": "",
        "Delivery Need Date": "",
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
            "Date": find_pattern(text, [
                r"Ready Date:\s*([^\n]+)",
            ]),
            "Delivery Need Date": find_pattern(text, [
                r"Ready Date:\s*([^\n]+)",
            ]),
            "Warehouse": find_pattern(text, [
                r"Pickup Full Information:.*?Name:\s*([^\n]+)",
                r"Name:\s*([^\n]+)\s+Contact:",
            ]),
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
            r"Customer[:\s]+(.+)",
            r"Consignee[:\s]+(.+)",
            r"(Flat\s*World\s*Global\s*Logistics)",
        ]),
        "Port": find_pattern(text, [
            r"Port[:\s]+(.+)",
            r"Terminal[:\s]+(.+)",
            r"Port of Lading:\s*([^\n]+)",
        ]),
        "Warehouse": find_pattern(text, [
            r"Warehouse[:\s]+(.+)",
            r"Delivery Location[:\s]+(.+)",
        ]),
        "Document Cutoff": find_pattern(text, [
            r"Document Cutoff[:\s]+(.+)",
            r"Doc Cutoff[:\s]+(.+)",
            r"Port Cut-Off Date:\s*([^\n]+)",
        ]),
        "Dispatcher Notes": "Parsed from generic order PDF",
    })

    if corrected_container:
        parsed["Container Number"] = corrected_container
        parsed["Dispatcher Notes"] = _append_note(
            parsed.get("Dispatcher Notes", ""),
            f"Container correction noted: use {corrected_container} instead of {replaced_container}.",
        )

    if not parsed.get("Customer") and re.search(r"\bflat\s*world\b|@flatworldgs\.com\b", text or "", re.IGNORECASE):
        parsed["Customer"] = "Flat World Global Logistics"

    return parsed
def find_pattern(text, patterns):
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
        if match:
            return match.group(1).strip()
    return ""
