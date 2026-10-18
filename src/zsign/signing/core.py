from __future__ import annotations

import base64
import dataclasses
import json
import tempfile
from pathlib import Path

from zsign.hashing import sha256_file
from zsign.signing.imgtool_runner import run
from zsign.signing.profile import SigningProfile
from zsign.vault import Vault


@dataclasses.dataclass(frozen=True)
class SignResult:
    key_id: str
    input_sha256: str
    signed_sha256: str


def sign_image(
    vault: Vault,
    profile: SigningProfile,
    input_path: Path,
    output_path: Path,
    imgtool_executable: str | Path = "imgtool",
) -> SignResult:
    """Sign an unsigned MCUboot-format image against a vault key.

    Two-pass external-signing flow per spec.md section 9: imgtool builds
    the image header/protected-TLV and exports the exact bytes that would
    be signed (`--vector-to-sign payload` for ECDSA/RSA, which hash
    internally like imgtool's own key.sign(); `--vector-to-sign digest`
    for Ed25519, which signs the bare digest directly -- see
    Vault.vector_kind). The vault signs those bytes in-process without
    ever writing the private key to disk, and a second imgtool pass
    bakes the externally produced signature into the final image
    (`--fix-sig`/`--fix-sig-pubkey`). Every flag that affects the
    header/TLV bytes must be identical between the two passes.
    """
    input_path = Path(input_path)
    output_path = Path(output_path)
    if input_path.resolve() == output_path.resolve():
        raise ValueError("input_path and output_path must differ")

    input_sha256 = sha256_file(input_path)
    common_args = profile.imgtool_common_args()
    vector_kind = vault.vector_kind(profile.key_id)

    with tempfile.TemporaryDirectory(prefix="zsign-sign-") as tmp_dir:
        tmp = Path(tmp_dir)
        vector_path = tmp / "vector-to-sign.bin"
        signature_path = tmp / "signature.b64"
        public_key_path = tmp / "public.pem"

        run(
            imgtool_executable,
            ["sign", *common_args, "--vector-to-sign", vector_kind,
             str(input_path), str(vector_path)],
        )

        signature = vault.sign(profile.key_id, vector_path.read_bytes())
        signature_path.write_bytes(base64.b64encode(signature))
        public_key_path.write_bytes(vault.get_public_pem(profile.key_id))

        run(
            imgtool_executable,
            ["sign", *common_args,
             "--fix-sig", str(signature_path),
             "--fix-sig-pubkey", str(public_key_path),
             str(input_path), str(output_path)],
        )

        # Self-check: the agent's own vault key must verify the image it
        # just produced. This is not the client's independent verification
        # against a separately-trusted key (spec.md section 5) -- it's a
        # sanity check that the signing flow above actually worked.
        run(imgtool_executable, ["verify", "-k", str(public_key_path), str(output_path)])

    return SignResult(
        key_id=profile.key_id,
        input_sha256=input_sha256,
        signed_sha256=sha256_file(output_path),
    )


def verify_image(
    public_key_pem_path: str | Path,
    image_path: str | Path,
    imgtool_executable: str | Path = "imgtool",
) -> None:
    """Independent verification against a trusted public key file.

    Raises ImgtoolError if verification fails. The caller supplies the
    public key path -- per spec.md, that key must come from an
    independently trusted source (e.g. reviewed and committed in the
    product repo), not be fetched from the same agent immediately before
    verifying.
    """
    run(imgtool_executable, ["verify", "-k", str(public_key_pem_path), str(image_path)])


def dump_image_info(
    image_path: str | Path,
    imgtool_executable: str | Path = "imgtool",
) -> dict:
    """Parsed `imgtool dumpinfo` output: header fields (incl. `version`)
    and protected/unprotected TLVs. Used to report the version and
    security counter actually embedded in an image, rather than trusting
    what a client claims in a request profile."""
    result = run(imgtool_executable, ["dumpinfo", "-f", "json", str(image_path)])
    return json.loads(result.stdout)


def embedded_security_counter(dump: dict) -> int | None:
    for tlv in dump.get("tlv_area", {}).get("tlvs_prot", []):
        if tlv.get("type_name") == "SEC_CNT":
            return int.from_bytes(bytes.fromhex(tlv["data"]), byteorder="little")
    return None
