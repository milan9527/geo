from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets


PASSWORD_ITERATIONS = int(os.environ.get("GEO_PASSWORD_ITERATIONS", "600000"))


def hash_password(
    password: str,
    *,
    salt: bytes | None = None,
    iterations: int = PASSWORD_ITERATIONS,
) -> tuple[str, str, int]:
    actual_salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        actual_salt,
        iterations,
    )
    return (
        base64.urlsafe_b64encode(digest).decode("ascii"),
        base64.urlsafe_b64encode(actual_salt).decode("ascii"),
        iterations,
    )


def verify_password(
    password: str,
    expected_hash: str,
    encoded_salt: str,
    iterations: int,
) -> bool:
    salt = base64.urlsafe_b64decode(encoded_salt.encode("ascii"))
    actual_hash, _, _ = hash_password(
        password,
        salt=salt,
        iterations=iterations,
    )
    return hmac.compare_digest(actual_hash, expected_hash)


def generate_session_token() -> str:
    return secrets.token_urlsafe(32)


def session_token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
