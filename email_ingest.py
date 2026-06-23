from __future__ import annotations

from email_client import fetch_recent_operations_emails


def main() -> None:
    emails = fetch_recent_operations_emails(limit=10)
    print(f"Fetched {len(emails)} Yahoo inbox email(s)")

    for item in emails:
        subject = item.get("subject") or "(no subject)"
        sender = item.get("from") or "(unknown sender)"
        received = item.get("received_at") or item.get("date") or ""
        print(f"- {received} | {sender} | {subject}")


if __name__ == "__main__":
    main()
