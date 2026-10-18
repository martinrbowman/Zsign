import hashlib

import pytest
from cryptography.exceptions import InvalidSignature, InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, padding
from cryptography.hazmat.primitives.serialization import load_pem_public_key

from zsign.vault import crypto
from zsign.vault.models import Algorithm


def test_aead_roundtrip():
    key = crypto.generate_kek()
    nonce, ciphertext = crypto.aead_encrypt(key, b"secret payload", b"aad")
    assert crypto.aead_decrypt(key, nonce, ciphertext, b"aad") == b"secret payload"


def test_aead_detects_ciphertext_tamper():
    key = crypto.generate_kek()
    nonce, ciphertext = crypto.aead_encrypt(key, b"secret payload", b"aad")
    tampered = bytes([ciphertext[0] ^ 0xFF]) + ciphertext[1:]
    with pytest.raises(InvalidTag):
        crypto.aead_decrypt(key, nonce, tampered, b"aad")


def test_aead_detects_aad_tamper():
    key = crypto.generate_kek()
    nonce, ciphertext = crypto.aead_encrypt(key, b"secret payload", b"aad-one")
    with pytest.raises(InvalidTag):
        crypto.aead_decrypt(key, nonce, ciphertext, b"aad-two")


def test_aead_wrong_key_fails():
    key_a = crypto.generate_kek()
    key_b = crypto.generate_kek()
    nonce, ciphertext = crypto.aead_encrypt(key_a, b"secret payload", b"aad")
    with pytest.raises(InvalidTag):
        crypto.aead_decrypt(key_b, nonce, ciphertext, b"aad")


def test_nonces_are_unique_per_encryption():
    key = crypto.generate_kek()
    nonces = {crypto.aead_encrypt(key, b"x", b"")[0] for _ in range(50)}
    assert len(nonces) == 50


def test_argon2id_is_deterministic_for_same_salt():
    params = crypto.Argon2idParams.generate()
    kek_a = crypto.derive_kek_from_passphrase("correct horse battery staple", params)
    kek_b = crypto.derive_kek_from_passphrase("correct horse battery staple", params)
    assert kek_a == kek_b
    assert len(kek_a) == crypto.KEK_LEN


def test_argon2id_differs_for_different_passphrase():
    params = crypto.Argon2idParams.generate()
    kek_a = crypto.derive_kek_from_passphrase("passphrase one", params)
    kek_b = crypto.derive_kek_from_passphrase("passphrase two", params)
    assert kek_a != kek_b


def test_p256_keypair_roundtrip_sign_verify():
    from cryptography.hazmat.primitives.serialization import load_pem_public_key

    private_pem, public_pem = crypto.generate_p256_keypair()
    private_key = crypto.load_private_key(private_pem)
    public_key = load_pem_public_key(public_pem)
    signature = private_key.sign(b"message", ec.ECDSA(hashes.SHA256()))
    public_key.verify(signature, b"message", ec.ECDSA(hashes.SHA256()))  # no raise = valid


def test_public_fingerprint_stable_and_sensitive_to_key():
    _, public_pem_a = crypto.generate_p256_keypair()
    _, public_pem_b = crypto.generate_p256_keypair()
    assert crypto.public_fingerprint(public_pem_a) == crypto.public_fingerprint(public_pem_a)
    assert crypto.public_fingerprint(public_pem_a) != crypto.public_fingerprint(public_pem_b)
    assert len(crypto.public_fingerprint(public_pem_a)) == 64  # sha256 hex


def test_ed25519_keypair_roundtrip_sign_verify_over_digest():
    private_pem, public_pem = crypto.generate_keypair(Algorithm.ED25519)
    private_key = crypto.load_private_key(private_pem)
    public_key = load_pem_public_key(public_pem)

    digest = hashlib.sha256(b"image bytes").digest()
    signature = crypto.sign_digest(Algorithm.ED25519, private_key, digest)
    public_key.verify(signature, digest)  # no raise = valid

    with pytest.raises(InvalidSignature):
        public_key.verify(signature, hashlib.sha256(b"different bytes").digest())


@pytest.mark.parametrize("algorithm", [Algorithm.RSA_2048, Algorithm.RSA_3072])
def test_rsa_keypair_roundtrip_sign_verify_over_payload(algorithm):
    private_pem, public_pem = crypto.generate_keypair(algorithm)
    private_key = crypto.load_private_key(private_pem)
    public_key = load_pem_public_key(public_pem)

    signature = crypto.sign_payload(algorithm, private_key, b"image payload bytes")
    # Matches imgtool's own RSA verify(): PSS, 32-byte salt, SHA-256.
    public_key.verify(
        signature, b"image payload bytes",
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=32),
        hashes.SHA256(),
    )
    assert public_key.key_size == {"rsa-2048": 2048, "rsa-3072": 3072}[algorithm.value]


def test_detect_algorithm_round_trips_for_every_supported_type():
    for algorithm in (Algorithm.ECDSA_P256, Algorithm.ED25519, Algorithm.RSA_2048, Algorithm.RSA_3072):
        private_pem, _ = crypto.generate_keypair(algorithm)
        private_key = crypto.load_private_key(private_pem)
        assert crypto.detect_algorithm(private_key) == algorithm


def test_detect_algorithm_rejects_unsupported_curve():
    key = ec.generate_private_key(ec.SECP384R1())
    with pytest.raises(NotImplementedError):
        crypto.detect_algorithm(key)


def test_uses_digest_vector_only_for_ed25519():
    assert crypto.uses_digest_vector(Algorithm.ED25519) is True
    assert crypto.uses_digest_vector(Algorithm.ECDSA_P256) is False
    assert crypto.uses_digest_vector(Algorithm.RSA_2048) is False
    assert crypto.uses_digest_vector(Algorithm.RSA_3072) is False


def test_sign_payload_rejects_ed25519():
    private_pem, _ = crypto.generate_keypair(Algorithm.ED25519)
    private_key = crypto.load_private_key(private_pem)
    with pytest.raises(ValueError):
        crypto.sign_payload(Algorithm.ED25519, private_key, b"data")


def test_sign_digest_rejects_non_ed25519():
    private_pem, _ = crypto.generate_keypair(Algorithm.ECDSA_P256)
    private_key = crypto.load_private_key(private_pem)
    with pytest.raises(ValueError):
        crypto.sign_digest(Algorithm.ECDSA_P256, private_key, b"digest")
