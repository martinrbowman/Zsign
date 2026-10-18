"""Smoke tests for the PySide6 shell. tests/conftest.py sets
QT_QPA_PLATFORM=offscreen before any Qt import, so these work in
CI/headless environments with no real display.
"""
import os
import shutil

import pytest

pytest.importorskip("PySide6.QtWidgets")
from PySide6.QtWidgets import QApplication, QDialog, QLabel, QMessageBox  # noqa: E402

from zsign.gui.main_window import MainWindow  # noqa: E402
from zsign.vault.models import Algorithm, LifecycleState  # noqa: E402

PASSPHRASE = "correct horse battery staple"


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def test_main_window_constructs(qapp):
    window = MainWindow()
    assert window.windowTitle().startswith("Zsign")
    assert window.status_label.text() == "No vault open."


def test_create_unlock_generate_sign_flow_via_controller(qapp, tmp_path):
    """Drives MainWindow through its actual action_* handlers (same code
    path buttons use), bypassing only the modal dialogs themselves by
    calling the controller directly where a dialog would block -- this
    exercises the real refresh/table/idle-timer wiring end to end."""
    window = MainWindow()

    vault_path = tmp_path / "vault.json"
    window.controller.create_vault(vault_path, protection="passphrase", passphrase=PASSPHRASE)
    window._refresh()
    assert window.status_label.text().endswith("[unlocked]")

    record = window.controller.generate_key("ci-key", "ci-project")
    window._refresh()
    assert window.table.rowCount() == 1
    assert window.table.item(0, 0).text() == "ci-key"

    window.controller.revoke_key(record.key_id)
    window._refresh()
    assert window.table.item(0, 4).text() == LifecycleState.REVOKED.value

    window.action_lock()
    assert window.status_label.text().endswith("[locked]")
    assert window.table.rowCount() == 0


@pytest.mark.skipif(shutil.which("imgtool") is None, reason="imgtool not on PATH")
def test_sign_image_end_to_end_through_controller(qapp, tmp_path):
    window = MainWindow()
    vault_path = tmp_path / "vault.json"
    window.controller.create_vault(vault_path, protection="passphrase", passphrase=PASSPHRASE)
    record = window.controller.generate_key("ci-key", "ci-project")

    unsigned = tmp_path / "app.bin"
    unsigned.write_bytes(os.urandom(1024))
    output = tmp_path / "app.signed.bin"

    result = window.controller.sign_image(
        key_id=record.key_id, project="ci-project", version="1.0.0+0",
        header_size=32, slot_size=65536, align=1, security_counter=None,
        pad_header=True, pad=False, confirm=False,
        input_path=unsigned, output_path=output,
    )
    assert output.exists()
    assert result.key_id == record.key_id


def test_create_vault_dialog_constructs(qapp):
    from zsign.gui.main_window import CreateVaultDialog

    dialog = CreateVaultDialog()
    assert dialog.windowTitle() == "Create Vault"
    path, protection, passphrase = dialog.values()
    assert protection == "keyring"
    assert passphrase is None
    dialog.deleteLater()


def test_generate_key_dialog_values(qapp):
    from zsign.gui.main_window import GenerateKeyDialog

    dialog = GenerateKeyDialog()
    dialog.label_edit.setText("my-key")
    dialog.project_edit.setText("my-project")
    label, project, algorithm = dialog.values()
    assert (label, project) == ("my-key", "my-project")
    assert algorithm is Algorithm.ECDSA_P256  # first enum member is the default selection
    dialog.deleteLater()


def test_generate_key_dialog_offers_all_algorithms(qapp):
    from zsign.gui.main_window import GenerateKeyDialog

    dialog = GenerateKeyDialog()
    offered = {dialog.algorithm_combo.itemData(i) for i in range(dialog.algorithm_combo.count())}
    assert offered == set(Algorithm)
    dialog.deleteLater()


def test_sign_image_dialog_values(qapp):
    from zsign.gui.main_window import SignImageDialog
    from zsign.vault.models import Algorithm

    class _Record:
        key_id = "abc"
        label = "k"
        public_fingerprint = "f" * 64
        lifecycle_state = LifecycleState.ACTIVE
        algorithm = Algorithm.ECDSA_P256

    dialog = SignImageDialog([_Record()])
    dialog.project_edit.setText("proj")
    dialog.input_edit.setText("/tmp/in.bin")
    dialog.output_edit.setText("/tmp/out.bin")
    values = dialog.values()
    assert values["key_id"] == "abc"
    assert values["project"] == "proj"
    assert values["header_size"] == 512
    dialog.deleteLater()


def test_issue_and_revoke_token_through_main_window(qapp, tmp_path):
    window = MainWindow()
    window.controller.create_vault(tmp_path / "vault.json", protection="passphrase", passphrase=PASSPHRASE)

    token = window.token_store.issue("sign", project="demo")
    window._audit_token_issue(token, "sign", "demo")
    token_id = token.split(".", 1)[0]

    events = [r.event_type for r in window.controller.audit_log.read_all()]
    assert "token.issue" in events
    assert token_id in [t.token_id for t in window.token_store.list_active()]

    from zsign.gui.main_window import ManageTokensDialog

    dialog = ManageTokensDialog(window.token_store, window.controller.audit_log)
    assert dialog.table.rowCount() == 1
    dialog.table.selectRow(0)
    dialog._revoke_selected()

    assert dialog.table.rowCount() == 0
    assert token_id not in [t.token_id for t in window.token_store.list_active()]
    events = [r.event_type for r in window.controller.audit_log.read_all()]
    assert "token.revoke" in events
    dialog.deleteLater()


