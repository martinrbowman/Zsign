import json
import os
import shutil

import pytest

from zsign.gui.controller import ControllerError, VaultController
from zsign.vault.models import LifecycleState

PASSPHRASE = "correct horse battery staple"


def _created_controller(tmp_path):
    controller = VaultController()
    controller.create_vault(tmp_path / "vault.json", protection="passphrase", passphrase=PASSPHRASE)
    return controller


def test_status_before_any_vault():
    controller = VaultController()
    status = controller.status
    assert status.path is None
    assert not status.exists
    assert not status.is_unlocked


def test_create_vault_unlocks_immediately(tmp_path):
    controller = _created_controller(tmp_path)
    assert controller.status.is_unlocked
    assert controller.status.mode == "passphrase"


def test_create_vault_refuses_existing_path(tmp_path):
    controller = _created_controller(tmp_path)
    other = VaultController()
    with pytest.raises(ControllerError):
        other.create_vault(controller.status.path, protection="passphrase", passphrase=PASSPHRASE)


def test_generate_key_requires_unlocked_vault():
    controller = VaultController()
    with pytest.raises(ControllerError):
        controller.generate_key("k", "p")


def test_generate_key_requires_label_and_project(tmp_path):
    controller = _created_controller(tmp_path)
    with pytest.raises(ControllerError):
        controller.generate_key("", "project")
    with pytest.raises(ControllerError):
        controller.generate_key("label", "")


def test_generate_list_revoke_roundtrip(tmp_path):
    controller = _created_controller(tmp_path)
    record = controller.generate_key("release-key", "product-a")
    assert record in controller.list_keys()

    revoked = controller.revoke_key(record.key_id)
    assert revoked.lifecycle_state is LifecycleState.REVOKED


def test_export_public_pem(tmp_path):
    controller = _created_controller(tmp_path)
    record = controller.generate_key("k", "p")
    assert controller.export_public_pem(record.key_id) == record.public_key_pem


def test_lock_blocks_further_operations(tmp_path):
    controller = _created_controller(tmp_path)
    controller.generate_key("k", "p")
    controller.lock()
    assert not controller.status.is_unlocked
    with pytest.raises(ControllerError):
        controller.list_keys()


def test_open_and_unlock_existing_vault(tmp_path):
    path = tmp_path / "vault.json"
    first = VaultController()
    first.create_vault(path, protection="passphrase", passphrase=PASSPHRASE)
    record = first.generate_key("k", "p")
    first.lock()

    second = VaultController()
    second.open_vault(path)
    assert not second.status.is_unlocked
    second.unlock(passphrase=PASSPHRASE)
    assert second.status.is_unlocked
    assert record.key_id in {k.key_id for k in second.list_keys()}


def test_unlock_wrong_passphrase_reports_controller_error(tmp_path):
    path = tmp_path / "vault.json"
    first = VaultController()
    first.create_vault(path, protection="passphrase", passphrase=PASSPHRASE)
    first.lock()

    second = VaultController()
    second.open_vault(path)
    with pytest.raises(ControllerError):
        second.unlock(passphrase="wrong")


def test_open_vault_missing_file(tmp_path):
    controller = VaultController()
    with pytest.raises(ControllerError):
        controller.open_vault(tmp_path / "nope.json")


def test_import_key_through_controller(tmp_path):
    from zsign.vault import crypto as crypto_module

    controller = _created_controller(tmp_path)
    private_pem, public_pem = crypto_module.generate_p256_keypair()
    record = controller.import_key(private_pem, label="imported", project="p")
    assert record.public_key_pem == public_pem


@pytest.mark.skipif(shutil.which("imgtool") is None, reason="imgtool not on PATH")
def test_sign_image_through_controller(tmp_path):
    controller = _created_controller(tmp_path)
    record = controller.generate_key("k", "p")
    unsigned = tmp_path / "app.bin"
    unsigned.write_bytes(os.urandom(1024))
    output = tmp_path / "app.signed.bin"

    result = controller.sign_image(
        key_id=record.key_id,
        project="p",
        version="1.0.0+0",
        header_size=32,
        slot_size=65536,
        align=1,
        security_counter=None,
        pad_header=True,
        pad=False,
        confirm=False,
        input_path=unsigned,
        output_path=output,
    )
    assert output.exists()
    assert result.key_id == record.key_id


def test_sign_image_requires_unlocked_vault(tmp_path):
    controller = VaultController()
    with pytest.raises(ControllerError):
        controller.sign_image(
            key_id="nope", project="p", version="1.0.0+0", header_size=32,
            slot_size=1024, align=1, security_counter=None, pad_header=True,
            pad=False, confirm=False, input_path=tmp_path / "a", output_path=tmp_path / "b",
        )


def test_audit_log_created_alongside_vault(tmp_path):
    controller = _created_controller(tmp_path)
    assert controller.audit_log is not None
    records = controller.audit_log.read_all()
    assert records[0].event_type == "vault.create"


def test_generate_key_is_audited(tmp_path):
    controller = _created_controller(tmp_path)
    record = controller.generate_key("release", "product-a")
    events = [r for r in controller.audit_log.read_all() if r.event_type == "key.generate"]
    assert len(events) == 1
    assert events[0].key_id == record.key_id
    assert events[0].project == "product-a"
    assert events[0].details["fingerprint"] == record.public_fingerprint


