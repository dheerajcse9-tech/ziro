"""Redaction of personal data before it is written to logs, audit details or agent events."""
from __future__ import annotations

import re
from typing import Any

SENSITIVE_KEYS = {"email", "phone", "password", "password_hash", "token", "api_key", "authorization", "secret"}
_EMAIL_RE = re.compile(r"([A-Za-z0-9._%+-])[A-Za-z0-9._%+-]*@([A-Za-z0-9.-]+)")
_PHONE_RE = re.compile(r"\+?\d[\d\s-]{8,}\d")


def mask_text(text: str) -> str:
    text = _EMAIL_RE.sub(lambda m: f"{m.group(1)}***@{m.group(2)}", text)
    return _PHONE_RE.sub("[redacted-phone]", text)


def redact(value: Any, _depth: int = 0) -> Any:
    if _depth > 8:
        return "[truncated]"
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            if str(k).lower() in SENSITIVE_KEYS:
                out[k] = "[redacted]" if v not in (None, "") else v
            else:
                out[k] = redact(v, _depth + 1)
        return out
    if isinstance(value, list):
        return [redact(v, _depth + 1) for v in value]
    if isinstance(value, str):
        return mask_text(value)
    return value
