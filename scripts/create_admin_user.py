"""Bootstrap the first Streamlit login account (or add any subsequent
one). There is no in-app "create user" page in v1 - app_users starts
empty after the app_users_migration.sql migration runs, so without this
script nobody could ever log in. Run manually, once, against a real
deployment's database; never automated, never scheduled.

Usage:
    python scripts/create_admin_user.py --email you@calitranscorp.com --display-name "Your Name" --role admin
    (prompts for password via getpass - never pass it on the command line,
    it would land in shell history)

Requires app_users_migration.sql to already be applied (run
scripts/run_migrations.py first). Never prints the password or the full
DATABASE_URL.
"""
from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from application.auth.models import Role  # noqa: E402
from application.auth.password import hash_password  # noqa: E402
from db_client import SchemaNotReadyError  # noqa: E402
from repositories.user_repo import create_user, get_user_by_email  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--email", required=True)
    parser.add_argument("--display-name", required=True)
    parser.add_argument("--role", required=True, choices=[r.value for r in Role])
    args = parser.parse_args()

    email = args.email.strip()
    if not email:
        print("Email must not be empty.", file=sys.stderr)
        return 1

    try:
        if get_user_by_email(email) is not None:
            print(f"A user with email {email!r} already exists. Nothing created.", file=sys.stderr)
            return 1
    except SchemaNotReadyError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    password = getpass.getpass("Password: ")
    confirm = getpass.getpass("Confirm password: ")
    if password != confirm:
        print("Passwords did not match. Nothing created.", file=sys.stderr)
        return 1

    try:
        password_hash = hash_password(password)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    user_id = create_user(
        email=email,
        display_name=args.display_name.strip(),
        password_hash=password_hash,
        role=args.role,
    )
    print(f"Created user id={user_id} email={email} role={args.role}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
