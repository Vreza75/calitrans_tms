import os
import imaplib
import email
from email.header import decode_header
from email.utils import parsedate_to_datetime
from dotenv import load_dotenv

try:
    import streamlit as st
except Exception:
    st = None

load_dotenv()


def get_setting(name, default=None):
    value = os.getenv(name)
    if value:
        return value
    if st is not None:
        try:
            value = st.secrets.get(name)
            if value:
                return value
        except Exception:
            pass
    return default


def decode_text(value):
    if not value:
        return ""
    decoded_parts = decode_header(value)
    result = ""
    for part, encoding in decoded_parts:
        if isinstance(part, bytes):
            result += part.decode(encoding or "utf-8", errors="ignore")
        else:
            result += part
    return result


def get_email_body(msg):
    body = ""
    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            disposition = str(part.get("Content-Disposition"))
            if content_type == "text/plain" and "attachment" not in disposition:
                payload = part.get_payload(decode=True)
                if payload:
                    body += payload.decode(errors="ignore")
            elif content_type == "text/html" and "attachment" not in disposition and not body:
                payload = part.get_payload(decode=True)
                if payload:
                    import re
                    html = payload.decode(errors="ignore")
                    body += re.sub("<[^<]+?>", " ", html)
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            body = payload.decode(errors="ignore")
    return body


def get_pdf_attachments(msg):
    attachments = []
    for part in msg.walk():
        filename = part.get_filename()
        if filename:
            filename = decode_text(filename)
        if filename and filename.lower().endswith(".pdf"):
            attachments.append({"filename": filename, "content": part.get_payload(decode=True)})
    return attachments


def _parse_email_date(value):
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
        if parsed is None:
            return None
        return parsed.isoformat()
    except Exception:
        return None


def _fetch_recent_emails(
    *,
    limit=10,
    search_query="ALL",
    terms=None,
    scan_window=None,
    mailbox=None,
):
    email_address = get_setting("EMAIL_ADDRESS")
    email_password = get_setting("EMAIL_APP_PASSWORD")
    imap_server = get_setting("EMAIL_IMAP_SERVER", "imap.gmail.com")
    selected_mailbox = mailbox or get_setting("EMAIL_INBOX_FOLDER", "inbox")

    if not email_address:
        raise ValueError("EMAIL_ADDRESS is missing from .streamlit/secrets.toml or .env")
    if not email_password:
        raise ValueError("EMAIL_APP_PASSWORD is missing from .streamlit/secrets.toml or .env")

    mail = imaplib.IMAP4_SSL(imap_server)
    mail.login(email_address, email_password)
    mail.select(selected_mailbox)

    status, data = mail.search(None, search_query or "ALL")
    if status != "OK" and search_query != "ALL":
        status, data = mail.search(None, "ALL")

    if status != "OK" or not data or not data[0]:
        mail.logout()
        return []

    email_ids = data[0].split()
    candidate_count = scan_window or max(limit * 4, limit)
    candidate_ids = email_ids[-candidate_count:]
    normalized_terms = [term.lower() for term in (terms or []) if str(term or "").strip()]
    results = []

    for email_id in reversed(candidate_ids):
        status, msg_data = mail.fetch(email_id, "(RFC822)")
        if status != "OK" or not msg_data or not msg_data[0]:
            continue

        raw_email = msg_data[0][1]
        msg = email.message_from_bytes(raw_email)
        subject = decode_text(msg.get("Subject"))
        sender = decode_text(msg.get("From"))
        body = get_email_body(msg)

        if normalized_terms:
            haystack = f"{subject}\n{sender}\n{body}".lower()
            if not any(term in haystack for term in normalized_terms):
                continue

        results.append({
            "id": email_id.decode(),
            "message_id": decode_text(msg.get("Message-ID")) or email_id.decode(),
            "subject": subject,
            "from": sender,
            "to": decode_text(msg.get("To")),
            "date": decode_text(msg.get("Date")),
            "received_at": _parse_email_date(msg.get("Date")),
            "body": body,
            "snippet": body[:300],
            "attachments": get_pdf_attachments(msg),
        })

        if len(results) >= limit:
            break

    mail.logout()
    return results


def fetch_recent_load_emails(limit=10):
    return _fetch_recent_emails(
        limit=limit,
        search_query=get_setting("EMAIL_LOAD_IMAP_SEARCH", '(OR SUBJECT "Booking" SUBJECT "Load")'),
        terms=["booking", "load", "delivery order", "container"],
        scan_window=max(limit * 3, limit),
    )


def fetch_recent_operations_emails(limit=25):
    terms = get_setting(
        "EMAIL_OPERATIONS_TERMS",
        "quote,rate,pricing,missing info,missing,load,booking,container,appointment,pod,cancel,delivery",
    )
    search_terms = [term.strip() for term in str(terms).split(",") if term.strip()]

    return _fetch_recent_emails(
        limit=limit,
        search_query=get_setting("EMAIL_OPERATIONS_IMAP_SEARCH", "ALL"),
        terms=search_terms,
        scan_window=max(limit * 5, 75),
    )
