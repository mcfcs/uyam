"""Author pseudonymization for the Uyám pipeline.

Raw Reddit usernames must never appear in JSONL, SQLite, logs, or exceptions.
All callers must use pseudonymize_author(); do not inline any hashing logic.

The resulting identifiers are *pseudonymized*, not anonymous: the persistent
HMAC digest can still link a user's contributions within this dataset.
This linkage is intentional for deduplication and research auditing.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os

logger = logging.getLogger(__name__)

_HMAC_KEY: bytes | None = None

_DELETED_SENTINELS = frozenset({"[deleted]", "[removed]", ""})


def _get_hmac_key() -> bytes:
    global _HMAC_KEY
    if _HMAC_KEY is None:
        raw = os.environ.get("AUTHOR_HMAC_KEY", "")
        if not raw:
            raise RuntimeError(
                "AUTHOR_HMAC_KEY is not set. "
                "Add it to your .env file before collecting data."
            )
        _HMAC_KEY = raw.encode()
    return _HMAC_KEY


def reset_hmac_key_cache() -> None:
    """Reset the cached HMAC key. Used in tests to swap key between cases."""
    global _HMAC_KEY
    _HMAC_KEY = None


def pseudonymize_author(username: str | None) -> tuple[str | None, str]:
    """Return (author_hash, author_status) for a Reddit username.

    author_status is one of: "pseudonymized", "deleted", "unavailable".
    Raw usernames are never logged or re-raised in exceptions.
    """
    if username is None:
        return None, "unavailable"

    normalized = username.strip().lower()
    if normalized in _DELETED_SENTINELS:
        return None, "deleted"

    key = _get_hmac_key()
    digest = hmac.new(key, normalized.encode("utf-8"), hashlib.sha256).hexdigest()
    return digest, "pseudonymized"
