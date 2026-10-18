# Zsign

Zsign is a desktop signing agent for Zephyr/MCUboot firmware. It keeps
your private signing keys in an encrypted vault, lets you sign
firmware images by hand through a GUI or automatically over a local
HTTP API with operator approval, and keeps a tamper-evident audit log
of everything it does.

## Requirements

- Python 3.10 or later
- A pinned `imgtool` matching your target MCUboot tree (see
  [Configuration](#configuration)) if you're signing for production;
  for trying things out, the bundled `imgtool` package is enough

## Install

From the repository root, no existing virtual environment required:

```sh
# Linux / macOS
sh scripts/setup.sh
```

```powershell
# Windows
powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
```

Either script creates `.venv` (or reuses it if one already exists)
and installs Zsign into it. Afterward:

```sh
source .venv/bin/activate   # .venv\Scripts\Activate.ps1 on Windows
zsign-gui
```

## Quick start

1. Launch `zsign-gui`.
2. **Vault → New Vault…** Choose a file location and a protection
   mode:
   - **keyring** stores the vault's encryption key in your OS's native
     credential store (macOS Keychain, Windows Credential Locker,
     Linux Secret Service). Nothing to remember, but it only works on
     the machine (and user account) that created it.
   - **passphrase** derives the encryption key from a passphrase you
     choose. Works anywhere, but losing the passphrase means losing
     the vault.
3. **Keys & Signing → Generate Key…** Pick a label, a project name
   (your own grouping -- e.g. a product line), and an algorithm:
   ECDSA P-256, Ed25519, RSA-2048, or RSA-3072. All four are
   supported and verifiable by MCUboot on Zephyr.
4. **Keys & Signing → Sign Image…** Pick the key, point it at an
   unsigned firmware image, fill in the signing parameters for your
   target device (see [Signing parameters](#signing-parameters)
   below), and confirm. The signed image is written to the output
   path you chose.

That's the whole golden path for signing something by hand. Everything
below covers the pieces that matter once you're doing this for real:
locking the vault back up, running the API, and the full Zephyr/MCUboot
example.

## The GUI, section by section

The main window groups its actions into five sections:

- **Vault** -- create, open, unlock, lock, and close vaults.
  **Lock** clears the vault's keys from memory but keeps the same file
  open, so **Unlock…** works again without re-browsing to it.
  **Close Vault** fully detaches -- the next action must be
  **New Vault…** or **Open Vault…**. A 🔒/🔓 indicator next to the
  vault's status line always shows the current lock state.
- **Keys & Signing** -- generate, import, export, revoke keys, and
  sign images. Disabled as a whole whenever the vault isn't unlocked.
- **Tokens** -- issue and revoke bearer tokens for the local API (see
  [Running the API](#running-the-local-api)).
- **API & Diagnostics** -- start/stop the local API, and a
  Diagnostics dialog showing which `imgtool` is active, Python/Qt
  versions, and the current vault's state.
- **Audit Log** -- a read-only viewer over the vault's tamper-evident
  audit trail, with a button that actually re-verifies the hash chain
  rather than just displaying it.

### Keys

- **Generate Key…** creates a new private key inside the vault. The
  private key material never leaves the vault in plaintext.
- **Import Key…** brings in an existing private key (PEM). Only
  algorithms MCUboot on Zephyr can actually verify are accepted:
  ECDSA P-256, Ed25519, RSA-2048, RSA-3072.
- **Export Public Key…** writes the key's public PEM to a file --
  this is the file your bootloader build embeds as its trust anchor.
- **Revoke Key** marks a key as revoked. Revocation only stops the
  agent from signing *new* images with that key -- it doesn't
  retroactively un-trust images you've already shipped. That's a
  separate, device-side concern (rollback/security-counter policy on
  the bootloader itself).

### Signing parameters

When you sign an image, you'll be asked for the project, version, and
these device-specific values. **Don't assume these match some
default** -- they depend on your exact board, partition layout, and
Zephyr/MCUboot configuration:

- **Header size** -- how much space Zephyr already reserved for the
  MCUboot header at the start of the image (from your build's
  `CONFIG_ROM_START_OFFSET`).
- **Slot size** -- the capacity of the flash partition the signed
  image will live in (from your partition layout / devicetree).
- **Align** -- flash write alignment, used by swap-based update
  modes.
- **Security counter** (optional) -- a monotonically increasing value
  used for rollback protection. If you set one, Zsign refuses to sign
  a *lower* counter than it has previously signed for that key -- a
  safety net against accidentally shipping an old, rolled-back image.
- **Pad header** -- only turn this on if your build did *not* already
  reserve header space (most Zephyr builds do, by default).

The bundled [example](#the-example-a-zephyr-firmware-you-can-sign-and-flash)
shows exactly how to derive these for one real board.

## Running the local API

Starting the API from **API & Diagnostics → Start API** lets other
programs (build pipelines, CI, your own scripts) request signatures
without touching the GUI directly -- but every signing request still
needs a human to click Approve.

Issue a token first (**Tokens → Issue Signing Token…** or
**Issue Admin Token…**). Tokens are shown once, copied to your
clipboard, and never stored in plaintext. They expire after an hour by
default and can be revoked early from **Tokens → Manage Tokens…**.

- **Sign-scoped tokens** are tied to one project. They can call
  `/v1/sign` and `/v1/verify` for keys in that project, and list that
  project's keys -- nothing else.
- **Admin-scoped tokens** can lock/unlock the vault and read the audit
  log (`/v1/lock`, `/v1/unlock`, `/v1/audit`), and see every project's
  keys -- but can never sign anything, even for their own purposes.

Example: signing an image over HTTP (note the request blocks until an
operator approves or denies it in the GUI):

```sh
curl -X POST http://127.0.0.1:8765/v1/sign \
  -H "Authorization: Bearer <your-sign-token>" \
  -F 'metadata={"project":"my-product","key_id":"<key-id>","version":"1.0.0+0","header_size":512,"slot_size":131072,"align":4}' \
  -F 'image=@unsigned.bin' \
  -o signed.bin -D -
```

A successful response is the signed binary, with `X-Input-SHA256`,
`X-Signed-SHA256`, and `X-Audit-ID` headers. A rejection (bad
parameters, inactive key, denied approval, etc.) comes back as JSON
with an `error`/`message` pair instead.

## Configuration

Environment variables Zsign reads:

- **`ZSIGN_IMGTOOL`** -- path to the pinned `imgtool` matching your
  target MCUboot tree (e.g.
  `.../your-project/bootloader/mcuboot/scripts/imgtool.py`). If unset,
  Zsign falls back to whatever `imgtool` resolves to on your `PATH`
  (the bundled package), which may not match your actual bootloader
  build. **Diagnostics** in the GUI shows which one is active.
- **`ZSIGN_API_TOKEN`** -- used by the example scripts
  (`zephyr_test_app/scripts/`) to authenticate against the local API.
  Not read by the GUI itself.
- **`ZSIGN_ZEPHYR_WORKSPACE`** -- used by the example scripts to find
  your Zephyr `west` workspace.

## Verifying your install

```sh
pytest -q
```

This runs the full test suite, including real round trips through a
pinned `imgtool` subprocess (sign, verify, tamper detection). A clean
pass is a reasonable sanity check that your Python environment and
`imgtool` are both working correctly.

## The example: a Zephyr firmware you can sign and flash

`zephyr_test_app/` is a complete, minimal MCUboot-bootable blinky app
plus the scripts to provision a bootloader, build it, sign it with
Zsign, and flash it to a real board (an NXP FRDM-MCXA156 out of the
box, adaptable to others). It's a full walkthrough of the "sign
firmware for a real device" path end to end -- see
[`zephyr_test_app/README.md`](zephyr_test_app/README.md) for the
complete instructions.

## License and support

Zsign is free for non-commercial use. Contact the author
(martinb1974@gmail.com) for commercial licensing.

If Zsign is useful to you, consider
[buying the author a coffee](https://buymeacoffee.com/redwolfelectronics)
-- there's also a link in the GUI itself, under **Help → About Zsign**.
