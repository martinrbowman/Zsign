import json

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import load_pem_public_key

from zsign.vault import Vault
from zsign.vault.errors import VaultIntegrityError, VaultLockedError
from zsign.vault.models import Algorithm, LifecycleState

PASSPHRASE = "correct horse battery staple"


def _new_vault(tmp_path, passphrase=PASSPHRASE):
    return Vault.create(tmp_path / "vault.json", protection="passphrase", passphrase=passphrase)


def test_create_and_generate_key(tmp_path):
    vault = _new_vault(tmp_path)
    record = vault.generate_key(label="product-a-release", project="product-a")
    assert record.algorithm is Algorithm.ECDSA_P256
    assert record.lifecycle_state is LifecycleState.ACTIVE
    assert record.key_id in {k.key_id for k in vault.list_keys()}
    assert vault.get_public_pem(record.key_id) == record.public_key_pem


def test_sign_verifies_against_public_key(tmp_path):
    vault = _new_vault(tmp_path)
    record = vault.generate_key(label="k", project="p")
    signature = vault.sign(record.key_id, b"image-digest-bytes")
    public_key = load_pem_public_key(record.public_key_pem)
    public_key.verify(signature, b"image-digest-bytes", ec.ECDSA(hashes.SHA256()))


def test_revoked_key_cannot_sign(tmp_path):
    vault = _new_vault(tmp_path)
    record = vault.generate_key(label="k", project="p")
    vault.revoke_key(record.key_id)
    with pytest.raises(Exception):
        vault.sign(record.key_id, b"data")


def test_locked_vault_rejects_operations(tmp_path):
    vault = _new_vault(tmp_path)
    record = vault.generate_key(label="k", project="p")
    vault.lock()
    assert not vault.is_unlocked
    with pytest.raises(VaultLockedError):
        vault.list_keys()
    with pytest.raises(VaultLockedError):
        vault.sign(record.key_id, b"data")
    with pytest.raises(VaultLockedError):
        vault.generate_key(label="k2", project="p")


def test_unlock_persists_across_reopen(tmp_path):
    path = tmp_path / "vault.json"
    vault = Vault.create(path, protection="passphrase", passphrase=PASSPHRASE)
    record = vault.generate_key(label="k", project="p")
    vault.lock()

    reopened = Vault(path)
    reopened.unlock(passphrase=PASSPHRASE)
    restored = reopened.get_key(record.key_id)
    assert restored.key_id == record.key_id
    assert restored.public_fingerprint == record.public_fingerprint
    # Private key survived the round trip too: still able to sign.
    signature = reopened.sign(record.key_id, b"data")
    load_pem_public_key(restored.public_key_pem).verify(
        signature, b"data", ec.ECDSA(hashes.SHA256())
    )


def test_wrong_passphrase_rejected(tmp_path):
    path = tmp_path / "vault.json"
    Vault.create(path, protection="passphrase", passphrase=PASSPHRASE)

    vault = Vault(path)
    with pytest.raises(VaultIntegrityError):
        vault.unlock(passphrase="wrong passphrase")
    assert not vault.is_unlocked


def test_corrupted_ciphertext_rejected(tmp_path):
    path = tmp_path / "vault.json"
    Vault.create(path, protection="passphrase", passphrase=PASSPHRASE)

    envelope = json.loads(path.read_text())
    tampered = bytearray(bytes.fromhex(envelope["ciphertext"]))
    tampered[0] ^= 0xFF
    envelope["ciphertext"] = bytes(tampered).hex()
    path.chmod(0o600)
    path.write_text(json.dumps(envelope))
    path.chmod(0o600)

    vault = Vault(path)
    with pytest.raises(VaultIntegrityError):
        vault.unlock(passphrase=PASSPHRASE)


