import os
import imaplib
import email
import os
import re
from email.header import decode_header
from email.utils import parsedate_to_datetime

try:
    from dotenv import load_dotenv
except Exception:
    def load_dotenv(*args, **kwargs):
        return False

try:
    import streamlit as st
except Exception:
    st = None

load_dotenv()


MESSAGE_ID_RE = re.compile(r"<([^>]+)>")
SUBJECT_PREFIX_RE = re.compile(r"^\s*(?:re|fw|fwd)\s*:\s*", re.I)


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


def normalize_message_id(value):
    text = decode_text(value).strip()
    if not text:
        return ""
    match = MESSAGE_ID_RE.search(text)
    if match:
        return match.group(1).strip().lower()
    return text.strip("<> ").lower()


def parse_reference_ids(value):
    text = decode_text(value)
    if not text:
        return []
    matches = MESSAGE_ID_RE.findall(text)
    if matches:
        return [item.strip().lower() for item in matches if item.strip()]
    return [item.strip("<> ").lower() for item in text.split() if item.strip()]


def normalize_subject(value):
    text = decode_text(value)
    while SUBJECT_PREFIX_RE.match(text):
        text = SUBJECT_PREFIX_RE.sub("", text, count=1)
    text = re.sub(r"\s+", " ", text).strip().lower()
    return text


def derive_thread_id(message_id, in_reply_to, references):
    reference_ids = parse_reference_ids(references)
    if reference_ids:
        return reference_ids[0]

    reply_id = normalize_message_id(in_reply_to)
    if reply_id:
        return reply_id

    return normalize_message_id(message_id)


def derive_conversation_key(message_id, in_reply_to, references, subject):
    reference_ids = parse_reference_ids(references)
    if reference_ids:
        return reference_ids[0]

    reply_id = normalize_message_id(in_reply_to)
    if reply_id:
        return reply_id

    normalized_message_id = normalize_message_id(message_id)
    if normalized_message_id:
        return normalized_message_id

    normalized_subject = normalize_subject(subject)
    return f"subject:{normalized_subject}" if normalized_subject else ""


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


def _attachment_filename(part, index):
    filename = part.get_filename()
    if filename:
        return decode_text(filename)

    content_type = part.get_content_type() or "application/octet-stream"
    extension = {
        "application/pdf": ".pdf",
        "text/plain": ".txt",
        "text/csv": ".csv",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
        "application/msword": ".doc",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
        "image/jpeg": ".jpg",
        "image/png": ".png",
    }.get(content_type, "")
    return f"attachment_{index}{extension}"


def get_email_attachments(msg):
    attachments = []
    seen = set()
    save_inline_images = _get_bool_setting("OPERATIONS_SAVE_INLINE_IMAGES", False)
    for index, part in enumerate(msg.walk(), start=1):
        content_type = part.get_content_type() or "application/octet-stream"
        if content_type == "message/rfc822":
            payload = part.get_payload()
            nested_messages = payload if isinstance(payload, list) else []
            for nested in nested_messages:
                for nested_attachment in get_email_attachments(nested):
                    nested_content = nested_attachment.get("content") or b""
                    nested_key = (
                        str(nested_attachment.get("filename", "")).lower(),
                        str(nested_attachment.get("content_type", "")),
                        len(nested_content),
                        nested_content[:32],
                    )
                    if nested_key in seen:
                        continue
                    seen.add(nested_key)
                    attachments.append(nested_attachment)
            continue

        if part.is_multipart():
            continue

        disposition = str(part.get("Content-Disposition") or "").lower()
        filename = part.get_filename()
        inline_without_filename = "inline" in disposition and not filename
        inline_image = "inline" in disposition and content_type.startswith("image/") and "attachment" not in disposition
        has_attachment_marker = "attachment" in disposition or bool(filename)
        if inline_image and not save_inline_images:
            continue
        if inline_without_filename and content_type != "application/pdf":
            continue
        if not has_attachment_marker and content_type in {"text/plain", "text/html"}:
            continue

        content = part.get_payload(decode=True)
        if not content:
            continue

        filename = _attachment_filename(part, index)
        dedupe_key = (filename.lower(), content_type, len(content), content[:32])
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        attachments.append(
            {
                "filename": filename,
                "content": content,
                "content_type": content_type,
                "size_bytes": len(content),
                "disposition": disposition,
                "content_id": decode_text(part.get("Content-ID")),
            }
        )
    return attachments


