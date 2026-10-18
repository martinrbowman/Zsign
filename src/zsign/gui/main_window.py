from __future__ import annotations

import importlib.metadata
import time
from pathlib import Path

from PySide6.QtCore import QEvent, QTimer, QUrl, Qt
from PySide6.QtGui import QDesktopServices, QFont
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from zsign.api import ApiServer, KeyLockRegistry, TokenStore, create_app
from zsign.gui.approval_bridge import APPROVAL_TIMEOUT_SECONDS, GuiApprovalBridge
from zsign.gui.controller import ControllerError, VaultController
from zsign.vault.models import Algorithm, LifecycleState

DEFAULT_API_PORT = 8765

DEFAULT_VAULT_PATH = Path.home() / ".zsign" / "vault.json"
IDLE_LOCK_TIMEOUT_MS = 10 * 60 * 1000  # 10 minutes, per spec.md's auto-lock requirement
BUY_ME_A_COFFEE_URL = "https://buymeacoffee.com/redwolfelectronics"
CONTACT_EMAIL = "martinb1974@gmail.com"
LICENSE_STATEMENT = (
    "Free for non-commercial use. Contact the author for commercial licensing."
)

_COLUMNS = ("Label", "Project", "Algorithm", "Fingerprint", "Lifecycle", "Created")


class CreateVaultDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Create Vault")
        layout = QFormLayout(self)

        self.path_edit = QLineEdit(str(DEFAULT_VAULT_PATH))
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        path_row = QWidget()
        path_layout = QVBoxLayout(path_row)
        path_layout.setContentsMargins(0, 0, 0, 0)
        path_layout.addWidget(self.path_edit)
        path_layout.addWidget(browse)
        layout.addRow("Vault file:", path_row)

        self.protection_combo = QComboBox()
        self.protection_combo.addItems(["keyring", "passphrase"])
        self.protection_combo.currentTextChanged.connect(self._update_passphrase_visibility)
        layout.addRow("Protection:", self.protection_combo)

        self.passphrase_edit = QLineEdit()
        self.passphrase_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.passphrase_label = QLabel("Passphrase:")
        layout.addRow(self.passphrase_label, self.passphrase_edit)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

        self._update_passphrase_visibility(self.protection_combo.currentText())

    def _browse(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Create Vault File", self.path_edit.text())
        if path:
            self.path_edit.setText(path)

    def _update_passphrase_visibility(self, protection: str) -> None:
        visible = protection == "passphrase"
        self.passphrase_label.setVisible(visible)
        self.passphrase_edit.setVisible(visible)

    def values(self) -> tuple[Path, str, str | None]:
        protection = self.protection_combo.currentText()
        passphrase = self.passphrase_edit.text() if protection == "passphrase" else None
        return Path(self.path_edit.text()), protection, passphrase


class GenerateKeyDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Generate Key")
        layout = QFormLayout(self)
        self.label_edit = QLineEdit()
        self.project_edit = QLineEdit()
        self.algorithm_combo = QComboBox()
        for algorithm in Algorithm:
            self.algorithm_combo.addItem(algorithm.value, algorithm)
        layout.addRow("Label:", self.label_edit)
        layout.addRow("Project:", self.project_edit)
        layout.addRow("Algorithm:", self.algorithm_combo)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

    def values(self) -> tuple[str, str, Algorithm]:
        return self.label_edit.text(), self.project_edit.text(), self.algorithm_combo.currentData()


class SignImageDialog(QDialog):
    """Manual sign flow: pick a key and an unsigned image directly in
    the GUI, with the same key-fingerprint/project/version confirmation
    an API-driven approval would show. Use this for ad hoc or one-off
    signing; API clients get their own approval dialog instead
    (ApprovalRequestDialog)."""

    def __init__(self, key_records, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Sign Image")
        self._key_records = {r.key_id: r for r in key_records}
        layout = QFormLayout(self)

        self.key_combo = QComboBox()
        for record in key_records:
            if record.lifecycle_state is LifecycleState.ACTIVE:
                self.key_combo.addItem(
                    f"{record.label}  [{record.public_fingerprint[:16]}…]", record.key_id
                )
        self.key_combo.currentIndexChanged.connect(self._update_fingerprint_label)
        layout.addRow("Key:", self.key_combo)

        self.fingerprint_label = QLabel("-")
        layout.addRow("Key fingerprint:", self.fingerprint_label)

        self.input_edit = QLineEdit()
        browse_in = QPushButton("Browse…")
        browse_in.clicked.connect(self._browse_input)
        input_row = QWidget()
        input_layout = QVBoxLayout(input_row)
        input_layout.setContentsMargins(0, 0, 0, 0)
        input_layout.addWidget(self.input_edit)
        input_layout.addWidget(browse_in)
        layout.addRow("Unsigned image:", input_row)

        self.output_edit = QLineEdit()
        browse_out = QPushButton("Browse…")
        browse_out.clicked.connect(self._browse_output)
        output_row = QWidget()
        output_layout = QVBoxLayout(output_row)
        output_layout.setContentsMargins(0, 0, 0, 0)
        output_layout.addWidget(self.output_edit)
        output_layout.addWidget(browse_out)
        layout.addRow("Signed output:", output_row)

        self.project_edit = QLineEdit()
        self.version_edit = QLineEdit("1.0.0+0")
        self.header_size_edit = QLineEdit("512")
        self.slot_size_edit = QLineEdit("131072")
        self.align_combo = QComboBox()
        self.align_combo.addItems(["1", "2", "4", "8", "16", "32"])
        self.security_counter_edit = QLineEdit()
        self.pad_header_combo = QComboBox()
        self.pad_header_combo.addItems(["false", "true"])

        layout.addRow("Project:", self.project_edit)
        layout.addRow("Version:", self.version_edit)
        layout.addRow("Header size:", self.header_size_edit)
        layout.addRow("Slot size:", self.slot_size_edit)
        layout.addRow("Align:", self.align_combo)
        layout.addRow("Security counter (blank = none):", self.security_counter_edit)
        layout.addRow("Pad header:", self.pad_header_combo)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

        self._update_fingerprint_label(self.key_combo.currentIndex())

    def _update_fingerprint_label(self, _index: int) -> None:
        key_id = self.key_combo.currentData()
        record = self._key_records.get(key_id)
        self.fingerprint_label.setText(record.public_fingerprint if record else "-")

    def _browse_input(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Unsigned Image")
        if path:
            self.input_edit.setText(path)

    def _browse_output(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Signed Output")
        if path:
            self.output_edit.setText(path)

    def values(self) -> dict:
        security_counter_text = self.security_counter_edit.text().strip()
        return {
            "key_id": self.key_combo.currentData(),
            "project": self.project_edit.text(),
            "version": self.version_edit.text(),
            "header_size": int(self.header_size_edit.text()),
            "slot_size": int(self.slot_size_edit.text()),
            "align": int(self.align_combo.currentText()),
            "security_counter": int(security_counter_text) if security_counter_text else None,
            "pad_header": self.pad_header_combo.currentText() == "true",
            "pad": False,
            "confirm": False,
            "input_path": Path(self.input_edit.text()),
            "output_path": Path(self.output_edit.text()),
        }


class ApprovalRequestDialog(QDialog):
    """Shown when an API client submits a /v1/sign request. Auto-denies
    on timeout (matching the API-side handler's own timeout) so a stale
    dialog can't sit open and sign something long after the client gave
    up, per spec.md: "rejected and expired approvals must not sign."
    """

    def __init__(self, request, parent=None, timeout: float = APPROVAL_TIMEOUT_SECONDS):
        super().__init__(parent)
        self.setWindowTitle("Approve Signing Request")
        layout = QFormLayout(self)
        profile = request.profile
        rows = [
            ("Client:", request.client_label),
            ("Project:", request.project),
            ("Key fingerprint:", request.key_fingerprint),
            ("Version:", request.version),
            ("Image SHA-256:", request.image_sha256),
            ("Image size:", f"{request.image_size} bytes"),
            ("Header size:", str(profile.get("header_size"))),
            ("Slot size:", str(profile.get("slot_size"))),
            ("Security counter:", str(profile.get("security_counter"))),
        ]
        for label_text, value in rows:
            layout.addRow(label_text, QLabel(str(value)))

        buttons = QDialogButtonBox()
        approve = buttons.addButton("Approve", QDialogButtonBox.ButtonRole.AcceptRole)
        deny = buttons.addButton("Deny", QDialogButtonBox.ButtonRole.RejectRole)
        approve.clicked.connect(self.accept)
        deny.clicked.connect(self.reject)
        layout.addRow(buttons)

        self._timeout_timer = QTimer(self)
        self._timeout_timer.setSingleShot(True)
        self._timeout_timer.timeout.connect(self.reject)
        self._timeout_timer.start(int(timeout * 1000))


class ManageTokensDialog(QDialog):
    """List active API tokens and let the operator revoke one early,
    instead of waiting out its TTL. Token secrets are never shown here
    (or stored anywhere) -- only the opaque token_id, which is what
    TokenStore.revoke() needs."""

    def __init__(self, token_store, audit_log, parent=None):
        super().__init__(parent)
        self.token_store = token_store
        self.audit_log = audit_log
        self.setWindowTitle("Manage Tokens")
        layout = QVBoxLayout(self)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Token ID", "Scope", "Project", "Expires in"])
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        layout.addWidget(self.table)

        revoke_button = QPushButton("Revoke Selected")
        revoke_button.clicked.connect(self._revoke_selected)
        layout.addWidget(revoke_button)

        close_button = QPushButton("Close")
        close_button.clicked.connect(self.accept)
        layout.addWidget(close_button)

        self._refresh()

    def _refresh(self) -> None:
        self.table.setRowCount(0)
        for token in self.token_store.list_active():
            row = self.table.rowCount()
            self.table.insertRow(row)
            self.table.setItem(row, 0, QTableWidgetItem(token.token_id))
            self.table.setItem(row, 1, QTableWidgetItem(token.scope))
            self.table.setItem(row, 2, QTableWidgetItem(token.project or "-"))
            remaining = max(0, int(token.expires_at - time.time()))
            self.table.setItem(row, 3, QTableWidgetItem(f"{remaining}s"))

    def _revoke_selected(self) -> None:
        row = self.table.currentRow()
        if row < 0:
            return
        token_id = self.table.item(row, 0).text()
        scope = self.table.item(row, 1).text()
        project_text = self.table.item(row, 2).text()
        project = None if project_text == "-" else project_text

        self.token_store.revoke(token_id)
        if self.audit_log is not None:
            self.audit_log.append(
                "token.revoke", "gui-operator",
                {"token_id": token_id, "scope": scope}, project=project,
            )
        self._refresh()


class AuditLogDialog(QDialog):
    """Read-only view over the vault's hash-chained audit log
    (zsign.audit.AuditLog), plus a button to actually run
    verify_chain() rather than just trusting the file looks fine."""

    _COLUMNS = ("Timestamp", "Event", "Actor", "Project", "Key ID", "Details")

    def __init__(self, audit_log, parent=None):
        super().__init__(parent)
        self.audit_log = audit_log
        self.setWindowTitle("Audit Log")
        layout = QVBoxLayout(self)

        filter_row = QHBoxLayout()
        self.key_id_filter = QLineEdit()
        self.key_id_filter.setPlaceholderText("Filter by key_id (blank = all)")
        filter_button = QPushButton("Filter")
        filter_button.clicked.connect(self._refresh)
        filter_row.addWidget(self.key_id_filter)
        filter_row.addWidget(filter_button)
        layout.addLayout(filter_row)

        self.table = QTableWidget(0, len(self._COLUMNS))
        self.table.setHorizontalHeaderLabels(self._COLUMNS)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        layout.addWidget(self.table)

        button_row = QHBoxLayout()
        verify_button = QPushButton("Verify Chain Integrity")
        verify_button.clicked.connect(self._verify_chain)
        refresh_button = QPushButton("Refresh")
        refresh_button.clicked.connect(self._refresh)
        close_button = QPushButton("Close")
        close_button.clicked.connect(self.accept)
        button_row.addWidget(verify_button)
        button_row.addWidget(refresh_button)
        button_row.addStretch()
        button_row.addWidget(close_button)
        layout.addLayout(button_row)

        self._refresh()

    def _refresh(self) -> None:
        key_id = self.key_id_filter.text().strip() or None
        records, _ = self.audit_log.query(key_id=key_id, limit=500)
        self.table.setRowCount(0)
        for record in reversed(records):  # newest first
            row = self.table.rowCount()
            self.table.insertRow(row)
            self.table.setItem(row, 0, QTableWidgetItem(record.timestamp))
            self.table.setItem(row, 1, QTableWidgetItem(record.event_type))
            self.table.setItem(row, 2, QTableWidgetItem(record.actor))
            self.table.setItem(row, 3, QTableWidgetItem(record.project or "-"))
            self.table.setItem(row, 4, QTableWidgetItem(record.key_id or "-"))
            self.table.setItem(row, 5, QTableWidgetItem(str(record.details)))
        self.table.resizeColumnsToContents()

    def _verify_chain(self) -> None:
        try:
            self.audit_log.verify_chain()
        except Exception as exc:
            QMessageBox.critical(self, "Audit Chain", f"Tamper/corruption detected:\n{exc}")
            return
        QMessageBox.information(self, "Audit Chain", "Chain is intact: no tampering detected.")


class AboutDialog(QDialog):
    def __init__(self, version: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("About Zsign")
        layout = QVBoxLayout(self)

        title = QLabel("Zsign")
        title_font = QFont()
        title_font.setPointSize(16)
        title_font.setBold(True)
        title.setFont(title_font)
        layout.addWidget(title)

        layout.addWidget(QLabel("Secure firmware signing service for Zephyr/MCUboot"))
        layout.addWidget(QLabel(f"Version {version}"))
        layout.addWidget(QLabel(""))

        license_label = QLabel(LICENSE_STATEMENT)
        license_label.setWordWrap(True)
        layout.addWidget(license_label)
        layout.addWidget(QLabel(f"Contact: {CONTACT_EMAIL}"))
        layout.addWidget(QLabel(""))

        coffee_button = QPushButton("☕ Buy Me a Coffee")
        coffee_button.setFlat(True)
        coffee_button.setCursor(Qt.CursorShape.PointingHandCursor)
        coffee_button.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(BUY_ME_A_COFFEE_URL)))
        layout.addWidget(coffee_button)

        close_button = QPushButton("Close")
        close_button.clicked.connect(self.accept)
        layout.addWidget(close_button)


class MainWindow(QMainWindow):
    def __init__(self, controller: VaultController | None = None):
        super().__init__()
        self.setWindowTitle("Zsign - Zephyr Firmware - Signing key manager")
        self.controller = controller or VaultController()

        help_menu = self.menuBar().addMenu("Help")
        help_menu.addAction("About Zsign…", self.action_about)

        self.token_store = TokenStore()
        self.key_lock_registry = KeyLockRegistry()
        self.approval_bridge = GuiApprovalBridge()
        self.approval_bridge.approval_requested.connect(self._on_approval_requested)
        self.api_server: ApiServer | None = None

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)

        status_row = QHBoxLayout()
        self.lock_icon_label = QLabel()
        lock_font = QFont()
        lock_font.setPointSize(20)
        self.lock_icon_label.setFont(lock_font)
        status_row.addWidget(self.lock_icon_label)
        self.status_label = QLabel()
        status_row.addWidget(self.status_label)
        status_row.addStretch()
        layout.addLayout(status_row)

        self.api_status_label = QLabel()
        layout.addWidget(self.api_status_label)

        self.table = QTableWidget(0, len(_COLUMNS))
        self.table.setHorizontalHeaderLabels(_COLUMNS)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        layout.addWidget(self.table)

        groups_layout = QGridLayout()
        self.vault_group = self._build_group(
            "Vault",
            [
                ("New Vault…", self.action_new_vault),
                ("Open Vault…", self.action_open_vault),
                ("Unlock…", self.action_unlock),
                ("Lock", self.action_lock),
                ("Close Vault", self.action_close_vault),
            ],
        )
        self.keys_group = self._build_group(
            "Keys && Signing",
            [
                ("Generate Key…", self.action_generate_key),
                ("Import Key…", self.action_import_key),
                ("Export Public Key…", self.action_export_public_key),
                ("Revoke Key", self.action_revoke_key),
                ("Sign Image…", self.action_sign_image),
            ],
        )
        self.tokens_group = self._build_group(
            "Tokens",
            [
                ("Issue Signing Token…", self.action_issue_signing_token),
                ("Issue Admin Token…", self.action_issue_admin_token),
                ("Manage Tokens…", self.action_manage_tokens),
            ],
        )
        self.api_group = self._build_group(
            "API && Diagnostics",
            [
                ("Start API", self.action_start_api),
                ("Stop API", self.action_stop_api),
                ("Diagnostics…", self.action_diagnostics),
            ],
        )
        self.audit_group = self._build_group(
            "Audit Log",
            [
                ("View Audit Log…", self.action_view_audit_log),
            ],
        )
        groups_layout.addWidget(self.vault_group, 0, 0)
        groups_layout.addWidget(self.keys_group, 0, 1)
        groups_layout.addWidget(self.tokens_group, 0, 2)
        groups_layout.addWidget(self.api_group, 1, 0)
        groups_layout.addWidget(self.audit_group, 1, 1)

        coffee_button = QPushButton("☕ Buy Me a Coffee")
        coffee_button.setFlat(True)
        coffee_button.setCursor(Qt.CursorShape.PointingHandCursor)
        coffee_button.clicked.connect(self._open_buy_me_a_coffee)
        groups_layout.addWidget(coffee_button, 1, 2, Qt.AlignmentFlag.AlignBottom)

        layout.addLayout(groups_layout)

        self._idle_timer = QTimer(self)
        self._idle_timer.setSingleShot(True)
        self._idle_timer.timeout.connect(self._on_idle_timeout)
        self.installEventFilter(self)

        self._refresh()

    @staticmethod
    def _build_group(title: str, buttons: list[tuple[str, object]]) -> QGroupBox:
        group = QGroupBox(title)
        group_layout = QVBoxLayout(group)
        for text, handler in buttons:
            button = QPushButton(text)
            button.clicked.connect(handler)
            group_layout.addWidget(button)
        return group

    # -- idle auto-lock -------------------------------------------------

    def eventFilter(self, watched, event):
        if event.type() in (
            QEvent.Type.MouseMove,
            QEvent.Type.MouseButtonPress,
            QEvent.Type.KeyPress,
        ):
            self._reset_idle_timer()
        return super().eventFilter(watched, event)

    def _reset_idle_timer(self) -> None:
        if self.controller.status.is_unlocked:
            self._idle_timer.start(IDLE_LOCK_TIMEOUT_MS)
        else:
            self._idle_timer.stop()

    def _on_idle_timeout(self) -> None:
        self.action_lock()

    # -- actions ----------------------------------------------------------

    def action_new_vault(self) -> None:
        dialog = CreateVaultDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        path, protection, passphrase = dialog.values()
        try:
            self.controller.create_vault(path, protection=protection, passphrase=passphrase)
        except ControllerError as exc:
            self._show_error(str(exc))
            return
        self._refresh()

    def action_open_vault(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Open Vault", str(DEFAULT_VAULT_PATH.parent))
        if not path:
            return
        try:
            self.controller.open_vault(Path(path))
        except ControllerError as exc:
            self._show_error(str(exc))
            return
        self._refresh()

    def action_unlock(self) -> None:
        status = self.controller.status
        passphrase = None
        if status.mode == "passphrase":
            passphrase, ok = QInputDialog.getText(
                self, "Unlock Vault", "Passphrase:", QLineEdit.EchoMode.Password
            )
            if not ok:
                return
        try:
            self.controller.unlock(passphrase=passphrase)
        except ControllerError as exc:
            self._show_error(str(exc))
            return
        self._refresh()

    def action_lock(self) -> None:
        self.controller.lock()
        self._refresh()

    def action_close_vault(self) -> None:
        self.controller.close_vault()
        self._refresh()

    def action_generate_key(self) -> None:
        dialog = GenerateKeyDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        label, project, algorithm = dialog.values()
        try:
            self.controller.generate_key(label, project, algorithm=algorithm)
        except ControllerError as exc:
            self._show_error(str(exc))
            return
        self._refresh()

    def action_import_key(self) -> None:
        pem_path, _ = QFileDialog.getOpenFileName(self, "Import Private Key PEM")
        if not pem_path:
            return
        label, ok = QInputDialog.getText(self, "Import Key", "Label:")
        if not ok:
            return
        project, ok = QInputDialog.getText(self, "Import Key", "Project:")
        if not ok:
            return
        try:
            private_pem = Path(pem_path).read_bytes()
            self.controller.import_key(private_pem, label, project)
        except ControllerError as exc:
            self._show_error(str(exc))
            return
        self._refresh()

    def action_export_public_key(self) -> None:
        key_id = self._selected_key_id()
        if key_id is None:
            return
        out_path, _ = QFileDialog.getSaveFileName(self, "Export Public Key", f"{key_id}.pem")
        if not out_path:
            return
        try:
            pem = self.controller.export_public_pem(key_id)
        except ControllerError as exc:
            self._show_error(str(exc))
            return
        Path(out_path).write_bytes(pem)

    def action_revoke_key(self) -> None:
        key_id = self._selected_key_id()
        if key_id is None:
            return
        confirm = QMessageBox.question(
            self, "Revoke Key", "Revocation cannot be undone. Continue?"
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        try:
            self.controller.revoke_key(key_id)
        except ControllerError as exc:
            self._show_error(str(exc))
            return
        self._refresh()

    def action_sign_image(self) -> None:
        if not self.controller.status.is_unlocked:
            self._show_error("Vault is locked")
            return
        records = [r for r in self.controller.list_keys() if r.lifecycle_state is LifecycleState.ACTIVE]
        if not records:
            self._show_error("No active keys available to sign with")
            return
        dialog = SignImageDialog(records, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        values = dialog.values()
        try:
            result = self.controller.sign_image(**values)
        except ControllerError as exc:
            self._show_error(str(exc))
            return
        except (ValueError, TypeError) as exc:
            self._show_error(f"Invalid signing parameters: {exc}")
            return
        QMessageBox.information(
            self,
            "Signed",
            f"Signed successfully.\nInput SHA-256: {result.input_sha256}\n"
            f"Signed SHA-256: {result.signed_sha256}",
        )

    def action_start_api(self) -> None:
        if self.api_server is not None and self.api_server.is_running:
            self._show_error("API is already running")
            return
        port, ok = QInputDialog.getInt(self, "Start API", "Port:", DEFAULT_API_PORT, 1, 65535)
        if not ok:
            return
        app = create_app(
            self.controller,
            self.token_store,
            self.key_lock_registry,
            approval_handler=self.approval_bridge.make_handler(),
        )
        self.api_server = ApiServer(app, port=port)
        try:
            self.api_server.start()
        except Exception as exc:
            self._show_error(f"Failed to start API: {exc}")
            self.api_server = None
            return
        self._refresh()

    def action_stop_api(self) -> None:
        if self.api_server is not None:
            self.api_server.stop()
        self._refresh()

    def action_issue_signing_token(self) -> None:
        project, ok = QInputDialog.getText(self, "Issue Signing Token", "Project:")
        if not ok or not project.strip():
            return
        token = self.token_store.issue("sign", project=project)
        self._audit_token_issue(token, "sign", project)
        self._show_token_once(token, f"sign-scoped, project={project}")

    def action_issue_admin_token(self) -> None:
        token = self.token_store.issue("admin")
        self._audit_token_issue(token, "admin", None)
        self._show_token_once(token, "admin-scoped")

    def _audit_token_issue(self, token: str, scope: str, project: str | None) -> None:
        if self.controller.audit_log is None:
            return
        token_id = token.split(".", 1)[0]
        self.controller.audit_log.append(
            "token.issue", "gui-operator", {"token_id": token_id, "scope": scope},
            project=project,
        )

    def _show_token_once(self, token: str, description: str) -> None:
        QApplication.clipboard().setText(token)
        QMessageBox.information(
            self,
            "Token Issued",
            f"{description} token (copied to clipboard, shown once):\n\n{token}",
        )

    def action_manage_tokens(self) -> None:
        dialog = ManageTokensDialog(self.token_store, self.controller.audit_log, self)
        dialog.exec()
        self._refresh()

    def action_view_audit_log(self) -> None:
        if self.controller.audit_log is None:
            self._show_error("No vault has been created/opened yet -- nothing to audit")
            return
        dialog = AuditLogDialog(self.controller.audit_log, self)
        dialog.exec()

    def _on_approval_requested(self, request, future) -> None:
        if future.done():
            return
        dialog = ApprovalRequestDialog(request, self)
        approved = dialog.exec() == QDialog.DialogCode.Accepted
        if not future.done():
            future.set_result(approved)

    def closeEvent(self, event) -> None:
        if self.api_server is not None:
            self.api_server.stop()
        super().closeEvent(event)

    def action_diagnostics(self) -> None:
        import platform
        import PySide6

        imgtool_path = self.controller.imgtool_executable
        lines = [
            f"imgtool: {imgtool_path or '(not found on PATH; set ZSIGN_IMGTOOL)'}",
            f"Python: {platform.python_version()}",
            f"PySide6: {PySide6.__version__}",
            f"Qt platform: {QApplication.platformName()}",
        ]
        status = self.controller.status
        if status.path is not None:
            state = "unlocked" if status.is_unlocked else "locked"
            lines.append(f"Vault: {status.path} [{status.mode or 'unknown'}, {state}]")
        else:
            lines.append("Vault: none open")
        QMessageBox.information(self, "Diagnostics", "\n".join(lines))

    def _open_buy_me_a_coffee(self) -> None:
        QDesktopServices.openUrl(QUrl(BUY_ME_A_COFFEE_URL))

    def action_about(self) -> None:
        try:
            version = importlib.metadata.version("zsign")
        except importlib.metadata.PackageNotFoundError:
            version = "unknown"
        dialog = AboutDialog(version, self)
        dialog.exec()

    # -- helpers ----------------------------------------------------------

    def _selected_key_id(self) -> str | None:
        row = self.table.currentRow()
        if row < 0:
            self._show_error("Select a key first")
            return None
        item = self.table.item(row, 0)
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def _show_error(self, message: str) -> None:
        QMessageBox.critical(self, "Error", message)

    def _refresh(self) -> None:
        status = self.controller.status
        if status.path is None:
            self.lock_icon_label.setText("")
            self.status_label.setText("No vault open.")
        else:
            state = "unlocked" if status.is_unlocked else "locked"
            self.lock_icon_label.setText("🔓" if status.is_unlocked else "🔒")
            self.status_label.setText(f"Vault: {status.path}  [{state}]")

        self.table.setRowCount(0)
        if status.is_unlocked:
            for record in self.controller.list_keys():
                row = self.table.rowCount()
                self.table.insertRow(row)
                label_item = QTableWidgetItem(record.label)
                label_item.setData(Qt.ItemDataRole.UserRole, record.key_id)
                self.table.setItem(row, 0, label_item)
                self.table.setItem(row, 1, QTableWidgetItem(record.project))
                self.table.setItem(row, 2, QTableWidgetItem(record.algorithm.value))
                self.table.setItem(row, 3, QTableWidgetItem(record.public_fingerprint[:16] + "…"))
                self.table.setItem(row, 4, QTableWidgetItem(record.lifecycle_state.value))
                self.table.setItem(row, 5, QTableWidgetItem(record.created_at))

        if self.api_server is not None and self.api_server.is_running:
            self.api_status_label.setText(
                f"API: running at {self.api_server.base_url}  "
                f"[{self.token_store.active_count()} active token(s)]"
            )
        else:
            self.api_status_label.setText("API: stopped")

        self._update_group_enabled_states(status)
        self._reset_idle_timer()

    def _update_group_enabled_states(self, status) -> None:
        # Keys & signing only ever make sense against an unlocked vault.
        self.keys_group.setEnabled(status.is_unlocked)

        vault_buttons = {
            button.text(): button
            for button in self.vault_group.findChildren(QPushButton)
        }
        vault_buttons["Lock"].setEnabled(status.is_unlocked)
        vault_buttons["Unlock…"].setEnabled(status.path is not None and not status.is_unlocked)
        vault_buttons["Close Vault"].setEnabled(status.path is not None)

        self.audit_group.setEnabled(self.controller.audit_log is not None)
