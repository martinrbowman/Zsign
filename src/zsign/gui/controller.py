from __future__ import annotations

import dataclasses
import shutil
import uuid
from pathlib import Path

from zsign.audit import AuditLog
from zsign.signing import SigningProfile, sign_image
from zsign.vault import Algorithm, KeyRecord, Vault


class ControllerError(Exception):
    """User-facing error: safe to show its message directly in a dialog."""


@dataclasses.dataclass(frozen=True)
class VaultStatus:
    path: Path | None
    exists: bool
    is_unlocked: bool
    mode: str | None


@dataclasses.dataclass(frozen=True)
class ControllerSignResult:
    key_id: str
    input_sha256: str
    signed_sha256: str
    audit_id: str


def _audit_path(vault_path: Path) -> Path:
    return Path(str(vault_path) + ".audit.jsonl")


class VaultController:
    """Plain-Python state holder wrapping a Vault for the GUI.

    No Qt dependency here on purpose: this class is the unit-testable
    core of the GUI shell. MainWindow is a thin view over it -- it calls
    these methods and turns ControllerError into a dialog, nothing more.
    """

    def __init__(self, imgtool_executable: str | None = None):
        self._vault: Vault | None = None
        self._path: Path | None = None
        self._imgtool_executable = imgtool_executable
        self.audit_log: AuditLog | None = None

    @property
    def status(self) -> VaultStatus:
        if self._path is None:
            return VaultStatus(path=None, exists=False, is_unlocked=False, mode=None)
        mode = None
        if self._path.exists():
            try:
                mode = Vault.peek_mode(self._path)
            except Exception:
                mode = None
        return VaultStatus(
            path=self._path,
            exists=self._path.exists(),
            is_unlocked=self._vault is not None and self._vault.is_unlocked,
            mode=mode,
        )

    def create_vault(self, path: Path, protection: str, passphrase: str | None = None) -> None:
        path = Path(path)
        if path.exists():
            raise ControllerError(f"A vault already exists at {path}")
        try:
            self._vault = Vault.create(path, protection=protection, passphrase=passphrase)
        except Exception as exc:
            raise ControllerError(str(exc)) from exc
        self._path = path
        self.audit_log = AuditLog(_audit_path(path))
        self.audit_log.append("vault.create", "gui-operator", {"protection": protection})

    def open_vault(self, path: Path) -> None:
        path = Path(path)
        if not path.exists():
            raise ControllerError(f"No vault file at {path}")
        self._path = path
        self._vault = Vault(path)
        self.audit_log = AuditLog(_audit_path(path))

    def unlock(self, passphrase: str | None = None, actor: str = "gui-operator") -> None:
        if self._vault is None:
            raise ControllerError("Open a vault first")
        try:
            self._vault.unlock(passphrase=passphrase)
        except Exception as exc:
            if self.audit_log:
                self.audit_log.append("vault.unlock_failed", actor, {"reason": str(exc)})
            raise ControllerError(str(exc)) from exc
        if self.audit_log:
            self.audit_log.append("vault.unlock", actor, {})

    def lock(self, actor: str = "gui-operator") -> None:
        if self._vault is not None:
            self._vault.lock()
        if self.audit_log:
            self.audit_log.append("vault.lock", actor, {})

    def close_vault(self, actor: str = "gui-operator") -> None:
        """Fully detach from the current vault (locking it first if
        needed), returning to the no-vault-open state. Unlike lock(),
        the path/audit_log are dropped too -- the next action must be
        New/Open Vault, not Unlock."""
        if self._vault is not None:
            self._vault.lock()
        if self.audit_log:
            self.audit_log.append("vault.close", actor, {})
        self._vault = None
        self._path = None
        self.audit_log = None

    def list_keys(self) -> list[KeyRecord]:
        self._require_unlocked()
        return self._vault.list_keys()

    def generate_key(
        self, label: str, project: str, algorithm: Algorithm = Algorithm.ECDSA_P256
    ) -> KeyRecord:
        self._require_unlocked()
        if not label.strip() or not project.strip():
            raise ControllerError("Label and project are required")
        try:
            record = self._vault.generate_key(label=label, project=project, algorithm=algorithm)
        except Exception as exc:
            raise ControllerError(str(exc)) from exc
        self.audit_log.append(
            "key.generate", "gui-operator",
            {"label": label, "algorithm": record.algorithm.value,
             "fingerprint": record.public_fingerprint},
            project=project, key_id=record.key_id,
        )
        return record

    def import_key(self, private_pem: bytes, label: str, project: str) -> KeyRecord:
        self._require_unlocked()
        if not label.strip() or not project.strip():
            raise ControllerError("Label and project are required")
        try:
            record = self._vault.import_key(private_pem, label=label, project=project)
        except Exception as exc:
            raise ControllerError(str(exc)) from exc
        self.audit_log.append(
            "key.import", "gui-operator",
            {"label": label, "algorithm": record.algorithm.value,
             "fingerprint": record.public_fingerprint},
            project=project, key_id=record.key_id,
        )
        return record

    def get_key(self, key_id: str) -> KeyRecord:
        self._require_unlocked()
        try:
            return self._vault.get_key(key_id)
        except KeyError as exc:
            raise ControllerError(f"unknown key {key_id}") from exc

    def export_public_pem(self, key_id: str) -> bytes:
        return self.get_key(key_id).public_key_pem

    def revoke_key(self, key_id: str) -> KeyRecord:
        self._require_unlocked()
        try:
            record = self._vault.revoke_key(key_id)
        except KeyError as exc:
            raise ControllerError(f"unknown key {key_id}") from exc
        self.audit_log.append("key.revoke", "gui-operator", {}, project=record.project, key_id=key_id)
        return record

    @property
    def imgtool_executable(self) -> str | None:
        """Resolved imgtool path: explicit pin if configured, else PATH
        lookup. May be None if neither resolves to anything."""
        return self._imgtool_executable or shutil.which("imgtool")

    def sign_image(
        self,
        key_id: str,
        project: str,
        version: str,
        header_size: int,
        slot_size: int,
        align: int,
        security_counter: int | None,
        pad_header: bool,
        pad: bool,
        confirm: bool,
        input_path: Path,
        output_path: Path,
        actor: str = "gui-operator",
        audit_id: str | None = None,
    ) -> ControllerSignResult:
        self._require_unlocked()
        audit_id = audit_id or str(uuid.uuid4())
        imgtool_executable = self.imgtool_executable
        if imgtool_executable is None:
            raise ControllerError("imgtool executable not found on PATH")

        if security_counter is not None:
            last = self.audit_log.last_security_counter(key_id)
            if last is not None and security_counter < last:
                self.audit_log.append(
                    "sign.failed", actor,
                    {"reason": "security_counter_regression",
                     "requested": security_counter, "last_signed": last},
                    project=project, key_id=key_id, record_id=audit_id,
                )
                raise ControllerError(
                    f"security_counter {security_counter} is lower than the last "
                    f"signed value {last} for this key -- refusing to sign a rollback"
                )

        profile = SigningProfile(
            project=project,
            key_id=key_id,
            version=version,
            header_size=header_size,
            slot_size=slot_size,
            align=align,
            security_counter=security_counter,
            pad_header=pad_header,
            pad=pad,
            confirm=confirm,
        )
        try:
            result = sign_image(self._vault, profile, input_path, output_path, imgtool_executable)
        except Exception as exc:
            self.audit_log.append(
                "sign.failed", actor, {"reason": str(exc), "version": version},
                project=project, key_id=key_id, record_id=audit_id,
            )
            raise ControllerError(str(exc)) from exc

        record = self.audit_log.append(
            "sign.completed", actor,
            {
                "version": version,
                "security_counter": security_counter,
                "input_sha256": result.input_sha256,
                "signed_sha256": result.signed_sha256,
            },
            project=project, key_id=key_id, record_id=audit_id,
        )
        return ControllerSignResult(
            key_id=result.key_id,
            input_sha256=result.input_sha256,
            signed_sha256=result.signed_sha256,
            audit_id=record.record_id,
        )

    def _require_unlocked(self) -> None:
        if self._vault is None or not self._vault.is_unlocked:
            raise ControllerError("Vault is locked")