def get_pdf_attachments(msg):
    return [
        attachment
        for attachment in get_email_attachments(msg)
        if str(attachment.get("filename", "")).lower().endswith(".pdf")
        or str(attachment.get("content_type", "")).lower() == "application/pdf"
    ]


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


def _get_bool_setting(name, default=False):
    value = str(get_setting(name, str(default))).strip().lower()
    return value in {"1", "true", "yes", "on"}


def _split_mailbox_candidates(value):
    return [item.strip() for item in str(value or "").split(",") if item.strip()]


def _split_email_accounts(value):
    return [item.strip() for item in str(value or "").replace(";", ",").split(",") if item.strip()]


def _account_setting_suffix(email_address):
    local_part = str(email_address or "").split("@", 1)[0]
    return re.sub(r"[^A-Za-z0-9]+", "_", local_part).strip("_").upper()


def _password_for_email_account(email_address):
    suffix = _account_setting_suffix(email_address)
    candidates = [
        f"OPERATIONS_EMAIL_PASSWORD_{suffix}",
        f"EMAIL_APP_PASSWORD_{suffix}",
        f"YAHOO_APP_PASSWORD_{suffix}",
        f"SMTP_PASSWORD_{suffix}",
    ]
    for key in candidates:
        value = get_setting(key)
        if value:
            return value

    default_email = (get_setting("YAHOO_EMAIL") or get_setting("EMAIL_ADDRESS") or "").lower()
    if str(email_address or "").lower() == default_email:
        return get_setting("YAHOO_APP_PASSWORD") or get_setting("EMAIL_APP_PASSWORD")
    return ""


def _operations_email_accounts():
    raw_accounts = (
        get_setting("OPERATIONS_CASE_MAILBOXES")
        or get_setting("OPERATIONS_EMAIL_ACCOUNTS")
        or get_setting(
            "OPERATIONS_SHARED_MAILBOXES",
            "margiea@calitranscorp.com,dispatch@calitranscorp.com,accounting@calitranscorp.com",
        )
    )
    configured_accounts = _split_email_accounts(raw_accounts)
    default_email = get_setting("YAHOO_EMAIL") or get_setting("EMAIL_ADDRESS")
    if default_email and default_email not in configured_accounts:
        configured_accounts.insert(0, default_email)

    accounts = []
    seen = set()
    for email_address in configured_accounts:
        normalized = email_address.lower()
        if normalized in seen:
            continue
        seen.add(normalized)
        password = _password_for_email_account(email_address)
        if not password:
            continue
        accounts.append({"email": email_address, "password": password})
    return accounts


