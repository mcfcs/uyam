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
import secrets
from pathlib import Path

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


def ensure_hmac_key(env_path: Path | None = None) -> None:
    """Guarantee AUTHOR_HMAC_KEY exists; auto-generate and persist if missing.

    If the key is already present in the environment, this is a no-op. Otherwise
    a fresh key is generated, appended to the given .env file (created if
    absent), exported to the process environment, and the cache is reset.

    The key VALUE is never logged. The key must remain stable across all
    collection runs: changing or losing it makes previously stored author
    hashes irreconcilable.
    """
    if os.environ.get("AUTHOR_HMAC_KEY"):
        return

    if env_path is None:
        env_path = Path(__file__).parent.parent.parent / ".env"

    key = secrets.token_hex(32)
    os.environ["AUTHOR_HMAC_KEY"] = key
    reset_hmac_key_cache()

    try:
        existing = env_path.read_text(encoding="utf-8") if env_path.exists() else ""
        prefix = "" if existing == "" or existing.endswith("\n") else "\n"
        with env_path.open("a", encoding="utf-8") as fh:
            fh.write(f"{prefix}AUTHOR_HMAC_KEY={key}\n")
        persisted = True
    except OSError as exc:
        persisted = False
        logger.warning(
            "hmac_key_persist_failed",
            extra={"env_path": str(env_path), "error": type(exc).__name__},
        )

    logger.warning(
        "hmac_key_generated",
        extra={
            "env_path": str(env_path),
            "persisted": persisted,
            "note": (
                "A new AUTHOR_HMAC_KEY was generated for author pseudonymization. "
                "Keep it stable across runs; losing it makes existing author "
                "hashes irreconcilable. The key value is intentionally not logged."
            ),
        },
    )


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