def test_lock_icon_and_group_enabled_states_through_vault_lifecycle(qapp, tmp_path):
    from PySide6.QtWidgets import QPushButton

    window = MainWindow()

    assert window.lock_icon_label.text() == ""
    assert not window.keys_group.isEnabled()
    assert not window.audit_group.isEnabled()

    window.controller.create_vault(tmp_path / "vault.json", protection="passphrase", passphrase=PASSPHRASE)
    window._refresh()
    assert window.lock_icon_label.text() == "🔓"
    assert window.keys_group.isEnabled()
    assert window.audit_group.isEnabled()

    window.controller.lock()
    window._refresh()
    assert window.lock_icon_label.text() == "🔒"
    assert not window.keys_group.isEnabled()
    vault_buttons = {b.text(): b.isEnabled() for b in window.vault_group.findChildren(QPushButton)}
    assert vault_buttons["Lock"] is False
    assert vault_buttons["Unlock…"] is True
    assert vault_buttons["Close Vault"] is True

    window.controller.close_vault()
    window._refresh()
    assert window.lock_icon_label.text() == ""
    vault_buttons = {b.text(): b.isEnabled() for b in window.vault_group.findChildren(QPushButton)}
    assert vault_buttons["Unlock…"] is False
    assert vault_buttons["Close Vault"] is False
    assert vault_buttons["New Vault…"] is True  # always available to start over


def test_close_vault_action_resets_ui(qapp, tmp_path):
    window = MainWindow()
    window.controller.create_vault(tmp_path / "vault.json", protection="passphrase", passphrase=PASSPHRASE)
    window.controller.generate_key("k", "p")
    window._refresh()
    assert window.table.rowCount() == 1

    window.action_close_vault()
    assert window.status_label.text() == "No vault open."
    assert window.table.rowCount() == 0


def test_audit_log_dialog_shows_records_and_verifies_chain(qapp, tmp_path, monkeypatch):
    from zsign.gui.main_window import AuditLogDialog

    window = MainWindow()
    window.controller.create_vault(tmp_path / "vault.json", protection="passphrase", passphrase=PASSPHRASE)
    window.controller.generate_key("k", "p")

    dialog = AuditLogDialog(window.controller.audit_log)
    assert dialog.table.rowCount() >= 2  # vault.create + key.generate
    events = {dialog.table.item(row, 1).text() for row in range(dialog.table.rowCount())}
    assert "key.generate" in events

    # _verify_chain() shows a real modal QMessageBox on success/failure;
    # under offscreen with nothing to click it, .exec() blocks forever.
    # Stub it out -- we're testing the chain check, not the dialog.
    shown = []
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: shown.append(a))
    monkeypatch.setattr(QMessageBox, "critical", lambda *a, **k: shown.append(a))
    dialog._verify_chain()
    assert len(shown) == 1
    dialog.deleteLater()


def test_view_audit_log_action_without_any_vault_shows_error(qapp, monkeypatch):
    window = MainWindow()
    errors = []
    monkeypatch.setattr(window, "_show_error", lambda message: errors.append(message))
    window.action_view_audit_log()
    assert len(errors) == 1


def test_buy_me_a_coffee_opens_correct_url(qapp, monkeypatch):
    from PySide6.QtGui import QDesktopServices

    from zsign.gui.main_window import BUY_ME_A_COFFEE_URL

    window = MainWindow()
    opened = []
    monkeypatch.setattr(QDesktopServices, "openUrl", lambda url: opened.append(url.toString()))
    window._open_buy_me_a_coffee()
    assert opened == [BUY_ME_A_COFFEE_URL]


def test_about_dialog_shows_license_and_contact(qapp):
    import importlib.metadata

    from zsign.gui.main_window import AboutDialog, CONTACT_EMAIL, LICENSE_STATEMENT

    version = importlib.metadata.version("zsign")
    dialog = AboutDialog(version)
    labels = [label.text() for label in dialog.findChildren(QLabel)]
    assert LICENSE_STATEMENT in labels
    assert f"Contact: {CONTACT_EMAIL}" in labels
    assert f"Version {version}" in labels
    dialog.deleteLater()


def test_action_about_constructs_dialog_without_blocking(qapp, monkeypatch):
    # action_about() calls dialog.exec(), which blocks waiting for a
    # real user click. Stub QDialog.exec itself so this test exercises
    # the action's wiring (version lookup, dialog construction) without
    # needing a click that will never come under offscreen.
    from zsign.gui.main_window import AboutDialog

    window = MainWindow()
    monkeypatch.setattr(QDialog, "exec", lambda self: QDialog.DialogCode.Accepted)
    window.action_about()  # no exception, no hang
