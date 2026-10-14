# One-time (and after every `git pull`) setup of a Windows machine.
# Idempotent: safe to run again; it never overwrites an existing .env.
#
#   scripts\bootstrap.ps1                                   # role all (control plane + local worker)
#   scripts\bootstrap.ps1 -Role control-plane               # API/dashboard/orchestrator for the team
#   scripts\bootstrap.ps1 -Role worker -ControlUrl http://factory-host:8000 -RegistrationToken <token>
param(
    [ValidateSet("all", "control-plane", "worker")][string]$Role = "all",
    [string]$ControlUrl,
    [string]$RegistrationToken,
    [string]$PublicUrl
)
$ErrorActionPreference = "Stop"
$root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $root
$want = (Get-Content .python-version -Raw).Trim()

function Invoke-Checked([string]$exe, [string[]]$arguments) {
    & $exe @arguments
    if ($LASTEXITCODE -ne 0) { throw "$exe $($arguments -join ' ') failed with exit code $LASTEXITCODE" }
}

function Find-Python {
    if (Get-Command py -ErrorAction SilentlyContinue) {
        $exe = (& py "-$want" -c "import sys; print(sys.executable)" 2>$null)
        if ($LASTEXITCODE -eq 0 -and $exe) { return $exe.Trim() }
    }
    foreach ($name in @("python$want", "python", "python3")) {
        $cmd = Get-Command $name -ErrorAction SilentlyContinue
        if ($cmd) {
            $ver = (& $cmd.Source -c "import sys; print(f'{sys.version_info[0]}.{sys.version_info[1]}')" 2>$null)
            if ($ver -and $ver.Trim() -eq $want) { return $cmd.Source }
        }
    }
    throw "Python $want not found. Install it (winget install Python.Python.$want) and rerun."
}

if (-not (Get-Command git -ErrorAction SilentlyContinue)) { throw "git not found on PATH." }

# 1. Virtual environment on the pinned Python version (recreated if the version differs).
$venvPy = Join-Path $root ".venv\Scripts\python.exe"
if (Test-Path $venvPy) {
    $have = (& $venvPy -c "import sys; print(f'{sys.version_info[0]}.{sys.version_info[1]}')").Trim()
    if ($have -ne $want) { Write-Host "Recreating .venv ($have -> $want)"; Remove-Item -Recurse -Force .venv }
    else {
        & $venvPy -m pip --version *> $null
        if ($LASTEXITCODE -ne 0) {
            Write-Host "Recreating .venv (it has no pip; an earlier setup was interrupted)"
            Remove-Item -Recurse -Force .venv
        }
    }
}
if (-not (Test-Path $venvPy)) {
    $python = Find-Python
    Write-Host "Creating .venv with $python"
    Invoke-Checked $python @("-m", "venv", ".venv")
}

# 2. Exact dependency versions from requirements.lock, then the project itself.
Invoke-Checked $venvPy @("-m", "pip", "install", "--quiet", "--disable-pip-version-check", "-r", "requirements.lock")
Invoke-Checked $venvPy @("-m", "pip", "install", "--quiet", "--disable-pip-version-check", "--no-deps", "-e", ".")
$awf = Join-Path $root ".venv\Scripts\awf.exe"

# 3. Machine configuration (.env) - created once, never overwritten.
$envArgs = @("env", "init", "--role", $Role)
if ($ControlUrl) { $envArgs += @("--control-url", $ControlUrl) }
if ($RegistrationToken) { $envArgs += @("--registration-token", $RegistrationToken) }
if ($PublicUrl) { $envArgs += @("--public-url", $PublicUrl) }
Invoke-Checked $awf $envArgs

# 4. Database schema (control plane only) and a health report.
if ($Role -ne "worker") { Invoke-Checked $awf @("db", "upgrade") }
& $awf doctor
Write-Host "`nStart this machine with:  .\.venv\Scripts\awf start"
