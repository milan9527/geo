from __future__ import annotations

import argparse
import getpass
import secrets

from backend.auth import hash_password
from backend.database import connection, init_db, utc_now


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create or rotate an Aperture Control administrator."
    )
    parser.add_argument("--username", default="admin")
    parser.add_argument("--display-name", default="Aperture Administrator")
    parser.add_argument("--role", default="administrator")
    parser.add_argument(
        "--generate",
        action="store_true",
        help="Generate a strong password and print it once.",
    )
    args = parser.parse_args()

    username = args.username.strip().lower()
    if not username:
        raise SystemExit("Username is required")
    password = (
        secrets.token_urlsafe(18)
        if args.generate
        else getpass.getpass("Administrator password: ")
    )
    if len(password) < 12:
        raise SystemExit("Password must contain at least 12 characters")

    password_hash, password_salt, iterations = hash_password(password)
    timestamp = utc_now()
    init_db()
    with connection() as conn:
        existing = conn.execute(
            "SELECT id FROM admin_users WHERE username = %s",
            (username,),
        ).fetchone()
        if existing:
            user_id = existing["id"]
            conn.execute(
                """
                UPDATE admin_users
                SET display_name = %s, role = %s, password_hash = %s,
                    password_salt = %s, password_iterations = %s,
                    status = 'active', failed_attempts = 0, locked_until = NULL,
                    updated_at = %s
                WHERE id = %s
                """,
                (
                    args.display_name,
                    args.role,
                    password_hash,
                    password_salt,
                    iterations,
                    timestamp,
                    user_id,
                ),
            )
            conn.execute(
                "DELETE FROM admin_sessions WHERE user_id = %s",
                (user_id,),
            )
            action = "rotated"
        else:
            user_id = conn.execute(
                """
                INSERT INTO admin_users(
                    username, display_name, role, password_hash, password_salt,
                    password_iterations, status, created_at, updated_at
                ) VALUES(%s, %s, %s, %s, %s, %s, 'active', %s, %s)
                RETURNING id
                """,
                (
                    username,
                    args.display_name,
                    args.role,
                    password_hash,
                    password_salt,
                    iterations,
                    timestamp,
                    timestamp,
                ),
            ).fetchone()["id"]
            action = "created"

    print(f"Administrator {action}: {username} (id={user_id})")
    if args.generate:
        print(f"Generated password: {password}")


if __name__ == "__main__":
    main()
