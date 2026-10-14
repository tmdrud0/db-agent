param(
    [ValidateRange(1, 300)][int]$Limit = 30,
    [ValidateRange(1, 16)][int]$Concurrency = 10
)

# Calibrated oracle run: builds the calibrated input, starts the calibrated DB
# environment, and grades the fixed development set with ground-truth SQL only.
# The original run (scripts/run-dev30.ps1) keeps using the default stack.

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonExe = Join-Path $projectRoot '.venv\Scripts\python.exe'
$benchmarkRoot = Join-Path $projectRoot 'vendor\BIRD-Interact\BIRD-Interact-ADK'
$calibratedData = Join-Path $projectRoot 'private\lite30.calibrated.jsonl'
$oracleResult = Join-Path $projectRoot 'results\dev30_oracle_calibrated.json'
$resultsDir = Join-Path $projectRoot 'results'
$env:PYTHONUTF8 = '1'
$env:DATASET = 'lite'
$env:DB_ENV_URL = 'http://127.0.0.1:6002'

if (!(Test-Path -LiteralPath $pythonExe)) {
    throw 'Python environment is missing. See docs/setup.md.'
}
if (!(Test-Path -LiteralPath $calibratedData)) {
    throw 'Calibrated input is missing. Run scripts/build_calibrated_set.py first.'
}

Push-Location $projectRoot
try {
    & docker compose -p db-agent-smoke -f compose.benchmark.yml -f compose.benchmark.calibrated.yml `
        up --build -d --wait --wait-timeout 900
    if ($LASTEXITCODE -ne 0) { throw 'The calibrated benchmark containers could not start.' }

    $calibrated = $false
    for ($attempt = 0; $attempt -lt 60; $attempt++) {
        try {
            $info = Invoke-RestMethod -Uri 'http://127.0.0.1:6002/calibration' -TimeoutSec 2
            if ($info.service -eq 'db_environment_calibration') { $calibrated = $true; break }
        } catch { }
        Start-Sleep -Milliseconds 500
    }
    if (!$calibrated) {
        throw 'The calibrated DB environment service did not become ready. Check docker compose logs db-environment.'
    }
    Write-Output "Calibration active: $($info | ConvertTo-Json -Compress)"

    Push-Location $benchmarkRoot
    try {
        & $pythonExe -m orchestrator.runner --mode oracle --data $calibratedData `
            --limit $Limit --concurrency $Concurrency --output $oracleResult
        if ($LASTEXITCODE -ne 0) { throw 'Calibrated oracle runner failed.' }
        & $pythonExe -m orchestrator.report $oracleResult
        if ($LASTEXITCODE -ne 0) { throw 'Calibrated oracle report generation failed.' }
    }
    finally { Pop-Location }

    $oracle = Get-Content -LiteralPath $oracleResult -Raw | ConvertFrom-Json
    foreach ($task in $oracle.results) {
        if ($task.error) { throw "Oracle infrastructure error in $($task.task_id): $($task.error)" }
    }
    if ($oracle.results.Count -ne $Limit) {
        throw "Expected $Limit graded tasks, found $($oracle.results.Count)."
    }
    if ($oracle.metrics.phase1_count -ne $Limit -or $oracle.metrics.phase2_count -ne $Limit) {
        throw "Phase results incomplete: $($oracle.metrics | ConvertTo-Json -Compress)"
    }
    if ([math]::Abs($oracle.metrics.average_reward - 1.0) -gt 0.000001) {
        throw "Unexpected average reward: $($oracle.metrics.average_reward)"
    }

    Write-Output "Calibrated oracle metrics: $($oracle.metrics | ConvertTo-Json -Compress)"
    $oracle.results | Sort-Object task_id | ForEach-Object {
        Write-Output ("{0}: phase1={1} phase2={2} reward={3}" -f $_.task_id, $_.phase1_passed, $_.phase2_passed, $_.total_reward)
    }
    Write-Output "Result files: $oracleResult and results/dev30_oracle_calibrated.html"
} finally {
    Pop-Location
}
