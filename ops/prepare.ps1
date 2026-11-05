$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
if (-not (Test-Path -LiteralPath '.venv/Scripts/python.exe')) {
    python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Python environment creation failed' }
}
& ./.venv/Scripts/python.exe -m pip install -r requirements.txt --disable-pip-version-check
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed' }
docker compose up -d --build --wait
if ($LASTEXITCODE -ne 0) { throw 'Database startup failed' }
Write-Output 'Ready. Run ./ops/check.ps1 or follow DEFENSE_WALKTHROUGH.md.'
