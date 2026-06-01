import os
import imaplib
import email
from email.header import decode_header
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


def fetch_recent_load_emails(limit=10):
    email_address = get_setting("EMAIL_ADDRESS")
    email_password = get_setting("EMAIL_APP_PASSWORD")
    imap_server = get_setting("EMAIL_IMAP_SERVER", "imap.gmail.com")

    if not email_address:
        raise ValueError("EMAIL_ADDRESS is missing from .streamlit/secrets.toml or .env")
    if not email_password:
        raise ValueError("EMAIL_APP_PASSWORD is missing from .streamlit/secrets.toml or .env")

    mail = imaplib.IMAP4_SSL(imap_server)
    mail.login(email_address, email_password)
    mail.select("inbox")

    search_query = '(OR SUBJECT "Booking" SUBJECT "Load")'
    status, data = mail.search(None, search_query)
    if status != "OK" or not data or not data[0]:
        mail.logout()
        return []

    email_ids = data[0].split()[-limit:]
    results = []

    for email_id in reversed(email_ids):
        status, msg_data = mail.fetch(email_id, "(RFC822)")
        if status != "OK" or not msg_data or not msg_data[0]:
            continue
        raw_email = msg_data[0][1]
        msg = email.message_from_bytes(raw_email)
        body = get_email_body(msg)
        results.append({
            "id": email_id.decode(),
            "subject": decode_text(msg.get("Subject")),
            "from": decode_text(msg.get("From")),
            "body": body,
            "snippet": body[:300],
            "attachments": get_pdf_attachments(msg),
        })

    mail.logout()
    return results
