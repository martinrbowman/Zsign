# Zsign DFU demo app

A minimal MCUboot-bootable blinky, used to exercise the full Zsign
signing pipeline against real hardware. Not product firmware -- the
point is the sign/flash loop, not the app itself.

Board: `frdm_mcxa156` (stock, upstream Zephyr -- it already ships
MCUboot-ready flash partitions and a `led0` alias, no custom board
files needed). Built standalone (no sysbuild): MCUboot is built and
flashed once, separately; the app is rebuilt and resigned each DFU
cycle.

Set these once per shell session (adjust paths to your own setup):

```sh
export ZSIGN_ZEPHYR_WORKSPACE=/path/to/your/zephyr/west/workspace
source /path/to/zsign/.venv/bin/activate
```

## One-time: provision the key, build and flash MCUboot

MCUboot needs the vault's public key embedded as its trust anchor, and
needs a swap mode that matches how this demo actually flashes (direct
overwrite of the primary slot -- there's no secondary slot, no OTA
transport, nothing to swap).

```sh
export ZSIGN_API_TOKEN="<token from Issue Signing Token…>"
python zephyr_test_app/scripts/provision_mcuboot.py
```

This fetches the vault key's public PEM, writes an MCUboot Kconfig
fragment (`CONFIG_BOOT_SIGNATURE_TYPE_*` for whichever algorithm your
key uses, `CONFIG_BOOT_SIGNATURE_KEY_FILE`, and
`CONFIG_BOOT_UPGRADE_ONLY` -- the alternative "swap using offset" mode
expects an actual secondary-slot upgrade and won't boot a primary-only
image), then builds and flashes MCUboot with it. Pass `--key-id <id>`
if your vault has more than one active key; `--no-flash` to build
only.

Rerun whenever the signing key rotates. The generated Kconfig fragment
is rewritten from scratch each run -- if you've hand-edited it, rerun
this afterward rather than trusting a stale build.

## Per-DFU-cycle: build, sign, flash the app

1. Start the Zsign GUI and its API (`zsign-gui`, then "Start API"),
   with a vault unlocked and an active key. Issue a sign-scoped token
   for that key's project and export it:

   ```sh
   export ZSIGN_API_TOKEN="<token from Issue Signing Token…>"
   ```

2. Run the demo script:

   ```sh
   python zephyr_test_app/scripts/dfu_demo.py --blink-ms 250
   ```

   This builds the app with `CONFIG_APP_BLINK_PERIOD_MS=250`, derives
   `header_size`/`slot_size`/`align` from that build's own generated
   `.config`/`zephyr.dts` and the version from the generated
   `app_version.h` (nothing hardcoded), picks your token's one active
   key automatically, and submits the signing request -- **watch the
   GUI: an approval dialog appears and must be clicked within 2
   minutes.**

3. Add `--flash` to flash the signed image immediately. Without it,
   the script prints the exact `west flash` command to run by hand.

   Use the printed command as-is: on some `west`/LinkServer
   combinations, `-f`/`--file` silently does nothing (west flashes the
   unsigned build artifact instead, no error) while `--bin-file`
   works correctly -- the script already uses the working form.

4. To see an actual update happen, run it again with a different
   `--blink-ms` and reflash. The LED's blink rate is the only
   observable signal that the newly signed image is the one running.

## Troubleshooting a failed boot

If the serial console (115200 8N1, the board's own USB VCOM) shows
`Image in the primary slot is not valid!` / `Unable to find bootable
image`: MCUboot erases the entire primary slot on any failed
validation, immediately. A failed boot destroys the evidence -- don't
assume the flash still holds what you just wrote. Reflash the app
before diagnosing further, and if it keeps failing, suspect the
bootloader's configuration (wrong/missing key, wrong swap mode) before
the image itself.

To inspect what's really in flash without giving MCUboot a chance to
run and erase it (using NXP's LinkServer tool as an example):

```sh
LinkServer flash --probe '#1' <device> load --no-reset <file>:0x10000
LinkServer flash --probe '#1' <device> dump 0x10000 <size> /tmp/dump.bin
cmp /tmp/dump.bin <file>
```

## Scope

- Direct-flashes the primary slot every time. Does not exercise
  MCUboot's swap/revert/test-confirm machinery -- there's no secondary
  image, no OTA transport involved.
- `security_counter` is not set by default. Pass a profile with it set
  (via `--version` plus a custom signing call) if you want to test
  rollback policy.
