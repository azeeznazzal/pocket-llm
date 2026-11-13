#!/usr/bin/env pwsh
# 1-Click PocketLLM launcher for Windows PowerShell.
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$py = $null
foreach ($candidate in @("py", "python", "python3")) {
    if (Get-Command $candidate -ErrorAction SilentlyContinue) {
        try {
            $argList = if ($candidate -eq "py") { @("-3", "-c", "import sys; exit(0 if sys.version_info >= (3,8) else 1)") } else { @("-c", "import sys; exit(0 if sys.version_info >= (3,8) else 1)") }
            & $candidate @argList 2>$null
            if ($LASTEXITCODE -eq 0) {
                $py = $candidate
                break
            }
        } catch {}
    }
}

if (-not $py) {
    Write-Host "Error: Python 3.8+ is required." -ForegroundColor Red
    Write-Host "Install via winget: winget install -e --id Python.Python.3.12"
    exit 1
}

if ($py -eq "py") {
    & py -3 "$PSScriptRoot\run.py" @args
} else {
    & $py "$PSScriptRoot\run.py" @args
}
exit $LASTEXITCODE
