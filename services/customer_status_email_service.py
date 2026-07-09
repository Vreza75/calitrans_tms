from __future__ import annotations

import re
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import getaddresses

from config import get_secret
from db_client import execute
from services.dispatch_data_service import _insert_dispatch_message
from services.dispatch_workflow_service import _next_status_goal, _eta_to_next_goal, _first_present

NAMED_EMAIL_ACCOUNTS = {
    "DISPATCH": "dispatch@calitranscorp.com",
    "MARGIE": "margiea@calitranscorp.com",
    "ACCOUNTING": "accounting@calitranscorp.com",
}

def _safe_str(value, default: str = "") -> str:
    value_str = str(value if value is not None else default).strip()
    if value_str.lower() in {"nan", "none", "nat", "null"}:
        return default
    return value_str

def _get_app_setting(name: str, default=None):
    return get_secret(name, default)

def _get_first_app_setting(names: list[str], default=None):
    for name in names:
        value = _get_app_setting(name)
        if value not in [None, ""]:
            return value
    return default

def _setting_suffix_for_email(email_address: str) -> str:
    local_part = _safe_str(email_address).split("@", 1)[0]
    return re.sub(r"[^A-Za-z0-9]+", "_", local_part).strip("_").upper()

def _unique_setting_names(names: list[str]) -> list[str]:
    result = []
    seen = set()
    for name in names:
        name = _safe_str(name)
        normalized = name.upper()
        if not name or normalized in seen:
            continue
        seen.add(normalized)
        result.append(name)
    return result

def _email_account_aliases(email_address: str) -> list[str]:
    normalized_email = _safe_str(email_address).lower()
    aliases = [_setting_suffix_for_email(email_address)]
    for alias, default_email in NAMED_EMAIL_ACCOUNTS.items():
        configured_email = _get_first_app_setting(
            [f"{alias}_YAHOO_EMAIL", f"{alias}_EMAIL", f"YAHOO_EMAIL_{alias}"],
            default_email,
        )
        if normalized_email == _safe_str(configured_email).lower():
            aliases.append(alias)
            if alias == "MARGIE":
                aliases.append("MARGIEA")
    return _unique_setting_names(aliases)

def _setting_candidates_for_aliases(aliases: list[str], templates: list[str]) -> list[str]:
    return _unique_setting_names([template.format(alias=alias) for alias in aliases for template in templates])

def _split_email_list(value: str) -> list[str]:
    return [item.strip() for item in str(value or "").replace(";", ",").split(",") if item.strip()]

def _split_email_addresses(value: str) -> list[str]:
    addresses = []
    for _, address in getaddresses([str(value or "").replace(";", ",")]):
        clean_address = _safe_str(address)
        if clean_address:
            addresses.append(clean_address)
    return addresses

def _smtp_credentials_for_sender(from_email: str) -> tuple[str, str, str]:
    smtp_user_default = _get_first_app_setting(["SMTP_USER", "DISPATCH_YAHOO_EMAIL", "YAHOO_EMAIL", "EMAIL_ADDRESS"])
    smtp_password_default = _get_first_app_setting(
        ["SMTP_PASSWORD", "DISPATCH_YAHOO_APP_PASSWORD", "YAHOO_APP_PASSWORD", "EMAIL_APP_PASSWORD"]
    )
    from_email = _safe_str(from_email) or _get_first_app_setting(["DISPATCH_EMAIL", "YAHOO_EMAIL", "EMAIL_ADDRESS"], smtp_user_default)
    aliases = _email_account_aliases(from_email)
    smtp_user = _get_first_app_setting(
        _setting_candidates_for_aliases(
            aliases,
            [
                "{alias}_SMTP_USER",
                "{alias}_YAHOO_EMAIL",
                "{alias}_EMAIL_ADDRESS",
                "SMTP_USER_{alias}",
                "YAHOO_EMAIL_{alias}",
                "EMAIL_ADDRESS_{alias}",
            ],
        ),
        from_email or smtp_user_default,
    )
    smtp_password = _get_first_app_setting(
        _setting_candidates_for_aliases(
            aliases,
            [
                "{alias}_SMTP_PASSWORD",
                "{alias}_YAHOO_APP_PASSWORD",
                "{alias}_EMAIL_APP_PASSWORD",
                "SMTP_PASSWORD_{alias}",
                "YAHOO_APP_PASSWORD_{alias}",
                "EMAIL_APP_PASSWORD_{alias}",
            ],
        ),
        smtp_password_default if _safe_str(smtp_user).lower() == _safe_str(smtp_user_default).lower() else None,
    )
    return from_email, smtp_user, smtp_password

def _customer_email_for_load(load) -> str:
    return _first_present(
        load,
        ["Customer Email", "Contact Email", "customer_email", "contact_email", "Email", "email"],
        "",
    )

