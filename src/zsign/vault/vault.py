from __future__ import annotations

import datetime
import json
import uuid
from pathlib import Path

from cryptography.exceptions import InvalidTag

from zsign.vault import crypto, keyring_backend, storage
from zsign.vault.crypto import Argon2idParams
from zsign.vault.errors import VaultError, VaultIntegrityError, VaultLockedError
from zsign.vault.models import Algorithm, KeyRecord, LifecycleState

FORMAT_VERSION = 1


def _utcnow_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Vault:
    """Encrypted key vault: headless, no GUI/API dependency.

    One vault file on disk, AES-256-GCM over a JSON payload of key
    records and private-key PEMs. The key-encryption key (KEK) is either
    stored in a validated native OS keyring, or derived from a master
    passphrase via Argon2id -- never both, never a silent downgrade
    between the two (see keyring_backend.ensure_backend_available).
    """

    def __init__(self, path: Path):
        self.path = Path(path)
        self._unlocked = False
        self._kek: bytes | None = None
        self._mode_fields: dict | None = None
        self._records: dict[str, KeyRecord] = {}
        self._private_keys: dict[str, bytes] = {}

    @property
    def is_unlocked(self) -> bool:
        return self._unlocked

    # -- lifecycle ---------------------------------------------------

    @classmethod
    def create(cls, path: Path, protection: str, passphrase: str | None = None) -> "Vault":
        path = Path(path)
        if path.exists():
            raise VaultError(f"vault already exists at {path}")
        if protection == "keyring":
            kek = crypto.generate_kek()
            account = str(uuid.uuid4())
            keyring_backend.store_kek(account, kek)
            mode_fields = {"mode": "keyring", "kek_account": account}
        elif protection == "passphrase":
            if not passphrase:
                raise ValueError("passphrase protection requires a non-empty passphrase")
            params = Argon2idParams.generate()
            kek = crypto.derive_kek_from_passphrase(passphrase, params)
            mode_fields = {"mode": "passphrase", "kdf": params.to_dict()}
        else:
            raise ValueError(f"unknown protection mode {protection!r}")

        vault = cls(path)
        vault._kek = kek
        vault._mode_fields = mode_fields
        vault._records = {}
        vault._private_keys = {}
        vault._unlocked = True
        vault._persist()
        return vault

    @staticmethod
    def peek_mode(path: Path) -> str:
        """Read a vault's protection mode ("keyring" or "passphrase")
        without decrypting it. The mode is not secret; GUIs use this to
        decide whether to prompt for a passphrase before unlocking."""
        envelope = json.loads(storage.read_file(Path(path)))
        return envelope["mode"]

    def unlock(self, passphrase: str | None = None) -> None:
        raw = storage.read_file(self.path)
        envelope = json.loads(raw)
        if envelope.get("format_version") != FORMAT_VERSION:
            raise VaultIntegrityError(
                f"unsupported vault format version {envelope.get('format_version')!r}"
            )
        mode = envelope.get("mode")
        if mode == "keyring":
            if passphrase is not None:
                raise ValueError("vault uses keyring protection; do not pass a passphrase")
            kek = keyring_backend.load_kek(envelope["kek_account"])
            mode_fields = {"mode": "keyring", "kek_account": envelope["kek_account"]}
        elif mode == "passphrase":
            if not passphrase:
                raise ValueError("vault requires a passphrase to unlock")
            params = Argon2idParams.from_dict(envelope["kdf"])
            kek = crypto.derive_kek_from_passphrase(passphrase, params)
            mode_fields = {"mode": "passphrase", "kdf": envelope["kdf"]}
        else:
            raise VaultIntegrityError(f"unknown vault protection mode {mode!r}")

        nonce = bytes.fromhex(envelope["nonce"])
        ciphertext = bytes.fromhex(envelope["ciphertext"])
        aad = self._associated_data(mode_fields)
        try:
            plaintext = crypto.aead_decrypt(kek, nonce, ciphertext, aad)
        except InvalidTag as exc:
            raise VaultIntegrityError(
                "vault authentication failed: wrong passphrase/KEK, or the "
                "file is corrupted or tampered"
            ) from exc

        payload = json.loads(plaintext)
        self._records = {
            key_id: KeyRecord.from_dict(data) for key_id, data in payload["records"].items()
        }
        self._private_keys = {
            key_id: bytes.fromhex(hex_pem) for key_id, hex_pem in payload["private_keys"].items()
        }
        self._kek = kek
        self._mode_fields = mode_fields
        self._unlocked = True

    def lock(self) -> None:
        """Best-effort clear of in-memory secrets.

        Python cannot guarantee zeroization of immutable bytes/str objects
        or copies held inside libraries (see spec.md's security
        qualifications); this drops our references so they become
        eligible for garbage collection, nothing stronger.
        """
        self._records = {}
        self._private_keys = {}
        self._kek = None
        self._mode_fields = None
        self._unlocked = False

    # -- key management ------------------------------------------------

    def generate_key(
        self,
        label: str,
        project: str,
        allowed_uses: tuple[str, ...] = ("sign",),
        algorithm: Algorithm = Algorithm.ECDSA_P256,
    ) -> KeyRecord:
        self._require_unlocked()
        private_pem, public_pem = crypto.generate_keypair(algorithm)
        key_id = str(uuid.uuid4())
        record = KeyRecord(
            key_id=key_id,
            label=label,
            algorithm=algorithm,
            project=project,
            public_fingerprint=crypto.public_fingerprint(public_pem),
            created_at=_utcnow_iso(),
            lifecycle_state=LifecycleState.ACTIVE,
            allowed_uses=tuple(allowed_uses),
            public_key_pem=public_pem,
        )
        self._records[key_id] = record
        self._private_keys[key_id] = private_pem
        self._persist()
        return record

    def import_key(
        self,
        private_pem: bytes,
        label: str,
        project: str,
        allowed_uses: tuple[str, ...] = ("sign",),
    ) -> KeyRecord:
        """Import an existing P-256 private key. Caller is responsible for
        decrypting any source-side encryption before calling this -- the
        vault never retains the plaintext source file itself, only the
        PEM bytes passed here, which it immediately re-encrypts under its
        own AEAD on persist. Per spec.md: existing unencrypted source
        files are outside the vault's protection and need a separate
        migration/deletion policy the GUI must enforce, not this method.
        """
        self._require_unlocked()
        private_key = crypto.load_private_key(private_pem)
        algorithm = crypto.detect_algorithm(private_key)
        public_pem = crypto.public_pem_from_private(private_key)
        key_id = str(uuid.uuid4())
        record = KeyRecord(
            key_id=key_id,
            label=label,
            algorithm=algorithm,
            project=project,
            public_fingerprint=crypto.public_fingerprint(public_pem),
            created_at=_utcnow_iso(),
            lifecycle_state=LifecycleState.ACTIVE,
            allowed_uses=tuple(allowed_uses),
            public_key_pem=public_pem,
        )
        self._records[key_id] = record
        self._private_keys[key_id] = private_pem
        self._persist()
        return record

    def list_keys(self) -> list[KeyRecord]:
        self._require_unlocked()
        return list(self._records.values())

    def get_key(self, key_id: str) -> KeyRecord:
        self._require_unlocked()
        return self._records[key_id]

    def get_public_pem(self, key_id: str) -> bytes:
        self._require_unlocked()
        return self._records[key_id].public_key_pem

    def revoke_key(self, key_id: str) -> KeyRecord:
        return self._set_lifecycle(key_id, LifecycleState.REVOKED)

    def _set_lifecycle(self, key_id: str, state: LifecycleState) -> KeyRecord:
        self._require_unlocked()
        record = self._records[key_id].with_lifecycle_state(state)
        self._records[key_id] = record
        self._persist()
        return record

    def vector_kind(self, key_id: str) -> str:
        """Which imgtool --vector-to-sign mode this key's algorithm
        needs: "digest" for Ed25519 (signs the bare digest directly),
        "payload" for everything else (signs the full payload, hashing
        internally -- matching imgtool's own per-algorithm key.sign())."""
        self._require_unlocked()
        algorithm = self._records[key_id].algorithm
        return "digest" if crypto.uses_digest_vector(algorithm) else "payload"

    def sign(self, key_id: str, data: bytes) -> bytes:
        """Signature over `data`, whose meaning depends on the key's
        algorithm (see vector_kind): the full imgtool payload for
        ECDSA/RSA, or the bare digest for Ed25519. Not an MCUboot image
        signature by itself -- imgtool-format signing is in
        zsign.signing, built on top of this primitive."""
        self._require_unlocked()
        record = self._records[key_id]
        if record.lifecycle_state != LifecycleState.ACTIVE:
            raise VaultError(f"key {key_id} is not active ({record.lifecycle_state.value})")
        if "sign" not in record.allowed_uses:
            raise VaultError(f"key {key_id} is not permitted for signing")
        private_key = crypto.load_private_key(self._private_keys[key_id])
        if crypto.uses_digest_vector(record.algorithm):
            return crypto.sign_digest(record.algorithm, private_key, data)
        return crypto.sign_payload(record.algorithm, private_key, data)

    # -- internals ------------------------------------------------------

    def _require_unlocked(self) -> None:
        if not self._unlocked:
            raise VaultLockedError(f"vault {self.path} is locked")

    @staticmethod
    def _associated_data(mode_fields: dict) -> bytes:
        canonical = {"format_version": FORMAT_VERSION, **mode_fields}
        return json.dumps(canonical, sort_keys=True).encode("utf-8")

    def _persist(self) -> None:
        self._require_unlocked()
        payload = {
            "records": {key_id: record.to_dict() for key_id, record in self._records.items()},
            "private_keys": {
                key_id: pem.hex() for key_id, pem in self._private_keys.items()
            },
        }
        plaintext = json.dumps(payload).encode("utf-8")
        aad = self._associated_data(self._mode_fields)
        nonce, ciphertext = crypto.aead_encrypt(self._kek, plaintext, aad)
        envelope = {
            "format_version": FORMAT_VERSION,
            **self._mode_fields,
            "nonce": nonce.hex(),
            "ciphertext": ciphertext.hex(),
        }
        storage.atomic_write(self.path, json.dumps(envelope, indent=2).encode("utf-8"))
