param([switch]$Recovery, [switch]$Benchmark)
$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
$taskPython = Join-Path (Get-Location) '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $taskPython)) { throw 'Run ./ops/prepare.ps1 first' }
$suites = @('verify.py', 'verify_replication.py', 'verify_operations.py')
if ($Recovery) { $suites += @('verify_services.py', 'verify_recovery.py', 'verify_failover.py') }
foreach ($suite in $suites) {
    & $taskPython -B (Join-Path 'tests' $suite)
    if ($LASTEXITCODE -ne 0) { throw "Failed: $suite" }
}
if ($Benchmark) {
    & $taskPython -B benchmarks/run.py --seconds 30 --clients 32 --batch 64
    if ($LASTEXITCODE -ne 0) { throw 'Benchmark failed' }
}