def _build_customer_status_email(load, old_status: str, new_status: str, note: str = "") -> tuple[str, str]:
    company_name = _get_app_setting("COMPANY_NAME", "CaliTrans")
    booking = _first_present(load, ["Booking Number", "booking_number"], "-")
    load_ref = _first_present(load, ["Load ID", "id", "_row_id"], "-")
    customer = _first_present(load, ["Customer", "customer"], "Customer")
    container = _first_present(load, ["Container Number", "container_number", "Reference Number"], "-")
    move_type = _first_present(load, ["TYPE", "type"], "-")
    pickup = _first_present(load, ["Port", "terminal", "pickup_location"], "-")
    delivery = _first_present(load, ["Warehouse", "Address", "delivery_location"], "-")
    driver = _first_present(load, ["Driver Name", "driver_name"], "Pending")
    truck = _first_present(load, ["Truck Assigned", "truck_assigned"], "Pending")
    chassis = _first_present(load, ["Chassis", "chassis"], "-")
    lfd = _first_present(load, ["LFD", "lfd"], "-")
    current_location = _first_present(load, ["current_location", "Current Location"], "-")
    public_notes = _first_present(load, ["Public Notes", "public_notes"], "")
    dispatcher_notes = note or public_notes
    next_goal = _next_status_goal(new_status)
    eta = _eta_to_next_goal(load, new_status)

    subject = f"{company_name} Load Update | {booking} | {new_status}"

    body = f"""
Hello {customer},

Your load status has been updated.

STATUS UPDATE
Previous Status: {old_status or '-'}
Current Status: {new_status}
Next Goal: {next_goal}
ETA to Next Goal: {eta}

LOAD DETAILS
Load ID: {load_ref}
Move Type: {move_type}
Booking Number: {booking}
Container / Reference: {container}
LFD: {lfd}

ROUTE
Pickup / Port: {pickup}
Delivery / Warehouse: {delivery}
Current Location: {current_location}

DISPATCH DETAILS
Driver: {driver}
Truck: {truck}
Chassis: {chassis}

NOTES
{dispatcher_notes if dispatcher_notes else 'No additional notes at this time.'}

Thank you,
{company_name} Dispatch
""".strip()
    return subject, body

def _log_customer_email_notification(load_id: int, old_status: str, new_status: str, recipient: str, subject: str, body: str, status: str, error_message: str = "") -> None:
    """Log customer status emails. Safe no-op if the table has not been created yet."""
    try:
        execute(
            """
            insert into email_notifications
                (load_id, old_status, new_status, sent_to, subject, body, status, error_message, sent_at)
            values
                (:load_id, :old_status, :new_status, :sent_to, :subject, :body, :status, :error_message,
                 case when :status = 'sent' then now() else null end)
            """,
            {
                "load_id": load_id,
                "old_status": old_status,
                "new_status": new_status,
                "sent_to": recipient or None,
                "subject": subject,
                "body": body,
                "status": status,
                "error_message": error_message or None,
            },
        )
    except Exception:
        pass

def _send_smtp_email(to_email: str, subject: str, body: str, from_email: str = "", cc_email: str = "") -> None:
    smtp_host = _get_app_setting("SMTP_HOST", "smtp.mail.yahoo.com")
    smtp_port = int(_get_app_setting("SMTP_PORT", 465))
    dispatch_email, smtp_user, smtp_password = _smtp_credentials_for_sender(from_email)
    to_recipients = _split_email_addresses(to_email)
    cc_recipients = _split_email_addresses(cc_email)

    if not to_recipients:
        raise ValueError("Missing customer email address on this load.")
    if not smtp_host or not smtp_user or not smtp_password:
        raise ValueError("Missing email settings. Add YAHOO_EMAIL and YAHOO_APP_PASSWORD, or SMTP_HOST, SMTP_USER, and SMTP_PASSWORD.")

    msg = MIMEMultipart()
    msg["From"] = dispatch_email
    msg["To"] = ", ".join(to_recipients)
    if cc_recipients:
        msg["Cc"] = ", ".join(cc_recipients)
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain"))
    recipients = []
    seen = set()
    for address in to_recipients + cc_recipients:
        normalized = address.lower()
        if normalized in seen:
            continue
        seen.add(normalized)
        recipients.append(address)
    if smtp_port == 465:
        with smtplib.SMTP_SSL(smtp_host, smtp_port) as server:
            server.login(smtp_user, smtp_password)
            server.sendmail(dispatch_email, recipients, msg.as_string())
    else:
        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.starttls()
            server.login(smtp_user, smtp_password)
            server.sendmail(dispatch_email, recipients, msg.as_string())

def _send_customer_status_update_email(
    load_id: int,
    original_load,
    old_status: str,
    new_status: str,
    note: str = "",
    recipient_override: str = "",
) -> tuple[bool, str]:
    """Send a customer email only when status actually changes."""
    if old_status == new_status:
        return True, "Status unchanged; no customer email needed."

    updated_load = original_load.copy()
    updated_load["Status"] = new_status
    if note:
        updated_load["Dispatcher Notes"] = note

    recipient = recipient_override.strip() or _customer_email_for_load(updated_load)

    subject, body = _build_customer_status_email(updated_load, old_status, new_status, note)

    try:
        _send_smtp_email(recipient, subject, body)
        _log_customer_email_notification(load_id, old_status, new_status, recipient, subject, body, "sent")
        _insert_dispatch_message(load_id, "customer_status_email", "outbound", recipient, body)
        return True, f"Customer email sent to {recipient}."
    except Exception as exc:
        _log_customer_email_notification(load_id, old_status, new_status, recipient, subject, body, "failed", str(exc))
        return False, str(exc)

