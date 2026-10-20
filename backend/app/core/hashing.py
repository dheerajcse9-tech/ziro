"""Canonical JSON hashing used for proposal binding, receipts and the audit chain."""
from __future__ import annotations

import hashlib
import json
from typing import Any


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str, ensure_ascii=False)


def sha256_hex(value: Any) -> str:
    data = value if isinstance(value, (bytes, bytearray)) else canonical_json(value).encode()
    return hashlib.sha256(data).hexdigest()
