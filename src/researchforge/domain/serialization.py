"""Canonical bytes for reproducible local audit digests."""

from __future__ import annotations

import json
from hashlib import sha256
from typing import Any


def canonical_json(value: Any) -> bytes:
    """Stable UTF-8 JSON, rejecting NaN/Inf, non-JSON types and recursive cycles."""
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")


def digest_bytes(payload: bytes) -> str:
    return sha256(payload).hexdigest()