def test_tampered_header_rejected_even_with_correct_passphrase(tmp_path):
    path = tmp_path / "vault.json"
    Vault.create(path, protection="passphrase", passphrase=PASSPHRASE)

    envelope = json.loads(path.read_text())
    # Flip a byte in the KDF salt (part of the authenticated header) without
    # touching the ciphertext: AEAD must still reject this.
    kdf = envelope["kdf"]
    salt = bytearray(bytes.fromhex(kdf["salt"]))
    salt[0] ^= 0xFF
    kdf["salt"] = bytes(salt).hex()
    path.write_text(json.dumps(envelope))
    path.chmod(0o600)

    vault = Vault(path)
    with pytest.raises(VaultIntegrityError):
        vault.unlock(passphrase=PASSPHRASE)


def test_import_key_roundtrip(tmp_path):
    from zsign.vault import crypto as crypto_module

    vault = _new_vault(tmp_path)
    private_pem, public_pem = crypto_module.generate_p256_keypair()
    record = vault.import_key(private_pem, label="imported", project="p")
    assert record.public_key_pem == public_pem
    signature = vault.sign(record.key_id, b"data")
    load_pem_public_key(public_pem).verify(signature, b"data", ec.ECDSA(hashes.SHA256()))


def test_import_key_rsa_2048_is_supported(tmp_path):
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat

    vault = _new_vault(tmp_path)
    rsa_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    rsa_pem = rsa_key.private_bytes(
        encoding=Encoding.PEM, format=PrivateFormat.PKCS8, encryption_algorithm=NoEncryption()
    )
    record = vault.import_key(rsa_pem, label="imported-rsa", project="p")
    assert record.algorithm is Algorithm.RSA_2048


def test_import_key_rejects_unsupported_rsa_size(tmp_path):
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat

    vault = _new_vault(tmp_path)
    rsa_key = rsa.generate_private_key(public_exponent=65537, key_size=4096)
    rsa_pem = rsa_key.private_bytes(
        encoding=Encoding.PEM, format=PrivateFormat.PKCS8, encryption_algorithm=NoEncryption()
    )
    with pytest.raises(NotImplementedError):
        vault.import_key(rsa_pem, label="bad", project="p")


def test_import_key_rejects_unsupported_curve(tmp_path):
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat

    vault = _new_vault(tmp_path)
    # P-384: imgtool can sign with it, but no Zephyr/MCUboot bootloader
    # can verify it, so the vault deliberately refuses to import it.
    key = ec.generate_private_key(ec.SECP384R1())
    pem = key.private_bytes(
        encoding=Encoding.PEM, format=PrivateFormat.PKCS8, encryption_algorithm=NoEncryption()
    )
    with pytest.raises(NotImplementedError):
        vault.import_key(pem, label="bad", project="p")


def test_peek_mode_does_not_require_unlock(tmp_path):
    path = tmp_path / "vault.json"
    Vault.create(path, protection="passphrase", passphrase=PASSPHRASE)
    assert Vault.peek_mode(path) == "passphrase"


def test_create_refuses_existing_path(tmp_path):
    path = tmp_path / "vault.json"
    Vault.create(path, protection="passphrase", passphrase=PASSPHRASE)
    with pytest.raises(Exception):
        Vault.create(path, protection="passphrase", passphrase=PASSPHRASE)


def test_failed_mutation_does_not_corrupt_existing_vault(tmp_path, monkeypatch):
    path = tmp_path / "vault.json"
    vault = Vault.create(path, protection="passphrase", passphrase=PASSPHRASE)
    first_record = vault.generate_key(label="k1", project="p")
    good_bytes_on_disk = path.read_bytes()

    from zsign.vault import storage as storage_module

    def boom(*args, **kwargs):
        raise OSError("simulated crash mid-persist")

    monkeypatch.setattr(storage_module, "atomic_write", boom)
    with pytest.raises(OSError):
        vault.generate_key(label="k2", project="p")

    # Vault file on disk must be exactly what it was before the failed call.
    assert path.read_bytes() == good_bytes_on_disk

    monkeypatch.undo()
    reopened = Vault(path)
    reopened.unlock(passphrase=PASSPHRASE)
    assert [k.key_id for k in reopened.list_keys()] == [first_record.key_id]
