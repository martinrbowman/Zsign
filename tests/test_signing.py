import os
import shutil

import pytest

from zsign.signing import (
    ImgtoolError,
    ProfileError,
    SigningProfile,
    dump_image_info,
    embedded_security_counter,
    sign_image,
    verify_image,
)
from zsign.vault import Vault
from zsign.vault.models import Algorithm

PASSPHRASE = "correct horse battery staple"


def _vault_with_key(tmp_path, label="k", project="demo", algorithm=Algorithm.ECDSA_P256):
    vault = Vault.create(tmp_path / "vault.json", protection="passphrase", passphrase=PASSPHRASE)
    record = vault.generate_key(label=label, project=project, algorithm=algorithm)
    return vault, record


def _skip_if_no_imgtool():
    if shutil.which("imgtool") is None:
        pytest.skip("imgtool executable not on PATH")


@pytest.fixture(autouse=True)
def require_imgtool():
    _skip_if_no_imgtool()


def test_sign_and_independently_verify(tmp_path):
    vault, record = _vault_with_key(tmp_path)
    unsigned = tmp_path / "app.bin"
    unsigned.write_bytes(os.urandom(2048))
    profile = SigningProfile(
        project="demo", key_id=record.key_id, version="1.2.3+0",
        header_size=32, slot_size=131072, align=1, pad_header=True,
    )

    signed = tmp_path / "app.signed.bin"
    result = sign_image(vault, profile, unsigned, signed)
    assert result.key_id == record.key_id
    assert signed.exists()

    pubkey_path = tmp_path / "public.pem"
    pubkey_path.write_bytes(vault.get_public_pem(record.key_id))
    verify_image(pubkey_path, signed)  # no raise = valid


def test_sign_with_already_reserved_header(tmp_path):
    """Mirrors a real Zephyr build, which normally reserves the header
    itself: pad_header=False, input already starts with header_size
    zero bytes."""
    vault, record = _vault_with_key(tmp_path)
    header_size = 32
    unsigned = tmp_path / "app.bin"
    unsigned.write_bytes(b"\x00" * header_size + os.urandom(2048))
    profile = SigningProfile(
        project="demo", key_id=record.key_id, version="1.0.0+0",
        header_size=header_size, slot_size=131072, align=1, pad_header=False,
    )

    signed = tmp_path / "app.signed.bin"
    sign_image(vault, profile, unsigned, signed)

    pubkey_path = tmp_path / "public.pem"
    pubkey_path.write_bytes(vault.get_public_pem(record.key_id))
    verify_image(pubkey_path, signed)


def test_sign_with_security_counter_protected_tlv(tmp_path):
    """security_counter adds a protected TLV computed before the digest;
    confirm the two-pass flow still produces a verifiable image."""
    vault, record = _vault_with_key(tmp_path)
    unsigned = tmp_path / "app.bin"
    unsigned.write_bytes(os.urandom(1024))
    profile = SigningProfile(
        project="demo", key_id=record.key_id, version="2.0.0+1",
        header_size=32, slot_size=65536, align=4, pad_header=True,
        security_counter=7,
    )
    signed = tmp_path / "app.signed.bin"
    sign_image(vault, profile, unsigned, signed)

    pubkey_path = tmp_path / "public.pem"
    pubkey_path.write_bytes(vault.get_public_pem(record.key_id))
    verify_image(pubkey_path, signed)


def test_tampered_image_fails_verification(tmp_path):
    vault, record = _vault_with_key(tmp_path)
    unsigned = tmp_path / "app.bin"
    unsigned.write_bytes(os.urandom(2048))
    profile = SigningProfile(
        project="demo", key_id=record.key_id, version="1.0.0+0",
        header_size=32, slot_size=131072, align=1, pad_header=True,
    )
    signed = tmp_path / "app.signed.bin"
    sign_image(vault, profile, unsigned, signed)

    tampered = bytearray(signed.read_bytes())
    tampered[40] ^= 0xFF
    tampered_path = tmp_path / "app.tampered.bin"
    tampered_path.write_bytes(bytes(tampered))

    pubkey_path = tmp_path / "public.pem"
    pubkey_path.write_bytes(vault.get_public_pem(record.key_id))
    with pytest.raises(ImgtoolError):
        verify_image(pubkey_path, tampered_path)


