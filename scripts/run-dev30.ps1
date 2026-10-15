param(
    [ValidateRange(1, 16)][int]$Concurrency = 10
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonExe = Join-Path $projectRoot '.venv\Scripts\python.exe'
$subset = Join-Path $projectRoot 'private\lite30.combined.jsonl'
$env:PYTHONUTF8 = '1'

# Reuse the fixed selection manifest; do not resample on each evaluation.
& $pythonExe (Join-Path $PSScriptRoot 'select_dev_set.py')
if ($LASTEXITCODE -ne 0) { throw 'Preparing the fixed 30-task dataset failed.' }

& (Join-Path $PSScriptRoot 'run-evaluation.ps1') -DataPath $subset -Limit 30 `
    -Concurrency $Concurrency -ResultName 'deepseek_v41_flash_two_phase_dev30' -OracleResultName 'dev30_oracle'
