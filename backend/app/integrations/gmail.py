"""Google sign-in (OAuth 2.0 web-server flow) and the Gmail API for the placement mailbox.

The placement officer signs in once with the Google account that receives
company emails (for example the drive upload officer's Gmail). PlacementOS
stores only the refresh token, encrypted, and asks for two permissions:

  gmail.readonly  read company emails (nothing is marked read, moved or deleted)
  gmail.send      send invitations, forms, reminders and rosters from that mailbox

Endpoints used (Google OAuth 2.0 and Gmail API v1):
  https://accounts.google.com/o/oauth2/v2/auth          consent screen
  https://oauth2.googleapis.com/token                    code exchange / refresh
  https://oauth2.googleapis.com/revoke                   disconnect
  GET  gmail/v1/users/me/profile                         which account is connected
  GET  gmail/v1/users/me/messages?q=...                  find company emails
  GET  gmail/v1/users/me/messages/{id}?format=raw        read one email (RFC 822)
  POST gmail/v1/users/me/messages/send                   send an email
"""
from __future__ import annotations

import base64
from datetime import timedelta
from typing import Any
from urllib.parse import urlencode

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import PreconditionFailed, ValidationFailed
from app.core.security import create_state_token, decrypt_secret, encrypt_secret
from app.db.base import utcnow
from app.models.entities import OAuthCredential

PROVIDER = "google_gmail"
SCOPES = ["https://www.googleapis.com/auth/gmail.readonly", "https://www.googleapis.com/auth/gmail.send"]
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
API = "https://gmail.googleapis.com/gmail/v1/users/me"

# Tests replace this with an httpx.MockTransport.
TRANSPORT: httpx.BaseTransport | None = None
_access_cache: dict[str, Any] = {}


class GmailError(RuntimeError):
    pass


def _http() -> httpx.Client:
    return httpx.Client(timeout=20, transport=TRANSPORT)


def credential(db: Session) -> OAuthCredential | None:
    return db.scalar(select(OAuthCredential).where(OAuthCredential.provider == PROVIDER))


def connected(db: Session) -> bool:
    cred = credential(db)
    return cred is not None and cred.status == "CONNECTED"


def status(db: Session) -> dict[str, Any]:
    s = get_settings()
    cred = credential(db)
    return {
        "configured": s.google_configured, "redirect_uri": s.google_callback_url,
        "connected": cred is not None and cred.status == "CONNECTED",
        "account_email": cred.account_email if cred else None, "status": cred.status if cred else None,
        "last_error": cred.last_error if cred else None, "scopes": SCOPES,
        "connected_at": cred.connected_at.isoformat() if cred else None,
        "last_used_at": cred.last_used_at.isoformat() if cred and cred.last_used_at else None,
    }


def authorization_url(user_id: int, login_hint: str | None = None) -> str:
    s = get_settings()
    if not s.google_configured:
        raise PreconditionFailed("Google sign-in is not set up: add GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET to backend/.env")
    params = {"client_id": s.google_client_id, "redirect_uri": s.google_callback_url, "response_type": "code",
              "scope": " ".join(SCOPES), "access_type": "offline", "prompt": "consent",
              "include_granted_scopes": "true", "state": create_state_token(user_id, "google_oauth")}
    if login_hint:
        params["login_hint"] = login_hint
    return f"{AUTH_URL}?{urlencode(params)}"


def _token_request(data: dict[str, str]) -> dict[str, Any]:
    s = get_settings()
    try:
        with _http() as http:
            r = http.post(TOKEN_URL, data={**data, "client_id": s.google_client_id, "client_secret": s.google_client_secret})
    except httpx.HTTPError as exc:
        raise GmailError(f"Could not reach Google: {type(exc).__name__}: {exc}") from exc
    body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    if r.status_code != 200:
        err = body.get("error", f"HTTP {r.status_code}")
        if err == "invalid_grant":
            raise GmailError("Google sign-in expired or was revoked. Reconnect Gmail in Settings.")
        raise GmailError(f"Google token request failed: {err} {body.get('error_description', '')}".strip())
    return body


