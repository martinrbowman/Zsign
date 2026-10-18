#!/usr/bin/env sh
# Zsign setup for Linux and macOS: creates .venv (if it doesn't already
# exist) and installs Zsign into it. Safe to re-run -- it reuses an
# existing .venv and just reinstalls on top of it.
set -eu

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
VENV_DIR="$REPO_ROOT/.venv"

PYTHON="${PYTHON:-python3}"
if ! command -v "$PYTHON" >/dev/null 2>&1; then
    echo "error: $PYTHON not found. Install Python 3.10+ first." >&2
    exit 1
fi

PYTHON_OK=$("$PYTHON" -c 'import sys; print(1 if sys.version_info >= (3, 10) else 0)')
if [ "$PYTHON_OK" != "1" ]; then
    echo "error: $PYTHON is older than the required 3.10 (pyproject.toml's requires-python)." >&2
    "$PYTHON" --version >&2
    exit 1
fi

if [ ! -d "$VENV_DIR" ]; then
    echo "Creating venv at $VENV_DIR"
    "$PYTHON" -m venv "$VENV_DIR"
else
    echo "Reusing existing venv at $VENV_DIR"
fi

# shellcheck disable=SC1091
. "$VENV_DIR/bin/activate"

pip install --upgrade pip -q
pip install -e "$REPO_ROOT[dev]"

echo
echo "Setup complete."
echo "Activate in a new shell with:  source $VENV_DIR/bin/activate"
echo "Run the GUI with:              zsign-gui"
echo "Run the test suite with:       pytest -q"
echo
case "$(uname -s)" in
    Linux)
        echo "Linux note: if the GUI fails to start with a Qt platform"
        echo "plugin error, your distro likely needs libxcb-cursor0 (or"
        echo "equivalent) installed via your system package manager --"
        echo "this setup script does not install system packages."
        ;;
esac
