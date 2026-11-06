$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
$taskPython = Join-Path (Get-Location) '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $taskPython)) { throw 'Run ./ops/prepare.ps1 first' }
& $taskPython -X utf8 -B inventory_console.py --setup --demo all --no-pause --status
if ($LASTEXITCODE -ne 0) { throw 'Defense scenario verification failed' }
Write-Output 'All eight defense scenarios passed. The console is ready.'
