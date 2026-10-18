import json

import pytest

from zsign.audit import AuditIntegrityError, AuditLog
from zsign.audit.models import GENESIS_HASH


def test_first_record_chains_from_genesis(tmp_path):
    log = AuditLog(tmp_path / "audit.jsonl")
    record = log.append("vault.create", "gui-operator", {})
    assert record.prev_hash == GENESIS_HASH
    assert record.record_hash == record.compute_hash()


def test_chain_links_sequential_records(tmp_path):
    log = AuditLog(tmp_path / "audit.jsonl")
    first = log.append("vault.create", "gui-operator", {})
    second = log.append("key.generate", "gui-operator", {"key_id": "k1"})
    assert second.prev_hash == first.record_hash


def test_verify_chain_passes_on_untouched_log(tmp_path):
    log = AuditLog(tmp_path / "audit.jsonl")
    for i in range(5):
        log.append("sign.completed", "gui-operator", {"i": i})
    log.verify_chain()  # no raise


def test_verify_chain_detects_tampered_field(tmp_path):
    path = tmp_path / "audit.jsonl"
    log = AuditLog(path)
    log.append("vault.create", "gui-operator", {})
    log.append("key.generate", "gui-operator", {"key_id": "k1"})

    lines = path.read_text().splitlines()
    tampered = json.loads(lines[0])
    tampered["actor"] = "attacker"
    lines[0] = json.dumps(tampered)
    path.write_text("\n".join(lines) + "\n")

    with pytest.raises(AuditIntegrityError):
        AuditLog(path).verify_chain()


def test_verify_chain_detects_reordered_records(tmp_path):
    path = tmp_path / "audit.jsonl"
    log = AuditLog(path)
    log.append("vault.create", "gui-operator", {})
    log.append("key.generate", "gui-operator", {"key_id": "k1"})

    lines = path.read_text().splitlines()
    path.write_text("\n".join(reversed(lines)) + "\n")

    with pytest.raises(AuditIntegrityError):
        AuditLog(path).verify_chain()


def test_verify_chain_detects_deleted_record(tmp_path):
    path = tmp_path / "audit.jsonl"
    log = AuditLog(path)
    log.append("vault.create", "gui-operator", {})
    log.append("key.generate", "gui-operator", {"key_id": "k1"})
    log.append("key.retire", "gui-operator", {"key_id": "k1"})

    lines = path.read_text().splitlines()
    path.write_text(lines[0] + "\n" + lines[2] + "\n")  # drop the middle record

    with pytest.raises(AuditIntegrityError):
        AuditLog(path).verify_chain()


def test_reopening_log_continues_the_chain(tmp_path):
    path = tmp_path / "audit.jsonl"
    first = AuditLog(path).append("vault.create", "gui-operator", {})
    second = AuditLog(path).append("key.generate", "gui-operator", {"key_id": "k1"})
    assert second.prev_hash == first.record_hash
    AuditLog(path).verify_chain()


def test_query_filters_by_key_id(tmp_path):
    log = AuditLog(tmp_path / "audit.jsonl")
    log.append("key.generate", "gui-operator", {}, key_id="k1")
    log.append("key.generate", "gui-operator", {}, key_id="k2")
    records, cursor = log.query(key_id="k1")
    assert len(records) == 1
    assert records[0].key_id == "k1"
    assert cursor is None


def test_query_paginates_with_cursor(tmp_path):
    log = AuditLog(tmp_path / "audit.jsonl")
    for i in range(5):
        log.append("sign.completed", "gui-operator", {"i": i})

    page1, cursor1 = log.query(limit=2)
    assert len(page1) == 2
    assert cursor1 == page1[-1].record_id

    page2, cursor2 = log.query(limit=2, cursor=cursor1)
    assert len(page2) == 2
    assert page2[0].details["i"] == 2

    page3, cursor3 = log.query(limit=2, cursor=cursor2)
    assert len(page3) == 1
    assert cursor3 is None


def test_query_filters_by_time_range(tmp_path):
    log = AuditLog(tmp_path / "audit.jsonl")
    log.append("sign.completed", "gui-operator", {})
    records, _ = log.query(since="2000-01-01T00:00:00Z")
    assert len(records) == 1
    records, _ = log.query(until="2000-01-01T00:00:00Z")
    assert len(records) == 0


def test_last_security_counter_tracks_maximum(tmp_path):
    log = AuditLog(tmp_path / "audit.jsonl")
    assert log.last_security_counter("k1") is None
    log.append("sign.completed", "gui-operator", {"security_counter": 3}, key_id="k1")
    log.append("sign.completed", "gui-operator", {"security_counter": 1}, key_id="k1")
    log.append("sign.completed", "gui-operator", {"security_counter": 7}, key_id="k1")
    log.append("sign.completed", "gui-operator", {"security_counter": 99}, key_id="k2")
    assert log.last_security_counter("k1") == 7
    assert log.last_security_counter("k2") == 99


def test_last_security_counter_ignores_records_without_counter(tmp_path):
    log = AuditLog(tmp_path / "audit.jsonl")
    log.append("sign.completed", "gui-operator", {}, key_id="k1")
    assert log.last_security_counter("k1") is None


def test_audit_file_has_restrictive_permissions(tmp_path):
    import os
    import stat as stat_module

    path = tmp_path / "audit.jsonl"
    AuditLog(path).append("vault.create", "gui-operator", {})
    mode = stat_module.S_IMODE(os.stat(path).st_mode)
    assert mode == 0o600
