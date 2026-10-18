from __future__ import annotations

import dataclasses
import datetime
import json
import os
import stat
import threading
import uuid
from pathlib import Path

from zsign.audit.models import GENESIS_HASH, AuditRecord


class AuditIntegrityError(Exception):
    """Raised when the hash chain is broken: a record's stored hash
    doesn't match what's recomputed from its own fields, or its
    prev_hash doesn't match the previous record's hash."""


def _utcnow_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class AuditLog:
    """Append-only, hash-chained audit log. Each record's hash covers
    its own fields plus the previous record's hash, so editing or
    reordering any entry breaks the chain from that point forward --
    detectable via verify_chain(), not prevented (this is a local file;
    external checkpoint anchoring is deliberately out of scope for now,
    per spec.md's qualification that a local file alone isn't tamper-
    proof, only tamper-evident).
    """

    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.Lock()
        self._last_hash = self._read_last_hash()

    def _read_last_hash(self) -> str:
        if not self.path.exists():
            return GENESIS_HASH
        last_hash = GENESIS_HASH
        with self.path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if line:
                    last_hash = json.loads(line)["record_hash"]
        return last_hash

    def append(
        self,
        event_type: str,
        actor: str,
        details: dict,
        project: str | None = None,
        key_id: str | None = None,
        record_id: str | None = None,
    ) -> AuditRecord:
        with self._lock:
            record = AuditRecord(
                record_id=record_id or str(uuid.uuid4()),
                timestamp=_utcnow_iso(),
                event_type=event_type,
                actor=actor,
                project=project,
                key_id=key_id,
                details=details,
                prev_hash=self._last_hash,
                record_hash="",
            )
            record = dataclasses.replace(record, record_hash=record.compute_hash())

            is_new_file = not self.path.exists()
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record.to_dict(), sort_keys=True) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            if is_new_file:
                os.chmod(self.path, stat.S_IRUSR | stat.S_IWUSR)

            self._last_hash = record.record_hash
            return record

    def read_all(self) -> list[AuditRecord]:
        if not self.path.exists():
            return []
        records = []
        with self.path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if line:
                    records.append(AuditRecord.from_dict(json.loads(line)))
        return records

    def verify_chain(self) -> None:
        expected_prev = GENESIS_HASH
        for record in self.read_all():
            if record.prev_hash != expected_prev:
                raise AuditIntegrityError(
                    f"record {record.record_id} prev_hash mismatch: "
                    f"expected {expected_prev}, got {record.prev_hash}"
                )
            if record.record_hash != record.compute_hash():
                raise AuditIntegrityError(
                    f"record {record.record_id} hash mismatch: stored hash does not "
                    f"match its own fields (tampered or corrupted)"
                )
            expected_prev = record.record_hash

    def query(
        self,
        since: str | None = None,
        until: str | None = None,
        key_id: str | None = None,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> tuple[list[AuditRecord], str | None]:
        """Filter by ISO-8601 timestamp range / key_id, then paginate.
        `cursor` is an opaque record_id: resume strictly after it."""
        records = self.read_all()
        if since is not None:
            records = [r for r in records if r.timestamp >= since]
        if until is not None:
            records = [r for r in records if r.timestamp <= until]
        if key_id is not None:
            records = [r for r in records if r.key_id == key_id]

        if cursor is not None:
            index = next((i for i, r in enumerate(records) if r.record_id == cursor), None)
            records = records[index + 1 :] if index is not None else []

        if limit is not None and len(records) > limit:
            page = records[:limit]
            next_cursor = page[-1].record_id
            return page, next_cursor
        return records, None

    def last_security_counter(self, key_id: str) -> int | None:
        """Highest security_counter among this key's completed signs.
        The audit log is the source of truth for rollback enforcement
        rather than separate mutable state."""
        highest = None
        for record in self.read_all():
            if record.event_type != "sign.completed" or record.key_id != key_id:
                continue
            counter = record.details.get("security_counter")
            if counter is not None and (highest is None or counter > highest):
                highest = counter
        return highest