def test_wrong_key_fails_verification(tmp_path):
    vault, record = _vault_with_key(tmp_path)
    unsigned = tmp_path / "app.bin"
    unsigned.write_bytes(os.urandom(2048))
    profile = SigningProfile(
        project="demo", key_id=record.key_id, version="1.0.0+0",
        header_size=32, slot_size=131072, align=1, pad_header=True,
    )
    signed = tmp_path / "app.signed.bin"
    sign_image(vault, profile, unsigned, signed)

    other_record = vault.generate_key(label="other", project="demo")
    other_pub_path = tmp_path / "other_public.pem"
    other_pub_path.write_bytes(vault.get_public_pem(other_record.key_id))
    with pytest.raises(ImgtoolError):
        verify_image(other_pub_path, signed)


def test_revoked_key_cannot_sign(tmp_path):
    vault, record = _vault_with_key(tmp_path)
    vault.revoke_key(record.key_id)
    unsigned = tmp_path / "app.bin"
    unsigned.write_bytes(os.urandom(2048))
    profile = SigningProfile(
        project="demo", key_id=record.key_id, version="1.0.0+0",
        header_size=32, slot_size=131072, align=1, pad_header=True,
    )
    with pytest.raises(Exception):
        sign_image(vault, profile, unsigned, tmp_path / "out.bin")


def test_input_and_output_path_must_differ(tmp_path):
    vault, record = _vault_with_key(tmp_path)
    unsigned = tmp_path / "app.bin"
    unsigned.write_bytes(os.urandom(2048))
    profile = SigningProfile(
        project="demo", key_id=record.key_id, version="1.0.0+0",
        header_size=32, slot_size=131072, align=1, pad_header=True,
    )
    with pytest.raises(ValueError):
        sign_image(vault, profile, unsigned, unsigned)


def test_profile_from_dict_requires_fields():
    with pytest.raises(ProfileError):
        SigningProfile.from_dict({"project": "demo"})


def test_profile_rejects_invalid_align():
    with pytest.raises(ProfileError):
        SigningProfile(
            project="demo", key_id="k", version="1.0.0+0",
            header_size=32, slot_size=1024, align=3,
        )


def test_profile_rejects_non_positive_slot_size():
    with pytest.raises(ProfileError):
        SigningProfile(
            project="demo", key_id="k", version="1.0.0+0",
            header_size=32, slot_size=0,
        )


def test_dump_image_info_reports_embedded_version_and_counter(tmp_path):
    vault, record = _vault_with_key(tmp_path)
    unsigned = tmp_path / "app.bin"
    unsigned.write_bytes(os.urandom(1024))
    profile = SigningProfile(
        project="demo", key_id=record.key_id, version="3.4.5+9",
        header_size=32, slot_size=65536, align=1, pad_header=True,
        security_counter=11,
    )
    signed = tmp_path / "app.signed.bin"
    sign_image(vault, profile, unsigned, signed)

    dump = dump_image_info(signed)
    assert dump["header"]["version"] == "3.4.5+9"
    assert embedded_security_counter(dump) == 11


def test_embedded_security_counter_none_when_absent(tmp_path):
    vault, record = _vault_with_key(tmp_path)
    unsigned = tmp_path / "app.bin"
    unsigned.write_bytes(os.urandom(1024))
    profile = SigningProfile(
        project="demo", key_id=record.key_id, version="1.0.0+0",
        header_size=32, slot_size=65536, align=1, pad_header=True,
    )
    signed = tmp_path / "app.signed.bin"
    sign_image(vault, profile, unsigned, signed)

    dump = dump_image_info(signed)
    assert embedded_security_counter(dump) is None


def test_profile_round_trip_from_dict():
    data = {
        "project": "product-a",
        "key_id": "abc-123",
        "version": "1.2.3+42",
        "header_size": 512,
        "slot_size": 131072,
        "align": 4,
        "security_counter": 7,
        "pad_header": False,
        "pad": False,
        "confirm": False,
    }
    profile = SigningProfile.from_dict(data)
    assert profile.security_counter == 7
    assert "-s" in profile.imgtool_common_args()


