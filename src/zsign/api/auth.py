from __future__ import annotations

import dataclasses
import hashlib
import hmac
import secrets
import time
from typing import Literal

Scope = Literal["sign", "admin"]


class TokenError(Exception):
    """Raised for any malformed/unknown/expired/mismatched token."""


@dataclasses.dataclass(frozen=True)
class Token:
    token_id: str
    scope: Scope
    project: str | None
    expires_at: float


@dataclasses.dataclass(frozen=True)
class _StoredToken:
    scope: Scope
    project: str | None
    expires_at: float
    secret_hash: str


class TokenStore:
    """In-memory bearer tokens, issued only by the GUI operator:
    short-lived, scoped client credentials, shown once at issuance.
    Nothing here persists across a restart -- a restarted agent has no
    tokens until the operator issues new ones."""

    def __init__(self):
        self._tokens: dict[str, _StoredToken] = {}

    def issue(self, scope: Scope, project: str | None = None, ttl_seconds: int = 3600) -> str:
        if scope == "sign" and not project:
            raise ValueError("sign-scoped tokens require a project")
        if scope == "admin":
            project = None
        token_id = secrets.token_hex(8)
        secret = secrets.token_urlsafe(32)
        self._tokens[token_id] = _StoredToken(
            scope=scope,
            project=project,
            expires_at=time.time() + ttl_seconds,
            secret_hash=hashlib.sha256(secret.encode("ascii")).hexdigest(),
        )
        return f"{token_id}.{secret}"

    def verify(self, plaintext: str) -> Token:
        try:
            token_id, secret = plaintext.split(".", 1)
        except ValueError:
            raise TokenError("malformed token") from None
        stored = self._tokens.get(token_id)
        if stored is None:
            raise TokenError("unknown token")
        if time.time() > stored.expires_at:
            del self._tokens[token_id]
            raise TokenError("token expired")
        secret_hash = hashlib.sha256(secret.encode("ascii")).hexdigest()
        if not hmac.compare_digest(secret_hash, stored.secret_hash):
            raise TokenError("invalid token")
        return Token(
            token_id=token_id, scope=stored.scope, project=stored.project,
            expires_at=stored.expires_at,
        )

    def revoke(self, token_id: str) -> None:
        self._tokens.pop(token_id, None)

    def active_count(self) -> int:
        now = time.time()
        return sum(1 for t in self._tokens.values() if t.expires_at > now)

    def list_active(self) -> list[Token]:
        """Metadata only -- token_id is an opaque identifier, not a
        secret (the secret is the part after the dot, never stored in
        plaintext, only as a hash). Safe to display in the GUI."""
        now = time.time()
        return [
            Token(token_id=tid, scope=t.scope, project=t.project, expires_at=t.expires_at)
            for tid, t in self._tokens.items()
            if t.expires_at > now
        ]
