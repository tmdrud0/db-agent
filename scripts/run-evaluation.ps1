param(
    [ValidateRange(1, 300)][int]$Limit = 1,
    [ValidateRange(1, 16)][int]$Concurrency = 10,
    [string]$DataPath = '',
    [ValidatePattern('^[A-Za-z0-9_-]+$')][string]$ResultName = 'empty_submit',
    [ValidatePattern('^[A-Za-z0-9_-]+$')][string]$OracleResultName = 'oracle_smoke'
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonExe = Join-Path $projectRoot '.venv\Scripts\python.exe'
$benchmarkRoot = Join-Path $projectRoot 'vendor\BIRD-Interact\BIRD-Interact-ADK'
$combinedData = Join-Path $benchmarkRoot 'bird-interact-lite\bird_interact_data.combined.jsonl'
if ($DataPath) { $combinedData = [IO.Path]::GetFullPath($DataPath) }
$resultsDir = Join-Path $projectRoot 'results'
$runtimeDir = Join-Path $projectRoot '.runtime-tmp'
$agentProcess = $null
$env:PYTHONUTF8 = '1'
$env:DATASET = 'lite'
$env:DB_ENV_URL = 'http://127.0.0.1:6002'
$env:USER_SIM_URL = 'http://127.0.0.1:6001'

if (!(Test-Path -LiteralPath $pythonExe) -or !(Test-Path -LiteralPath $combinedData)) {
    throw 'Python environment or combined Lite data is missing. See docs/actual-grading.md.'
}
New-Item -ItemType Directory -Force $resultsDir, $runtimeDir | Out-Null

function Wait-Service([string]$Url) {
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        try {
            $health = Invoke-RestMethod -Uri $Url -TimeoutSec 2
            if ($health.status -eq 'healthy') { return }
        } catch { }
        Start-Sleep -Milliseconds 500
    }
    throw "Service did not become ready: $Url"
}

Push-Location $projectRoot
try {
    & docker compose -p db-agent-smoke -f compose.benchmark.yml up --build -d --wait --wait-timeout 900
    if ($LASTEXITCODE -ne 0) { throw 'Benchmark containers could not start.' }
    Wait-Service 'http://127.0.0.1:6002/health'
    Wait-Service 'http://127.0.0.1:6001/health'

    # Support future implementations using the same server API.
    $existingAgent = $null
    try { $existingAgent = Invoke-RestMethod 'http://127.0.0.1:6000/health' -TimeoutSec 2 } catch { }
    if ($existingAgent) {
        if ($existingAgent.service -ne 'system_agent') {
            throw 'Port 6000 is not running a system agent. Stop that service before evaluation.'
        }
    } else {
        $agentProcess = Start-Process -FilePath $pythonExe -WindowStyle Hidden -PassThru `
            -ArgumentList '-m', 'uvicorn', 'agent.server:app', '--host', '127.0.0.1', '--port', '6000' `
            -WorkingDirectory $projectRoot `
            -RedirectStandardOutput (Join-Path $runtimeDir 'agent.stdout.log') `
            -RedirectStandardError (Join-Path $runtimeDir 'agent.stderr.log')
        Wait-Service 'http://127.0.0.1:6000/health'
    }

    Push-Location $benchmarkRoot
    try {
        # Positive control: original ground-truth SQL, no agent/model calls.
        & $pythonExe -m orchestrator.runner --mode oracle --data $combinedData `
            --limit $Limit --concurrency $Concurrency --output (Join-Path $resultsDir "$OracleResultName.json")
        if ($LASTEXITCODE -ne 0) { throw 'Oracle runner failed.' }
        $oracle = Get-Content -LiteralPath (Join-Path $resultsDir "$OracleResultName.json") -Raw | ConvertFrom-Json
        foreach ($task in $oracle.results) {
            if ($task.error) { throw "Oracle infrastructure error: $($task.error)" }
        }
        if ($oracle.metrics.phase1_count -eq 0) {
            throw 'No oracle submission passed. Check DB contents and ground truth before interpreting an empty-submit score.'
        }

        # Evaluate the current custom agent using exactly the same input tasks.
        & $pythonExe -m orchestrator.runner --mode a-interact --data $combinedData `
            --limit $Limit --concurrency $Concurrency --output (Join-Path $resultsDir "$ResultName.json")
        if ($LASTEXITCODE -ne 0) { throw 'Custom agent runner failed.' }

        & $pythonExe -m orchestrator.report (Join-Path $resultsDir "$OracleResultName.json")
        if ($LASTEXITCODE -ne 0) { throw 'Oracle report generation failed.' }
        & $pythonExe -m orchestrator.report (Join-Path $resultsDir "$ResultName.json")
        if ($LASTEXITCODE -ne 0) { throw 'Custom agent report generation failed.' }
    } finally { Pop-Location }

    foreach ($name in @($OracleResultName, $ResultName)) {
        $result = Get-Content -LiteralPath (Join-Path $resultsDir "$name.json") -Raw | ConvertFrom-Json
        foreach ($task in $result.results) {
            if ($task.error) { throw "$name recorded an infrastructure error: $($task.error)" }
        }
        Write-Output "$name metrics: $($result.metrics | ConvertTo-Json -Compress)"
    }
    Write-Output "Result files: $resultsDir"
} finally {
    # Stop only the agent process started by this script; leave a user's agent alone.
    if ($agentProcess -and !$agentProcess.HasExited) {
        Stop-Process -Id $agentProcess.Id
    }
    Pop-Location
}
