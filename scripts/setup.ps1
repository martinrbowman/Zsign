# Zsign setup for Windows: creates .venv (if it doesn't already exist)
# and installs Zsign into it. Safe to re-run -- it reuses an existing
# .venv and just reinstalls on top of it.
$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = (Resolve-Path (Join-Path $ScriptDir "..")).Path
$VenvDir = Join-Path $RepoRoot ".venv"

$PythonCmd = $null
$PythonArgs = @()
if ($env:PYTHON) {
    $PythonCmd = $env:PYTHON
} elseif (Get-Command py -ErrorAction SilentlyContinue) {
    $PythonCmd = "py"
    $PythonArgs = @("-3")
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
    $PythonCmd = "python"
} else {
    Write-Error "python not found. Install Python 3.10+ first (from python.org or the Microsoft Store)."
    exit 1
}

$versionOk = & $PythonCmd @PythonArgs -c "import sys; print(1 if sys.version_info >= (3, 10) else 0)"
if ($versionOk -ne "1") {
    & $PythonCmd @PythonArgs --version
    Write-Error "Python is older than the required 3.10 (pyproject.toml's requires-python)."
    exit 1
}

if (-not (Test-Path $VenvDir)) {
    Write-Host "Creating venv at $VenvDir"
    & $PythonCmd @PythonArgs -m venv $VenvDir
} else {
    Write-Host "Reusing existing venv at $VenvDir"
}

$VenvPython = Join-Path $VenvDir "Scripts\python.exe"
if (-not (Test-Path $VenvPython)) {
    Write-Error "venv creation appears to have failed: $VenvPython not found."
    exit 1
}

& $VenvPython -m pip install --upgrade pip -q
& $VenvPython -m pip install -e "$RepoRoot[dev]"

Write-Host ""
Write-Host "Setup complete."
Write-Host "Activate in a new shell with:  $VenvDir\Scripts\Activate.ps1"
Write-Host "Run the GUI with:              zsign-gui"
Write-Host "Run the test suite with:       pytest -q"
Write-Host ""
Write-Host "If Activate.ps1 is blocked by execution policy, run once as"
Write-Host "admin: Set-ExecutionPolicy -Scope CurrentUser RemoteSigned"