def test_revoke_is_audited(tmp_path):
    controller = _created_controller(tmp_path)
    record = controller.generate_key("k", "p")
    controller.revoke_key(record.key_id)
    event_types = [r.event_type for r in controller.audit_log.read_all()]
    assert "key.revoke" in event_types


def test_lock_unlock_are_audited(tmp_path):
    controller = _created_controller(tmp_path)
    controller.lock()
    controller.unlock(passphrase=PASSPHRASE)
    event_types = [r.event_type for r in controller.audit_log.read_all()]
    assert "vault.lock" in event_types
    assert "vault.unlock" in event_types


def test_close_vault_resets_to_no_vault_state(tmp_path):
    controller = _created_controller(tmp_path)
    path = controller.status.path
    controller.close_vault()
    status = controller.status
    assert status.path is None
    assert not status.is_unlocked
    with pytest.raises(ControllerError):
        controller.list_keys()
    # The audit file itself still exists and recorded the close -- only
    # the controller's in-memory reference to it was dropped.
    from zsign.audit import AuditLog
    from zsign.gui.controller import _audit_path
    events = [r.event_type for r in AuditLog(_audit_path(path)).read_all()]
    assert "vault.close" in events


def test_close_vault_then_reopen(tmp_path):
    controller = _created_controller(tmp_path)
    path = controller.status.path
    record = controller.generate_key("k", "p")
    controller.close_vault()

    controller.open_vault(path)
    controller.unlock(passphrase=PASSPHRASE)
    assert record.key_id in {k.key_id for k in controller.list_keys()}


def test_unlock_failure_is_audited_without_leaking_passphrase(tmp_path):
    controller = _created_controller(tmp_path)
    controller.lock()
    with pytest.raises(ControllerError):
        controller.unlock(passphrase="wrong-passphrase-value")
    records = controller.audit_log.read_all()
    failure = [r for r in records if r.event_type == "vault.unlock_failed"]
    assert len(failure) == 1
    assert "wrong-passphrase-value" not in json.dumps(failure[0].details)


@pytest.mark.skipif(shutil.which("imgtool") is None, reason="imgtool not on PATH")
def test_sign_image_audit_id_matches_record(tmp_path):
    controller = _created_controller(tmp_path)
    record = controller.generate_key("k", "p")
    unsigned = tmp_path / "app.bin"
    unsigned.write_bytes(os.urandom(1024))

    result = controller.sign_image(
        key_id=record.key_id, project="p", version="1.0.0+0", header_size=32,
        slot_size=65536, align=1, security_counter=None, pad_header=True,
        pad=False, confirm=False,
        input_path=unsigned, output_path=tmp_path / "out.bin",
    )
    matching = [r for r in controller.audit_log.read_all() if r.record_id == result.audit_id]
    assert len(matching) == 1
    assert matching[0].event_type == "sign.completed"


@pytest.mark.skipif(shutil.which("imgtool") is None, reason="imgtool not on PATH")
def test_sign_image_rejects_security_counter_regression(tmp_path):
    controller = _created_controller(tmp_path)
    record = controller.generate_key("k", "p")
    unsigned = tmp_path / "app.bin"
    unsigned.write_bytes(os.urandom(1024))

    controller.sign_image(
        key_id=record.key_id, project="p", version="1.0.0+0", header_size=32,
        slot_size=65536, align=1, security_counter=5, pad_header=True,
        pad=False, confirm=False,
        input_path=unsigned, output_path=tmp_path / "out1.bin",
    )
    with pytest.raises(ControllerError, match="security_counter"):
        controller.sign_image(
            key_id=record.key_id, project="p", version="1.0.1+0", header_size=32,
            slot_size=65536, align=1, security_counter=3, pad_header=True,
            pad=False, confirm=False,
            input_path=unsigned, output_path=tmp_path / "out2.bin",
        )
    failures = [r for r in controller.audit_log.read_all() if r.event_type == "sign.failed"]
    assert any(f.details.get("reason") == "security_counter_regression" for f in failures)


@pytest.mark.skipif(shutil.which("imgtool") is None, reason="imgtool not on PATH")
def test_sign_image_allows_equal_or_higher_security_counter(tmp_path):
    controller = _created_controller(tmp_path)
    record = controller.generate_key("k", "p")
    unsigned = tmp_path / "app.bin"
    unsigned.write_bytes(os.urandom(1024))

    controller.sign_image(
        key_id=record.key_id, project="p", version="1.0.0+0", header_size=32,
        slot_size=65536, align=1, security_counter=5, pad_header=True,
        pad=False, confirm=False,
        input_path=unsigned, output_path=tmp_path / "out1.bin",
    )
    # Equal is allowed (only strictly-lower is a regression).
    controller.sign_image(
        key_id=record.key_id, project="p", version="1.0.1+0", header_size=32,
        slot_size=65536, align=1, security_counter=5, pad_header=True,
        pad=False, confirm=False,
        input_path=unsigned, output_path=tmp_path / "out2.bin",
    )
    controller.sign_image(
        key_id=record.key_id, project="p", version="1.0.2+0", header_size=32,
        slot_size=65536, align=1, security_counter=9, pad_header=True,
        pad=False, confirm=False,
        input_path=unsigned, output_path=tmp_path / "out3.bin",
    )
