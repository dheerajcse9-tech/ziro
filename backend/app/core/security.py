"""Password hashing (PBKDF2-SHA256, stdlib) and JWT session tokens."""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
from datetime import timedelta

import jwt

from app.core.config import get_settings
from app.db.base import utcnow

_ITERATIONS = 240_000


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _ITERATIONS)
    return "pbkdf2_sha256${}${}${}".format(
        _ITERATIONS, base64.b64encode(salt).decode(), base64.b64encode(digest).decode()
    )


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iterations, salt_b64, digest_b64 = stored.split("$")
    except ValueError:
        return False
    if algo != "pbkdf2_sha256":
        return False
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode(), base64.b64decode(salt_b64), int(iterations)
    )
    return hmac.compare_digest(digest, base64.b64decode(digest_b64))


def create_access_token(user_id: int, role: str) -> str:
    settings = get_settings()
    now = utcnow()
    payload = {
        "sub": str(user_id),
        "role": role,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=settings.jwt_ttl_minutes)).timestamp()),
        "iss": "placementos",
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def decode_access_token(token: str) -> dict:
    settings = get_settings()
    return jwt.decode(token, settings.jwt_secret, algorithms=["HS256"], issuer="placementos")


def _fernet():
    from cryptography.fernet import Fernet

    key = hashlib.sha256(f"placementos-credentials:{get_settings().jwt_secret}".encode()).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def encrypt_secret(value: str) -> str:
    """Encrypt a stored credential (e.g. a Google refresh token) with a key derived from JWT_SECRET."""
    return _fernet().encrypt(value.encode()).decode()


def decrypt_secret(token: str) -> str:
    """Raises ValueError when the secret was encrypted under a different JWT_SECRET."""
    from cryptography.fernet import InvalidToken

    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken as exc:
        raise ValueError("Stored credential cannot be decrypted (JWT_SECRET changed?)") from exc


def create_state_token(user_id: int, purpose: str, minutes: int = 10) -> str:
    """Short-lived signed token for OAuth `state` (binds the callback to the user who started it)."""
    now = utcnow()
    payload = {"sub": str(user_id), "purpose": purpose, "nonce": base64.urlsafe_b64encode(os.urandom(12)).decode(),
               "iat": int(now.timestamp()), "exp": int((now + timedelta(minutes=minutes)).timestamp()),
               "iss": "placementos"}
    return jwt.encode(payload, get_settings().jwt_secret, algorithm="HS256")


def read_state_token(token: str, purpose: str) -> int:
    payload = jwt.decode(token, get_settings().jwt_secret, algorithms=["HS256"], issuer="placementos")
    if payload.get("purpose") != purpose:
        raise jwt.InvalidTokenError("wrong purpose")
    return int(payload["sub"])
