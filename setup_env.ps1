$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

# -----------------------------
# OS detection (PS 5.1 + PS 7+ kompatibel, ohne $IsWindows)
# -----------------------------
function Test-IsWindows {
    # 1) Klassisch auf Windows (gibt es auch in PS7 auf Windows)
    if ($env:OS -eq 'Windows_NT') { return $true }

    # 2) Windows PowerShell ("Desktop") läuft nur auf Windows
    if ($PSVersionTable.PSEdition -eq 'Desktop') { return $true }

    # 3) PowerShell 6/7: Platform existiert oft
    if ($PSVersionTable.ContainsKey('Platform') -and $PSVersionTable.Platform -eq 'Win32NT') { return $true }

    return $false
}

$script:OnWindows = Test-IsWindows

# Make paths independent from current working directory
$root = $PSScriptRoot
Set-Location $root

# -----------------------------
# Config
# -----------------------------
$venvPath          = Join-Path $root ".venv"
$requirementsFile  = Join-Path $root "_requirements.txt"
$extensionsFile    = Join-Path $root "_extensions.txt"

# Windows-only: which Python to use via the py launcher (if present)
# If you need 32-bit on Windows, set to "-3.13-32" (or your exact installed version)
$windowsPySpec = "-3.13"   # e.g. "-3.13" or "-3.13-32"

# -----------------------------
# Helpers
# -----------------------------
function Invoke-Native {
    param(
        [Parameter(Mandatory = $true)][string] $Exe,
        [Parameter(Mandatory = $false)][string[]] $Args = @()
    )

    & $Exe @Args
    if ($LASTEXITCODE -ne 0) {
        $argStr = ($Args -join " ")
        throw "Command failed ($LASTEXITCODE): $Exe $argStr"
    }
}

function Resolve-PythonRunner {
    # Prefer Windows py launcher if available (nur auf Windows sinnvoll)
    if ($script:OnWindows -and (Get-Command py -ErrorAction SilentlyContinue)) {
        return @{ Exe = "py"; Args = @($windowsPySpec) }
    }

    foreach ($cmd in @("python3", "python")) {
        if (Get-Command $cmd -ErrorAction SilentlyContinue) {
            return @{ Exe = $cmd; Args = @() }
        }
    }

    throw "No Python found. On Ubuntu install: sudo apt install python3 python3-venv python3-pip"
}

function Get-ActivateScriptPath([string] $Venv) {
    if ($script:OnWindows) { return (Join-Path $Venv "Scripts/Activate.ps1") }
    return (Join-Path $Venv "bin/Activate.ps1")
}

function Get-VenvPythonPath([string] $Venv) {
    if ($script:OnWindows) { return (Join-Path $Venv "Scripts/python.exe") }
    return (Join-Path $Venv "bin/python")
}

function Ensure-Pip([string] $PythonExe) {
    # Check if pip exists
    & $PythonExe -m pip --version *> $null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "pip not found in venv -> bootstrapping with ensurepip..."
        Invoke-Native $PythonExe @("-m", "ensurepip", "--upgrade")
    }

    Write-Host "Upgrading pip/setuptools/wheel..."
    Invoke-Native $PythonExe @("-m", "pip", "install", "-U", "pip", "setuptools", "wheel")
}

function Resolve-CodeCli {
    foreach ($cmd in @("code", "code-insiders", "codium", "code-oss")) {
        if (Get-Command $cmd -ErrorAction SilentlyContinue) { return $cmd }
    }
    return $null
}

# -----------------------------
# Create venv
# -----------------------------
Write-Host "=== Creating/checking virtual environment '$venvPath' ==="
$py = Resolve-PythonRunner

if (-not (Test-Path $venvPath)) {
    Write-Host "Creating venv using: $($py.Exe) $($py.Args -join ' ')"
    Invoke-Native $py.Exe @($py.Args + @("-m", "venv", $venvPath))
} else {
    Write-Host "Venv already exists, skipping creation."
}

$venvPython = Get-VenvPythonPath $venvPath
if (-not (Test-Path $venvPython)) {
    throw "Venv python not found at '$venvPython'."
}

# -----------------------------
# Ensure pip + install requirements
# -----------------------------
Write-Host "=== Ensuring pip exists ==="
Ensure-Pip $venvPython

if (Test-Path $requirementsFile) {
    Write-Host "=== Installing Python packages from '$requirementsFile' ==="
    Invoke-Native $venvPython @("-m", "pip", "install", "-r", $requirementsFile)
} else {
    Write-Warning "Requirements file '$requirementsFile' not found. Skipping."
}

# -----------------------------
# Install VS Code extensions (optional)
# -----------------------------
Write-Host "=== Installing VS Code extensions (optional) ==="
if (Test-Path $extensionsFile) {
    $codeCmd = Resolve-CodeCli
    if (-not $codeCmd) {
        Write-Warning "VS Code CLI not found (code/codium). Skipping extension install."
    } else {
        Get-Content $extensionsFile | ForEach-Object {
            $ext = $_.Trim()
            if ($ext -and -not $ext.StartsWith("#")) {
                Write-Host "Installing VS Code extension '$ext'..."
                Invoke-Native $codeCmd @("--install-extension", $ext)
            }
        }
    }
} else {
    Write-Host "No _extensions.txt found. Skipping extension install."
}

# -----------------------------
# Activate (only affects the current PowerShell session)
# -----------------------------
$activateScript = Get-ActivateScriptPath $venvPath
if (Test-Path $activateScript) {
    Write-Host "=== Activating venv in this PowerShell session ==="
    . $activateScript
    Write-Host "Active venv: $env:VIRTUAL_ENV"
} else {
    Write-Warning "Activate script not found at '$activateScript'. (venv still created and dependencies installed)"
}

Write-Host "=== Done. ==="
Write-Host "Tip: Use '$venvPython -m pip ...' to always target the venv explicitly."