def complete_sign_in(db: Session, code: str, user_id: int) -> OAuthCredential:
    tokens = _token_request({"code": code, "grant_type": "authorization_code", "redirect_uri": get_settings().google_callback_url})
    refresh = tokens.get("refresh_token")
    if not refresh:
        raise GmailError("Google did not return a refresh token. Remove PlacementOS from your Google account's "
                         "third-party access and connect again.")
    granted = set((tokens.get("scope") or "").split())
    missing = [sc for sc in SCOPES if sc not in granted]
    if missing:
        raise GmailError("Google gave only part of the access PlacementOS needs "
                         f"(missing: {', '.join(m.rsplit('/', 1)[-1] for m in missing)}). Click Connect Gmail again and, on "
                         "Google's permission screen, tick BOTH boxes (read and send email) or 'Select all'.")
    _access_cache.clear()
    _access_cache.update(token=tokens["access_token"], expires=utcnow() + timedelta(seconds=int(tokens.get("expires_in", 3600)) - 60))
    profile = _get(f"{API}/profile", tokens["access_token"])
    cred = credential(db) or OAuthCredential(provider=PROVIDER)
    cred.account_email = profile["emailAddress"]
    cred.refresh_token_encrypted = encrypt_secret(refresh)
    cred.scopes = " ".join(sorted(granted))
    cred.status, cred.last_error = "CONNECTED", None
    cred.connected_by_id, cred.connected_at = user_id, utcnow()
    db.add(cred)
    db.flush()
    return cred


def disconnect(db: Session) -> None:
    cred = credential(db)
    if cred is None:
        return
    try:
        with _http() as http:
            http.post(REVOKE_URL, data={"token": decrypt_secret(cred.refresh_token_encrypted)})
    except (httpx.HTTPError, ValueError):
        pass  # local removal is what matters
    db.delete(cred)
    _access_cache.clear()
    db.flush()


def _access_token(db: Session) -> str:
    cred = credential(db)
    if cred is None or cred.status != "CONNECTED":
        raise GmailError("Gmail is not connected")
    if _access_cache.get("token") and _access_cache.get("expires") and _access_cache["expires"] > utcnow():
        return _access_cache["token"]
    try:
        tokens = _token_request({"grant_type": "refresh_token", "refresh_token": decrypt_secret(cred.refresh_token_encrypted)})
    except (GmailError, ValueError) as exc:
        cred.status, cred.last_error = "ERROR", str(exc)
        db.flush()
        raise GmailError(str(exc)) from exc
    _access_cache.update(token=tokens["access_token"], expires=utcnow() + timedelta(seconds=int(tokens.get("expires_in", 3600)) - 60))
    cred.last_used_at = utcnow()
    return tokens["access_token"]


def _get(url: str, token: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    try:
        with _http() as http:
            r = http.get(url, params=params, headers={"Authorization": f"Bearer {token}"})
    except httpx.HTTPError as exc:
        raise GmailError(f"Could not reach Gmail: {type(exc).__name__}: {exc}") from exc
    if r.status_code != 200:
        raise GmailError(f"Gmail API returned HTTP {r.status_code}: {r.text[:200]}")
    return r.json()


def search_raw_messages(db: Session, query: str, limit: int = 50) -> list[bytes]:
    """Raw RFC 822 bytes of matching messages, oldest first."""
    token = _access_token(db)
    listing = _get(f"{API}/messages", token, {"q": query, "maxResults": limit})
    out = []
    for ref in reversed(listing.get("messages", [])):
        msg = _get(f"{API}/messages/{ref['id']}", token, {"format": "raw"})
        out.append(base64.urlsafe_b64decode(msg["raw"] + "=" * (-len(msg["raw"]) % 4)))
    return out


def send_mime(db: Session, mime_bytes: bytes) -> str:
    token = _access_token(db)
    try:
        with _http() as http:
            r = http.post(f"{API}/messages/send", json={"raw": base64.urlsafe_b64encode(mime_bytes).decode()},
                          headers={"Authorization": f"Bearer {token}"})
    except httpx.HTTPError as exc:
        raise GmailError(f"Could not reach Gmail: {type(exc).__name__}: {exc}") from exc
    if r.status_code != 200:
        raise GmailError(f"Gmail refused the message (HTTP {r.status_code}): {r.text[:200]}")
    return r.json().get("id", "")


def account_email(db: Session) -> str | None:
    cred = credential(db)
    return cred.account_email if cred and cred.status == "CONNECTED" else None


def require_code(code: str | None, error: str | None) -> str:
    if error:
        raise ValidationFailed(f"Google sign-in was cancelled or refused: {error}")
    if not code:
        raise ValidationFailed("Missing authorisation code from Google")
    return code
