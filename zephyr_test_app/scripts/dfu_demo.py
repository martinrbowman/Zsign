#!/usr/bin/env python3
"""Zsign DFU demo: build the blinky app with a given blink rate, sign
it through the running Zsign API (real GUI approval required), and
optionally flash the signed image straight into MCUboot's primary
slot.

Run under Zsign's own venv (needs httpx -- `pip install -e ".[dev]"`),
not the Zephyr workspace's venv: this script shells out to that
workspace's `west` by absolute path, so it doesn't need to run under
it. It never touches the private key -- that stays in the vault,
signing happens over the API with a real operator approval, same as
any other client.

Token comes from ZSIGN_API_TOKEN (not a CLI flag) -- don't put secrets
on the command line or in shell history.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import httpx

APP_DIR = Path(__file__).resolve().parent.parent
# No hardcoded default: every machine's Zephyr workspace lives
# somewhere different. Set ZSIGN_ZEPHYR_WORKSPACE once, or pass
# --zephyr-workspace every time.
DEFAULT_ZEPHYR_WORKSPACE = os.environ.get("ZSIGN_ZEPHYR_WORKSPACE")
DEFAULT_BOARD = "frdm_mcxa156"
DEFAULT_API_URL = "http://127.0.0.1:8765"
SIGN_TIMEOUT_SECONDS = 130  # a bit above the server's own approval timeout


def fail(message: str) -> None:
    print(f"error: {message}", file=sys.stderr)
    sys.exit(1)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def build(zephyr_workspace: Path, board: str, blink_ms: int, build_dir: Path) -> None:
    west = zephyr_workspace / ".venv" / "bin" / "west"
    if not west.exists():
        fail(f"west not found at {west} -- wrong --zephyr-workspace?")
    cmd = [
        str(west), "build", "-b", board, "-d", str(build_dir), str(APP_DIR), "--pristine",
        "--", f"-DCONFIG_APP_BLINK_PERIOD_MS={blink_ms}",
    ]
    print(f"+ {' '.join(cmd)}")
    subprocess.run(cmd, cwd=zephyr_workspace, check=True)


def derive_signing_params(build_dir: Path) -> dict:
    """Pull header_size/slot_size/align from this build's own generated
    output rather than hardcoding them, per spec.md's repeated warning
    against assuming board defaults."""
    config_text = (build_dir / "zephyr" / ".config").read_text()
    header_match = re.search(r"^CONFIG_ROM_START_OFFSET=(\S+)", config_text, re.MULTILINE)
    if not header_match:
        fail("CONFIG_ROM_START_OFFSET not found in build/zephyr/.config")
    header_size = int(header_match.group(1), 0)

    dts_text = (build_dir / "zephyr" / "zephyr.dts").read_text()
    slot_match = re.search(
        r"slot0_partition:\s*partition@\w+\s*\{.*?reg\s*=\s*<\s*(\S+)\s+(\S+)\s*>",
        dts_text, re.DOTALL,
    )
    if not slot_match:
        fail("slot0_partition not found in generated zephyr.dts")
    slot_size = int(slot_match.group(2), 0)

    align_match = re.search(r"write-block-size\s*=\s*<\s*(\S+)\s*>", dts_text)
    align = int(align_match.group(1), 0) if align_match else 1
    if align not in (1, 2, 4, 8, 16, 32):
        align = 1

    return {"header_size": header_size, "slot_size": slot_size, "align": align}


def derive_version(build_dir: Path) -> str:
    app_version_path = (
        build_dir / "zephyr" / "include" / "generated" / "zephyr" / "app_version.h"
    )
    text = app_version_path.read_text()
    match = re.search(r'#define APP_VERSION_TWEAK_STRING\s+"([^"]+)"', text)
    if not match:
        fail(f"APP_VERSION_TWEAK_STRING not found in {app_version_path}")
    return match.group(1)


def discover_key(api_url: str, token: str, explicit_key_id: str | None) -> dict:
    response = httpx.get(f"{api_url}/v1/keys", headers={"Authorization": f"Bearer {token}"})
    if response.status_code != 200:
        fail(f"GET /v1/keys failed: HTTP {response.status_code}: {response.text}")
    keys = response.json()
    if explicit_key_id:
        for key in keys:
            if key["key_id"] == explicit_key_id:
                return key
        fail(f"key_id {explicit_key_id} not visible to this token")
    active = [k for k in keys if k["lifecycle_state"] == "active"]
    if not active:
        fail("no active keys visible to this token's project")
    if len(active) > 1:
        options = ", ".join(f"{k['label']} ({k['key_id']})" for k in active)
        fail(f"multiple active keys available, pass --key-id explicitly: {options}")
    return active[0]


def sign(api_url: str, token: str, profile: dict, image_bytes: bytes) -> tuple[bytes, dict]:
    print("Submitting /v1/sign -- go check the Zsign GUI, an approval dialog should appear.")
    print("Click Approve within 2 minutes or the request will be denied.")
    response = httpx.post(
        f"{api_url}/v1/sign",
        data={"metadata": json.dumps(profile)},
        files={"image": ("zephyr.bin", image_bytes)},
        headers={"Authorization": f"Bearer {token}"},
        timeout=SIGN_TIMEOUT_SECONDS,
    )
    if response.status_code != 200:
        try:
            detail = response.json()
        except ValueError:
            detail = response.text
        fail(f"/v1/sign rejected: HTTP {response.status_code}: {detail}")
    return response.content, dict(response.headers)


def flash(zephyr_workspace: Path, build_dir: Path, signed_path: Path, runner: str | None) -> None:
    west = zephyr_workspace / ".venv" / "bin" / "west"
    # --bin-file, not -f/--file: -f silently fails to override the
    # flashed file with this west/linkserver combo (flashes the
    # unsigned build artifact instead, no error) -- confirmed by
    # tracing west's own constructed LinkServer command with -v.
    cmd = [str(west), "flash", "-d", str(build_dir), "--bin-file", str(signed_path), "--dt-flash=y"]
    if runner:
        cmd += ["-r", runner]
    print(f"+ {' '.join(cmd)}")
    subprocess.run(cmd, cwd=zephyr_workspace, check=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--blink-ms", type=int, required=True,
                         help="LED half-period in ms -- the observable 'new version' signal")
    parser.add_argument("--version", default=None,
                         help="Override the image version (default: derived from VERSION file)")
    parser.add_argument("--key-id", default=None,
                         help="Explicit vault key id (default: auto-discover the one active key)")
    parser.add_argument("--board", default=DEFAULT_BOARD)
    parser.add_argument(
        "--zephyr-workspace", type=Path,
        default=Path(DEFAULT_ZEPHYR_WORKSPACE) if DEFAULT_ZEPHYR_WORKSPACE else None,
        required=DEFAULT_ZEPHYR_WORKSPACE is None,
        help="Path to your west workspace (or set ZSIGN_ZEPHYR_WORKSPACE)",
    )
    parser.add_argument("--api-url", default=DEFAULT_API_URL)
    parser.add_argument("--build-dir", type=Path, default=APP_DIR / "build")
    parser.add_argument("--output", type=Path, default=None,
                         help="Where to write the signed image (default: <build-dir>/zsign.signed.bin)")
    parser.add_argument("--flash", action="store_true", help="Flash the signed image after signing")
    parser.add_argument("--runner", default=None, help="west flash runner override (e.g. jlink)")
    args = parser.parse_args()

    token = os.environ.get("ZSIGN_API_TOKEN")
    if not token:
        fail("ZSIGN_API_TOKEN is not set (issue a sign-scoped token from the GUI)")

    if not (args.zephyr_workspace / ".west").exists():
        fail(f"{args.zephyr_workspace} does not look like a west workspace")

    build(args.zephyr_workspace, args.board, args.blink_ms, args.build_dir)

    unsigned_path = args.build_dir / "zephyr" / "zephyr.bin"
    unsigned_bytes = unsigned_path.read_bytes()
    params = derive_signing_params(args.build_dir)
    version = args.version or derive_version(args.build_dir)
    print(f"Derived signing params: {params}, version={version}")

    key = discover_key(args.api_url, token, args.key_id)
    print(f"Signing with key {key['label']} ({key['key_id']}), project={key['project']}")

    profile = {
        "project": key["project"],
        "key_id": key["key_id"],
        "version": version,
        "header_size": params["header_size"],
        "slot_size": params["slot_size"],
        "align": params["align"],
        "pad_header": False,  # Zephyr already reserved the header (CONFIG_ROM_START_OFFSET)
        "pad": False,          # flashing straight into the primary slot, no swap trailer needed
        "confirm": False,
    }

    signed_bytes, headers = sign(args.api_url, token, profile, unsigned_bytes)
    if headers.get("x-input-sha256") != sha256_bytes(unsigned_bytes):
        fail("agent's reported input digest does not match what we sent")
    if sha256_bytes(signed_bytes) != headers.get("x-signed-sha256"):
        fail("signed response digest mismatch")

    output_path = args.output or (args.build_dir / "zsign.signed.bin")
    output_path.write_bytes(signed_bytes)
    print(f"Signed image written to {output_path}")
    print(f"  input_sha256:  {headers.get('x-input-sha256')}")
    print(f"  signed_sha256: {headers.get('x-signed-sha256')}")
    print(f"  audit_id:      {headers.get('x-audit-id')}")

    flash_cmd_hint = (
        f"{args.zephyr_workspace / '.venv/bin/west'} flash -d {args.build_dir} "
        f"--bin-file {output_path} --dt-flash=y"
    )
    if args.flash:
        flash(args.zephyr_workspace, args.build_dir, output_path, args.runner)
    else:
        print("Not flashing (pass --flash to flash now). Equivalent command:")
        print(f"  {flash_cmd_hint}")


if __name__ == "__main__":
    main()