@pytest.mark.parametrize(
    "algorithm", [Algorithm.ED25519, Algorithm.RSA_2048, Algorithm.RSA_3072]
)
def test_sign_and_independently_verify_other_algorithms(tmp_path, algorithm):
    """Mirrors test_sign_and_independently_verify for every non-P-256
    algorithm the vault supports -- this is what actually proves
    vector_kind()/sign_payload()/sign_digest() produce signatures
    imgtool itself accepts, not just that our own code is internally
    consistent."""
    vault, record = _vault_with_key(tmp_path, algorithm=algorithm)
    unsigned = tmp_path / "app.bin"
    unsigned.write_bytes(os.urandom(2048))
    profile = SigningProfile(
        project="demo", key_id=record.key_id, version="1.2.3+0",
        header_size=32, slot_size=131072, align=1, pad_header=True,
    )

    signed = tmp_path / "app.signed.bin"
    result = sign_image(vault, profile, unsigned, signed)
    assert result.key_id == record.key_id

    pubkey_path = tmp_path / "public.pem"
    pubkey_path.write_bytes(vault.get_public_pem(record.key_id))
    verify_image(pubkey_path, signed)  # no raise = valid


@pytest.mark.parametrize(
    "algorithm", [Algorithm.ED25519, Algorithm.RSA_2048, Algorithm.RSA_3072]
)
def test_tampered_image_fails_verification_other_algorithms(tmp_path, algorithm):
    vault, record = _vault_with_key(tmp_path, algorithm=algorithm)
    unsigned = tmp_path / "app.bin"
    unsigned.write_bytes(os.urandom(2048))
    profile = SigningProfile(
        project="demo", key_id=record.key_id, version="1.0.0+0",
        header_size=32, slot_size=131072, align=1, pad_header=True,
    )
    signed = tmp_path / "app.signed.bin"
    sign_image(vault, profile, unsigned, signed)

    tampered = bytearray(signed.read_bytes())
    tampered[40] ^= 0xFF
    tampered_path = tmp_path / "app.tampered.bin"
    tampered_path.write_bytes(bytes(tampered))

    pubkey_path = tmp_path / "public.pem"
    pubkey_path.write_bytes(vault.get_public_pem(record.key_id))
    with pytest.raises(ImgtoolError):
        verify_image(pubkey_path, tampered_path)


@pytest.mark.parametrize(
    "algorithm", [Algorithm.ED25519, Algorithm.RSA_2048, Algorithm.RSA_3072]
)
def test_wrong_key_fails_verification_other_algorithms(tmp_path, algorithm):
    vault, record = _vault_with_key(tmp_path, algorithm=algorithm)
    unsigned = tmp_path / "app.bin"
    unsigned.write_bytes(os.urandom(2048))
    profile = SigningProfile(
        project="demo", key_id=record.key_id, version="1.0.0+0",
        header_size=32, slot_size=131072, align=1, pad_header=True,
    )
    signed = tmp_path / "app.signed.bin"
    sign_image(vault, profile, unsigned, signed)

    other_record = vault.generate_key(label="other", project="demo", algorithm=algorithm)
    other_pub_path = tmp_path / "other_public.pem"
    other_pub_path.write_bytes(vault.get_public_pem(other_record.key_id))
    with pytest.raises(ImgtoolError):
        verify_image(other_pub_path, signed)


def test_sign_with_security_counter_other_algorithm(tmp_path):
    """security_counter adds a protected TLV computed before the
    digest/payload is hashed/signed -- confirm that still works for a
    non-ECDSA algorithm too (Ed25519's digest-vector path is the most
    different from P-256's, so it's the most useful one to check here)."""
    vault, record = _vault_with_key(tmp_path, algorithm=Algorithm.ED25519)
    unsigned = tmp_path / "app.bin"
    unsigned.write_bytes(os.urandom(1024))
    profile = SigningProfile(
        project="demo", key_id=record.key_id, version="2.0.0+1",
        header_size=32, slot_size=65536, align=4, pad_header=True,
        security_counter=7,
    )
    signed = tmp_path / "app.signed.bin"
    sign_image(vault, profile, unsigned, signed)

    pubkey_path = tmp_path / "public.pem"
    pubkey_path.write_bytes(vault.get_public_pem(record.key_id))
    verify_image(pubkey_path, signed)

    dump = dump_image_info(signed)
    assert embedded_security_counter(dump) == 7