def _select_mailbox(mail, selected_mailbox, fallback_mailboxes=None):
    attempted = []
    candidates = [selected_mailbox] + list(fallback_mailboxes or ["INBOX", "Inbox", "inbox"])

    for mailbox in candidates:
        if not mailbox or mailbox in attempted:
            continue

        attempted.append(mailbox)
        status, data = mail.select(mailbox)
        if status == "OK":
            return mailbox

    raise ValueError(f"Could not open email folder. Tried: {', '.join(attempted)}")


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
    mailbox_candidates=None,
    direction="inbound",
    require_terms=False,
    email_address=None,
    email_password=None,
    include_attachments=True,
):
    email_address = email_address or get_setting("YAHOO_EMAIL") or get_setting("EMAIL_ADDRESS")
    email_password = email_password or get_setting("YAHOO_APP_PASSWORD") or get_setting("EMAIL_APP_PASSWORD")
    imap_server = get_setting("IMAP_SERVER", "imap.mail.yahoo.com")
    imap_port = _get_int_setting("IMAP_PORT", 993)
    selected_mailbox = mailbox or get_setting("EMAIL_INBOX_FOLDER", "INBOX")

    if not email_address:
        raise ValueError("YAHOO_EMAIL is missing from .streamlit/secrets.toml")
    if not email_password:
        raise ValueError("YAHOO_APP_PASSWORD is missing from .streamlit/secrets.toml")

    imap_timeout = _get_int_setting("IMAP_TIMEOUT_SECONDS", 20)
    try:
        mail = imaplib.IMAP4_SSL(imap_server, imap_port, timeout=imap_timeout)
    except TypeError:
        mail = imaplib.IMAP4_SSL(imap_server, imap_port)
    try:
        mail.login(email_address, email_password)
        selected_mailbox = _select_mailbox(mail, selected_mailbox, mailbox_candidates)

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
            recipients = decode_text(msg.get("To"))
            body = get_email_body(msg)
            haystack = f"{subject}\n{sender}\n{body}".lower()
            matched_terms = [term for term in normalized_terms if term in haystack]

            if require_terms and normalized_terms and not matched_terms:
                continue

            message_id = normalize_message_id(msg.get("Message-ID")) or email_id.decode()
            in_reply_to = normalize_message_id(msg.get("In-Reply-To"))
            references = parse_reference_ids(msg.get("References"))
            thread_id = derive_thread_id(message_id, in_reply_to, msg.get("References"))
            normalized_subject = normalize_subject(subject)
            conversation_key = derive_conversation_key(
                message_id,
                in_reply_to,
                msg.get("References"),
                subject,
            )

            results.append({
                "id": email_id.decode(),
                "message_id": message_id,
                "mailbox": f"{email_address}:{selected_mailbox}" if email_address else selected_mailbox,
                "mailbox_account": email_address,
                "mailbox_folder": selected_mailbox,
                "direction": direction,
                "thread_id": thread_id,
                "conversation_key": conversation_key,
                "normalized_subject": normalized_subject,
                "in_reply_to": in_reply_to,
                "references": references,
                "subject": subject,
                "from": sender,
                "to": recipients,
                "cc": decode_text(msg.get("Cc")),
                "date": decode_text(msg.get("Date")),
                "received_at": _parse_email_date(msg.get("Date")),
                "body": body,
                "snippet": body[:300],
                "matched_terms": matched_terms,
                "attachments": get_email_attachments(msg) if include_attachments else [],
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
        mailbox=get_setting("EMAIL_INBOX_FOLDER", "INBOX"),
        mailbox_candidates=_split_mailbox_candidates(
            get_setting("EMAIL_INBOX_FOLDER_CANDIDATES", "INBOX,Inbox,inbox")
        ),
        direction="inbound",
        terms=search_terms,
        scan_window=max(limit * 10, 250),
        require_terms=False,
    )


def fetch_operations_email_sync(limit=50):
    terms = get_setting(
        "EMAIL_OPERATIONS_TERMS",
        "quote,rate,pricing,missing info,missing,load,booking,container,appointment,pod,cancel,delivery",
    )
    search_terms = [term.strip() for term in str(terms).split(",") if term.strip()]
    accounts = _operations_email_accounts()
    if not accounts:
        accounts = [{"email": get_setting("YAHOO_EMAIL") or get_setting("EMAIL_ADDRESS"), "password": get_setting("YAHOO_APP_PASSWORD") or get_setting("EMAIL_APP_PASSWORD")}]
    account_count = max(1, len(accounts))
    default_per_account_limit = max(5, (max(1, int(limit)) + account_count - 1) // account_count)
    per_mailbox_limit = _get_int_setting("OPERATIONS_EMAIL_PER_ACCOUNT_LIMIT", default_per_account_limit)
    sent_limit = min(per_mailbox_limit, _get_int_setting("OPERATIONS_SENT_SYNC_LIMIT", 6))
    inbox_scan_window = _get_int_setting("OPERATIONS_EMAIL_SCAN_WINDOW", max(per_mailbox_limit * 4, 60))
    sent_scan_window = _get_int_setting("OPERATIONS_SENT_SCAN_WINDOW", max(sent_limit * 3, 20))
    sync_sent = _get_bool_setting("OPERATIONS_SYNC_SENT_ENABLED", True)

    inbox_messages = []
    sent_messages = []
    for account in accounts:
        account_email = account.get("email")
        account_password = account.get("password")
        if not account_email or not account_password:
            continue
        try:
            inbox_messages.extend(
                _fetch_recent_emails(
                    limit=per_mailbox_limit,
                    search_query=get_setting("EMAIL_OPERATIONS_IMAP_SEARCH", "ALL"),
                    mailbox=get_setting("EMAIL_INBOX_FOLDER", "INBOX"),
                    mailbox_candidates=_split_mailbox_candidates(
                        get_setting("EMAIL_INBOX_FOLDER_CANDIDATES", "INBOX,Inbox,inbox")
                    ),
                    direction="inbound",
                    terms=search_terms,
                    scan_window=inbox_scan_window,
                    require_terms=False,
                    email_address=account_email,
                    email_password=account_password,
                    include_attachments=True,
                )
            )
        except Exception:
            pass

        if not sync_sent or sent_limit <= 0:
            continue
        try:
            sent_messages.extend(
                _fetch_recent_emails(
                    limit=sent_limit,
                    search_query=get_setting("EMAIL_OPERATIONS_SENT_IMAP_SEARCH", "ALL"),
                    mailbox=get_setting("EMAIL_SENT_FOLDER", "Sent"),
                    mailbox_candidates=_split_mailbox_candidates(
                        get_setting(
                            "EMAIL_SENT_FOLDER_CANDIDATES",
                            "Sent,Sent Messages,Sent Mail,Sent Items,sent,[Gmail]/Sent Mail",
                        )
                    ),
                    direction="outbound",
                    terms=search_terms,
                    scan_window=sent_scan_window,
                    require_terms=False,
                    email_address=account_email,
                    email_password=account_password,
                    include_attachments=False,
                )
            )
        except Exception:
            pass

    return inbox_messages + sent_messages


def fetch_operations_email_by_message_id(message_id, limit=5):
    normalized_message_id = normalize_message_id(message_id)
    if not normalized_message_id:
        return []

    search_query = f'HEADER Message-ID "{normalized_message_id}"'
    accounts = _operations_email_accounts()
    if not accounts:
        accounts = [{"email": get_setting("YAHOO_EMAIL") or get_setting("EMAIL_ADDRESS"), "password": get_setting("YAHOO_APP_PASSWORD") or get_setting("EMAIL_APP_PASSWORD")}]

    found_messages = []
    for account in accounts:
        account_email = account.get("email")
        account_password = account.get("password")
        if not account_email or not account_password:
            continue
        try:
            found_messages.extend(
                _fetch_recent_emails(
                    limit=limit,
                    search_query=search_query,
                    mailbox=get_setting("EMAIL_INBOX_FOLDER", "INBOX"),
                    mailbox_candidates=_split_mailbox_candidates(
                        get_setting("EMAIL_INBOX_FOLDER_CANDIDATES", "INBOX,Inbox,inbox")
                    ),
                    direction="inbound",
                    terms=[],
                    scan_window=max(limit * 4, 20),
                    require_terms=False,
                    email_address=account_email,
                    email_password=account_password,
                    include_attachments=True,
                )
            )
        except Exception:
            pass
        try:
            found_messages.extend(
                _fetch_recent_emails(
                    limit=limit,
                    search_query=search_query,
                    mailbox=get_setting("EMAIL_SENT_FOLDER", "Sent"),
                    mailbox_candidates=_split_mailbox_candidates(
                        get_setting(
                            "EMAIL_SENT_FOLDER_CANDIDATES",
                            "Sent,Sent Messages,Sent Mail,Sent Items,sent,[Gmail]/Sent Mail",
                        )
                    ),
                    direction="outbound",
                    terms=[],
                    scan_window=max(limit * 4, 20),
                    require_terms=False,
                    email_address=account_email,
                    email_password=account_password,
                    include_attachments=False,
                )
            )
        except Exception:
            pass

    return found_messages


def _legacy_fetch_operations_email_sync(limit=50):
    terms = get_setting(
        "EMAIL_OPERATIONS_TERMS",
        "quote,rate,pricing,missing info,missing,load,booking,container,appointment,pod,cancel,delivery",
    )
    search_terms = [term.strip() for term in str(terms).split(",") if term.strip()]
    per_mailbox_limit = max(1, int(limit))

    inbox_messages = _fetch_recent_emails(
        limit=per_mailbox_limit,
        search_query=get_setting("EMAIL_OPERATIONS_IMAP_SEARCH", "ALL"),
        mailbox=get_setting("EMAIL_INBOX_FOLDER", "INBOX"),
        mailbox_candidates=_split_mailbox_candidates(
            get_setting("EMAIL_INBOX_FOLDER_CANDIDATES", "INBOX,Inbox,inbox")
        ),
        direction="inbound",
        terms=search_terms,
        scan_window=max(per_mailbox_limit * 10, 250),
        require_terms=False,
    )

    try:
        sent_messages = _fetch_recent_emails(
            limit=per_mailbox_limit,
            search_query=get_setting("EMAIL_OPERATIONS_SENT_IMAP_SEARCH", "ALL"),
            mailbox=get_setting("EMAIL_SENT_FOLDER", "Sent"),
            mailbox_candidates=_split_mailbox_candidates(
                get_setting(
                    "EMAIL_SENT_FOLDER_CANDIDATES",
                    "Sent,Sent Messages,Sent Mail,Sent Items,sent,[Gmail]/Sent Mail",
                )
            ),
            direction="outbound",
            terms=search_terms,
            scan_window=max(per_mailbox_limit * 10, 250),
            require_terms=False,
        )
    except Exception:
        sent_messages = []

    return inbox_messages + sent_messages


def _legacy_fetch_operations_email_by_message_id(message_id, limit=5):
    normalized_message_id = normalize_message_id(message_id)
    if not normalized_message_id:
        return []

    search_query = f'HEADER Message-ID "{normalized_message_id}"'
    inbox_messages = _fetch_recent_emails(
        limit=limit,
        search_query=search_query,
        mailbox=get_setting("EMAIL_INBOX_FOLDER", "INBOX"),
        mailbox_candidates=_split_mailbox_candidates(
            get_setting("EMAIL_INBOX_FOLDER_CANDIDATES", "INBOX,Inbox,inbox")
        ),
        direction="inbound",
        terms=[],
        scan_window=max(limit * 4, 20),
        require_terms=False,
    )

    try:
        sent_messages = _fetch_recent_emails(
            limit=limit,
            search_query=search_query,
            mailbox=get_setting("EMAIL_SENT_FOLDER", "Sent"),
            mailbox_candidates=_split_mailbox_candidates(
                get_setting(
                    "EMAIL_SENT_FOLDER_CANDIDATES",
                    "Sent,Sent Messages,Sent Mail,Sent Items,sent,[Gmail]/Sent Mail",
                )
            ),
            direction="outbound",
            terms=[],
            scan_window=max(limit * 4, 20),
            require_terms=False,
        )
    except Exception:
        sent_messages = []

    return inbox_messages + sent_messages
