"""Server-issued, single-use approval tokens (PLAN §9).

A token is minted only when a human approves, handed in memory to the
executor, and stored as a SHA-256 hash. It never reaches the browser or a log.
The executor accepts it once, before its own short expiry.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime

# Stored in `approvals.token_hash` until a token is minted. A SHA-256 hex
# digest is 64 hex characters, so no token can ever hash to this value.
UNISSUED = "unissued"


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def mint() -> tuple[str, str]:
    """A fresh token and its hash. Only the hash is persisted."""
    token = secrets.token_urlsafe(32)
    return token, token_hash(token)


def check(
    token: str,
    *,
    stored_hash: str,
    expires_at: datetime | None,
    used_at: datetime | None,
    now: datetime,
) -> str | None:
    """None when the token may be used now, otherwise why it may not."""
    if stored_hash == UNISSUED or expires_at is None:
        return "no token was issued for this approval"
    if used_at is not None:
        return "token already used"
    if not hmac.compare_digest(token_hash(token), stored_hash):
        return "token does not match"
    if expires_at <= now:
        return "token expired"
    return None


__all__ = ["UNISSUED", "check", "mint", "token_hash"]
