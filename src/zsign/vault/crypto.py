from __future__ import annotations

import dataclasses
import hashlib
import os

from argon2.low_level import Type, hash_secret_raw
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519, padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from zsign.vault.models import Algorithm

RSA_KEY_SIZE_BITS = {
    Algorithm.RSA_2048: 2048,
    Algorithm.RSA_3072: 3072,
}
# MCUboot's bootloader-side signature verification (CONFIG_BOOT_SIGNATURE_TYPE_*)
# only ships P-256, RSA-2048/3072, and Ed25519 on Zephyr -- no P-384, even
# though imgtool itself can generate P-384 keys. A P-384 key here would be
# unverifiable by any Zephyr/MCUboot bootloader, so it's deliberately not
# offered.

NONCE_LEN = 12
KEK_LEN = 32

# Argon2id baseline. Reviewed starting point, not a final tuning: raise
# memory/time cost if target hardware allows, per spec.md's requirement
# for a "reviewed Argon2id configuration".
ARGON2ID_TIME_COST = 3
ARGON2ID_MEMORY_COST_KIB = 65536  # 64 MiB
ARGON2ID_PARALLELISM = 4
ARGON2ID_SALT_LEN = 16


def random_bytes(n: int) -> bytes:
    return os.urandom(n)


def generate_kek() -> bytes:
    return random_bytes(KEK_LEN)


@dataclasses.dataclass(frozen=True)
class Argon2idParams:
    salt: bytes
    time_cost: int = ARGON2ID_TIME_COST
    memory_cost_kib: int = ARGON2ID_MEMORY_COST_KIB
    parallelism: int = ARGON2ID_PARALLELISM

    def to_dict(self) -> dict:
        return {
            "salt": self.salt.hex(),
            "time_cost": self.time_cost,
            "memory_cost_kib": self.memory_cost_kib,
            "parallelism": self.parallelism,
        }

    @staticmethod
    def from_dict(data: dict) -> "Argon2idParams":
        return Argon2idParams(
            salt=bytes.fromhex(data["salt"]),
            time_cost=data["time_cost"],
            memory_cost_kib=data["memory_cost_kib"],
            parallelism=data["parallelism"],
        )

    @staticmethod
    def generate() -> "Argon2idParams":
        return Argon2idParams(salt=random_bytes(ARGON2ID_SALT_LEN))


def derive_kek_from_passphrase(passphrase: str, params: Argon2idParams) -> bytes:
    return hash_secret_raw(
        secret=passphrase.encode("utf-8"),
        salt=params.salt,
        time_cost=params.time_cost,
        memory_cost=params.memory_cost_kib,
        parallelism=params.parallelism,
        hash_len=KEK_LEN,
        type=Type.ID,
    )


def aead_encrypt(key: bytes, plaintext: bytes, associated_data: bytes) -> tuple[bytes, bytes]:
    nonce = random_bytes(NONCE_LEN)
    ciphertext = AESGCM(key).encrypt(nonce, plaintext, associated_data)
    return nonce, ciphertext


def aead_decrypt(key: bytes, nonce: bytes, ciphertext: bytes, associated_data: bytes) -> bytes:
    return AESGCM(key).decrypt(nonce, ciphertext, associated_data)


def _private_key_to_pems(private_key) -> tuple[bytes, bytes]:
    """Private PEM is unencrypted PKCS8: the outer vault AEAD already
    protects it, and a second password layer here would just be a
    second place to lose the passphrase."""
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return private_pem, public_pem


def generate_keypair(algorithm: Algorithm) -> tuple[bytes, bytes]:
    """Returns (private_pem, public_pem) for any vault-supported algorithm."""
    if algorithm == Algorithm.ECDSA_P256:
        private_key = ec.generate_private_key(ec.SECP256R1())
    elif algorithm == Algorithm.ED25519:
        private_key = ed25519.Ed25519PrivateKey.generate()
    elif algorithm in RSA_KEY_SIZE_BITS:
        private_key = rsa.generate_private_key(
            public_exponent=65537, key_size=RSA_KEY_SIZE_BITS[algorithm]
        )
    else:
        raise NotImplementedError(f"key generation not implemented for {algorithm}")
    return _private_key_to_pems(private_key)


def generate_p256_keypair() -> tuple[bytes, bytes]:
    return generate_keypair(Algorithm.ECDSA_P256)


def detect_algorithm(private_key) -> Algorithm:
    """Identify which vault-supported Algorithm a loaded private key
    object is, for import_key (which receives a PEM, not a declared
    algorithm) to validate against and record."""
    if isinstance(private_key, ec.EllipticCurvePrivateKey):
        if isinstance(private_key.curve, ec.SECP256R1):
            return Algorithm.ECDSA_P256
        raise NotImplementedError(
            f"curve {private_key.curve.name} is not verifiable by MCUboot on "
            f"Zephyr (only P-256 is); cannot import this key"
        )
    if isinstance(private_key, ed25519.Ed25519PrivateKey):
        return Algorithm.ED25519
    if isinstance(private_key, rsa.RSAPrivateKey):
        for algorithm, bits in RSA_KEY_SIZE_BITS.items():
            if private_key.key_size == bits:
                return algorithm
        raise NotImplementedError(
            f"RSA-{private_key.key_size} is not supported by MCUboot "
            f"(only RSA-2048/3072 are); cannot import this key"
        )
    raise NotImplementedError(f"unsupported key type {type(private_key).__name__}")


def uses_digest_vector(algorithm: Algorithm) -> bool:
    """Ed25519 signs over the image's SHA-256 digest directly (Ed25519 is
    not hash-then-sign; imgtool's own Ed25519 key class only implements
    sign_digest, not sign). ECDSA and RSA sign over the full payload,
    hashing internally -- matching imgtool's own key.sign(payload)."""
    return algorithm == Algorithm.ED25519


def sign_payload(algorithm: Algorithm, private_key, payload: bytes) -> bytes:
    if algorithm == Algorithm.ECDSA_P256:
        return private_key.sign(payload, ec.ECDSA(hashes.SHA256()))
    if algorithm in RSA_KEY_SIZE_BITS:
        # Matches imgtool's own RSA.sign(): PSS with a 32-byte salt is
        # the only length its verification side accepts.
        return private_key.sign(
            payload,
            padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=32),
            hashes.SHA256(),
        )
    raise ValueError(f"{algorithm} does not sign over a full payload")


def sign_digest(algorithm: Algorithm, private_key, digest: bytes) -> bytes:
    if algorithm == Algorithm.ED25519:
        return private_key.sign(digest)
    raise ValueError(f"{algorithm} does not sign over a bare digest")


def public_fingerprint(public_pem: bytes) -> str:
    """SHA-256 over the DER SubjectPublicKeyInfo, hex-encoded.

    This is a generic PEM fingerprint for inventory/display purposes only.
    Per spec.md: do not equate it with MCUboot's encoded-key hash, which
    imgtool computes over its own key encoding, not raw SPKI DER.
    """
    public_key = serialization.load_pem_public_key(public_pem)
    der = public_key.public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return hashlib.sha256(der).hexdigest()


def load_private_key(private_pem: bytes):
    return serialization.load_pem_private_key(private_pem, password=None)


def public_pem_from_private(private_key) -> bytes:
    return private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
