import os
import imaplib
import email
import re
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


def _decode_payload(part):
    payload = part.get_payload(decode=True)
    if not payload:
        return ""

    charset = part.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset, errors="ignore")
    except Exception:
        return payload.decode("utf-8", errors="ignore")


def get_email_body(msg):
    plain_body = ""
    html_body = ""

    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            disposition = str(part.get("Content-Disposition"))
            if content_type == "text/plain" and "attachment" not in disposition:
                plain_body += _decode_payload(part)
            elif content_type == "text/html" and "attachment" not in disposition:
                html_body += _decode_payload(part)
    else:
        if msg.get_content_type() == "text/html":
            html_body = _decode_payload(msg)
        else:
            plain_body = _decode_payload(msg)

    if plain_body.strip():
        return plain_body.strip()

    return re.sub(r"<[^<]+?>", " ", html_body).strip()


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


def _get_int_setting(name, default):
    try:
        return int(get_setting(name, str(default)))
    except Exception:
        return int(default)


def _select_mailbox(mail, selected_mailbox):
    attempted = []
    candidates = [
        selected_mailbox,
        "INBOX",
        "Inbox",
        "inbox",
    ]

    for mailbox in candidates:
        if not mailbox or mailbox in attempted:
            continue

        attempted.append(mailbox)
        status, data = mail.select(mailbox)
        if status == "OK":
            return mailbox

    raise ValueError(f"Could not open Yahoo inbox folder. Tried: {', '.join(attempted)}")


def _search_message_ids(mail, search_query):
    query = search_query or "ALL"
    status, data = mail.search(None, query)

    if status == "OK" and data and data[0]:
        return data[0].split()

    if query != "ALL":
        status, data = mail.search(None, "ALL")
        if status == "OK" and data and data[0]:
            return data[0].split()

    return []


def _fetch_recent_emails(
    *,
    limit=10,
    search_query="ALL",
    terms=None,
    scan_window=None,
    mailbox=None,
    require_terms=False,
):
    email_address = get_setting("YAHOO_EMAIL") or get_setting("EMAIL_ADDRESS")
    email_password = get_setting("YAHOO_APP_PASSWORD") or get_setting("EMAIL_APP_PASSWORD")
    imap_server = get_setting("IMAP_SERVER", "imap.mail.yahoo.com")
    imap_port = _get_int_setting("IMAP_PORT", 993)
    selected_mailbox = mailbox or get_setting("EMAIL_INBOX_FOLDER", "INBOX")

    if not email_address:
        raise ValueError("YAHOO_EMAIL is missing from .streamlit/secrets.toml")
    if not email_password:
        raise ValueError("YAHOO_APP_PASSWORD is missing from .streamlit/secrets.toml")

    mail = imaplib.IMAP4_SSL(imap_server, imap_port)
    try:
        mail.login(email_address, email_password)
        selected_mailbox = _select_mailbox(mail, selected_mailbox)

        email_ids = _search_message_ids(mail, search_query)
        if not email_ids:
            return []

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
            haystack = f"{subject}\n{sender}\n{body}".lower()
            matched_terms = [term for term in normalized_terms if term in haystack]

            if require_terms and normalized_terms and not matched_terms:
                continue

            results.append({
                "id": email_id.decode(),
                "message_id": decode_text(msg.get("Message-ID")) or email_id.decode(),
                "mailbox": selected_mailbox,
                "subject": subject,
                "from": sender,
                "to": decode_text(msg.get("To")),
                "date": decode_text(msg.get("Date")),
                "received_at": _parse_email_date(msg.get("Date")),
                "body": body,
                "snippet": body[:300],
                "matched_terms": matched_terms,
                "attachments": get_pdf_attachments(msg),
            })

            if len(results) >= limit:
                break

        return results
    finally:
        try:
            mail.logout()
        except Exception:
            pass


def fetch_recent_load_emails(limit=10):
    return _fetch_recent_emails(
        limit=limit,
        search_query=get_setting("EMAIL_LOAD_IMAP_SEARCH", "ALL"),
        terms=["booking", "load", "delivery order", "container"],
        scan_window=max(limit * 8, 150),
        require_terms=False,
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
        scan_window=max(limit * 10, 250),
        require_terms=False,
    )
