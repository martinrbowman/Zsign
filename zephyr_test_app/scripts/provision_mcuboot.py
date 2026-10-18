#!/usr/bin/env python3
"""One-time MCUboot provisioning for this demo: fetch the vault key's
public PEM, build MCUboot with it embedded as the trust anchor (plus
overwrite-only mode, since this demo always direct-flashes the primary
slot and never populates a secondary slot), and flash it.

Rerun whenever the signing key rotates, or after a fresh board/MCUboot
wipe. Not needed per DFU cycle -- that's dfu_demo.py.

Run under Zsign's own venv (needs httpx -- `pip install -e ".[dev]"`).
Token comes from ZSIGN_API_TOKEN, same convention as the other scripts.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent
# No hardcoded default: every machine's Zephyr workspace lives
# somewhere different. Set ZSIGN_ZEPHYR_WORKSPACE once, or pass
# --zephyr-workspace every time.
DEFAULT_ZEPHYR_WORKSPACE = os.environ.get("ZSIGN_ZEPHYR_WORKSPACE")
DEFAULT_BOARD = "frdm_mcxa156"
DEFAULT_MCUBOOT_BUILD_DIR = APP_DIR / "mcuboot_build"
DEFAULT_KEYS_DIR = APP_DIR / "keys"
MCUBOOT_APP_PATH = "external/bootloader/mcuboot/boot/zephyr"


def fail(message: str) -> None:
    print(f"error: {message}", file=sys.stderr)
    sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--board", default=DEFAULT_BOARD)
    parser.add_argument(
        "--zephyr-workspace", type=Path,
        default=Path(DEFAULT_ZEPHYR_WORKSPACE) if DEFAULT_ZEPHYR_WORKSPACE else None,
        required=DEFAULT_ZEPHYR_WORKSPACE is None,
        help="Path to your west workspace (or set ZSIGN_ZEPHYR_WORKSPACE)",
    )
    parser.add_argument("--mcuboot-build-dir", type=Path, default=DEFAULT_MCUBOOT_BUILD_DIR)
    parser.add_argument("--keys-dir", type=Path, default=DEFAULT_KEYS_DIR)
    parser.add_argument("--api-url", default="http://127.0.0.1:8765")
    parser.add_argument("--key-id", default=None,
                         help="Explicit vault key id (default: auto-discover the one active key)")
    parser.add_argument("--flash", action="store_true", default=True,
                         help="Flash MCUboot after building (default: on)")
    parser.add_argument("--no-flash", dest="flash", action="store_false",
                         help="Build only, don't flash")
    args = parser.parse_args()

    if not (args.zephyr_workspace / ".west").exists():
        fail(f"{args.zephyr_workspace} does not look like a west workspace")
    west = args.zephyr_workspace / ".venv" / "bin" / "west"
    if not west.exists():
        fail(f"west not found at {west}")

    fetch_script = Path(__file__).resolve().parent / "fetch_public_key.py"
    fetch_cmd = [
        sys.executable, str(fetch_script),
        "--api-url", args.api_url,
        "--out-dir", str(args.keys_dir),
    ]
    if args.key_id:
        fetch_cmd += ["--key-id", args.key_id]
    print(f"+ {' '.join(fetch_cmd)}")
    subprocess.run(fetch_cmd, check=True)

    conf_path = args.keys_dir / "mcuboot-public-key.conf"
    if not conf_path.exists():
        fail(f"expected {conf_path} after fetch_public_key.py, but it's missing")

    build_cmd = [
        str(west), "build", "-b", args.board, "-d", str(args.mcuboot_build_dir),
        MCUBOOT_APP_PATH, "--pristine",
        "--", f"-DEXTRA_CONF_FILE={conf_path.resolve()}",
    ]
    print(f"+ {' '.join(build_cmd)}")
    subprocess.run(build_cmd, cwd=args.zephyr_workspace, check=True)

    if args.flash:
        flash_cmd = [str(west), "flash", "-d", str(args.mcuboot_build_dir)]
        print(f"+ {' '.join(flash_cmd)}")
        subprocess.run(flash_cmd, cwd=args.zephyr_workspace, check=True)
        print("MCUboot flashed. Now run dfu_demo.py to build/sign/flash the app.")
    else:
        print(f"Built but not flashed. To flash: {west} flash -d {args.mcuboot_build_dir}")


if __name__ == "__main__":
    main()
