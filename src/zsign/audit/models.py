from __future__ import annotations

import dataclasses
import hashlib
import json

GENESIS_HASH = "0" * 64


def _canonical_bytes(payload: dict) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode(
        "utf-8"
    )


@dataclasses.dataclass(frozen=True)
class AuditRecord:
    """One hash-chained audit entry.

    `details` must never contain private key material, KEKs,
    passphrases, or bearer tokens -- same rule spec.md states for the
    on-disk audit log, enforced here by caller discipline (every
    append() call site in this codebase is reviewed for this).
    """

    record_id: str
    timestamp: str
    event_type: str
    actor: str
    project: str | None
    key_id: str | None
    details: dict
    prev_hash: str
    record_hash: str

    def _hashable_fields(self) -> dict:
        return {
            "record_id": self.record_id,
            "timestamp": self.timestamp,
            "event_type": self.event_type,
            "actor": self.actor,
            "project": self.project,
            "key_id": self.key_id,
            "details": self.details,
            "prev_hash": self.prev_hash,
        }

    def compute_hash(self) -> str:
        return hashlib.sha256(_canonical_bytes(self._hashable_fields())).hexdigest()

    def to_dict(self) -> dict:
        return {**self._hashable_fields(), "record_hash": self.record_hash}

    @staticmethod
    def from_dict(data: dict) -> "AuditRecord":
        return AuditRecord(
            record_id=data["record_id"],
            timestamp=data["timestamp"],
            event_type=data["event_type"],
            actor=data["actor"],
            project=data.get("project"),
            key_id=data.get("key_id"),
            details=data.get("details", {}),
            prev_hash=data["prev_hash"],
            record_hash=data["record_hash"],
        )
