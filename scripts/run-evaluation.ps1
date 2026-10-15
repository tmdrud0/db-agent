param(
    [ValidateRange(1, 300)][int]$Limit = 1,
    [ValidateRange(1, 16)][int]$Concurrency = 10,
    [string]$DataPath = '',
    [ValidatePattern('^[A-Za-z0-9_-]+$')][string]$ResultName = 'deepseek_v41_flash_two_phase',
    [ValidatePattern('^[A-Za-z0-9_-]+$')][string]$OracleResultName = 'oracle_smoke',
    [ValidateSet('fixed', 'tools')][string]$AgentMode = 'fixed',
    [switch]$Calibrated
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonExe = Join-Path $projectRoot '.venv\Scripts\python.exe'
$benchmarkRoot = Join-Path $projectRoot 'vendor\BIRD-Interact\BIRD-Interact-ADK'
$combinedData = Join-Path $benchmarkRoot 'bird-interact-lite\bird_interact_data.combined.jsonl'
# The original run stays on the default stack and the full Lite input. The
# calibrated run is requested explicitly and defaults to the calibrated input.
if ($DataPath) { $combinedData = [IO.Path]::GetFullPath($DataPath) }
elseif ($Calibrated) { $combinedData = Join-Path $projectRoot 'private\lite30.calibrated.jsonl' }
$resultsDir = Join-Path $projectRoot 'results'
$runtimeDir = Join-Path $projectRoot '.runtime-tmp'
$agentProcess = $null
$env:PYTHONUTF8 = '1'
$env:DATASET = 'lite'
$env:DB_ENV_URL = 'http://127.0.0.1:6002'
$env:USER_SIM_URL = 'http://127.0.0.1:6001'
# Health values reported by the selected agent mode (agent/server.py).
$agentNames = @{
    fixed = 'deepseek-v4.1-flash-fixed-context-two-phase'
    tools = 'deepseek-v4.1-flash-tool-selecting-two-phase'
}
$agentName = $agentNames[$AgentMode]
$modelId = 'deepseek/deepseek-v4.1-flash'
$agentUrl = 'http://127.0.0.1:6000'

if (!(Test-Path -LiteralPath $pythonExe) -or !(Test-Path -LiteralPath $combinedData)) {
    throw 'Python environment or combined Lite data is missing. See docs/setup.md.'
}
New-Item -ItemType Directory -Force $resultsDir, $runtimeDir | Out-Null

function Import-AgentApiKey([string]$PythonExe, [string]$EnvPath) {
    if ($env:CMD_API_KEY -or !(Test-Path -LiteralPath $EnvPath)) { return }

    # Parse dotenv instead of executing it. Capture the key without logging it.
    $loadedKey = & $PythonExe -c "import sys; from dotenv import dotenv_values; sys.stdout.write(dotenv_values(sys.argv[1], encoding='utf-8-sig').get('CMD_API_KEY') or '')" $EnvPath
    if ($LASTEXITCODE -ne 0) { throw 'Could not read CMD_API_KEY from the project .env file.' }
    if ($loadedKey) { $env:CMD_API_KEY = $loadedKey }
}

# PowerShell 5.1 promotes a native command's stderr to a terminating error while
# $ErrorActionPreference is Stop, which breaks non-interactive runs (CI, task
# schedulers). External tools run with the preference relaxed; the exit code is
# checked here as before.
function Invoke-External([string]$Label, [scriptblock]$Command) {
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try { & $Command } finally { $ErrorActionPreference = $previous }
    if ($LASTEXITCODE -ne 0) { throw "$Label (exit code $LASTEXITCODE)." }
}

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

# A reused or freshly started agent must be the intended two-phase agent for
# the requested mode, not a stale server with a different model or settings.
function Assert-BaselineAgent([string]$Url, [string]$Mode) {
    $info = Invoke-RestMethod -Uri "$Url/health" -TimeoutSec 5
    if ($info.service -ne 'system_agent' -or $info.mode -ne $Mode -or
        $info.agent -ne $agentName -or $info.model -ne $modelId) {
        throw "Port 6000 is not the '$Mode' two-phase agent ($agentName / $modelId). Stop that service before evaluation."
    }
}

# The requested stack must match the running DB service: the calibrated wrapper
# reports /calibration, the original service answers 404 there.
function Assert-Calibration([bool]$ExpectCalibrated) {
    $info = $null
    try { $info = Invoke-RestMethod -Uri 'http://127.0.0.1:6002/calibration' -TimeoutSec 5 } catch { }
    if ($ExpectCalibrated -and (!$info -or $info.service -ne 'db_environment_calibration')) {
        throw 'Calibrated evaluation was requested but port 6002 is not reporting the calibrated DB service.'
    }
    if (!$ExpectCalibrated -and $info) {
        throw 'Port 6002 reports a calibrated DB service. Re-run with -Calibrated or stop the calibrated stack.'
    }
    if ($ExpectCalibrated) {
        Write-Output "Calibration active: $($info | ConvertTo-Json -Compress)"
    }
}

Push-Location $projectRoot
try {
    $composeFiles = @('-f', 'compose.benchmark.yml')
    if ($Calibrated) { $composeFiles += @('-f', 'compose.benchmark.calibrated.yml') }
    Invoke-External 'Benchmark containers could not start' {
        docker compose -p db-agent-smoke @composeFiles up --build -d --wait --wait-timeout 900
    }
    Wait-Service 'http://127.0.0.1:6002/health'
    Wait-Service 'http://127.0.0.1:6001/health'
    Assert-Calibration $Calibrated

    # Reuse a running agent only when it reports the requested mode.
    $existingAgent = $null
    try { $existingAgent = Invoke-RestMethod "$agentUrl/health" -TimeoutSec 2 } catch { }
    if ($existingAgent) {
        Assert-BaselineAgent $agentUrl $AgentMode
    } else {
        Import-AgentApiKey -PythonExe $pythonExe -EnvPath (Join-Path $projectRoot '.env')
        if (-not $env:CMD_API_KEY) {
            throw 'CMD_API_KEY is missing. Set it in the project .env file or the current PowerShell environment.'
        }
        $env:AGENT_MODE = $AgentMode
        $agentProcess = Start-Process -FilePath $pythonExe -WindowStyle Hidden -PassThru `
            -ArgumentList '-m', 'uvicorn', 'agent.server:app', '--host', '127.0.0.1', '--port', '6000' `
            -WorkingDirectory $projectRoot `
            -RedirectStandardOutput (Join-Path $runtimeDir 'agent.stdout.log') `
            -RedirectStandardError (Join-Path $runtimeDir 'agent.stderr.log')
        Wait-Service "$agentUrl/health"
        Assert-BaselineAgent $agentUrl $AgentMode
    }

    Push-Location $benchmarkRoot
    try {
        # Positive control: original ground-truth SQL, no agent/model calls.
        Invoke-External 'Oracle runner failed' {
            & $pythonExe -m orchestrator.runner --mode oracle --data $combinedData `
                --limit $Limit --concurrency $Concurrency --output (Join-Path $resultsDir "$OracleResultName.json")
        }
        $oracle = Get-Content -LiteralPath (Join-Path $resultsDir "$OracleResultName.json") -Raw | ConvertFrom-Json
        foreach ($task in $oracle.results) {
            if ($task.error) { throw "Oracle infrastructure error: $($task.error)" }
        }
        if ($oracle.metrics.phase1_count -eq 0) {
            throw 'No oracle submission passed. Check DB contents and ground truth before interpreting the baseline score.'
        }

        # Evaluate the current custom agent using exactly the same input tasks.
        Invoke-External 'Custom agent runner failed' {
            & $pythonExe -m orchestrator.runner --mode a-interact --data $combinedData `
                --limit $Limit --concurrency $Concurrency --output (Join-Path $resultsDir "$ResultName.json")
        }

        Invoke-External 'Oracle report generation failed' {
            & $pythonExe -m orchestrator.report (Join-Path $resultsDir "$OracleResultName.json")
        }
        Invoke-External 'Custom agent report generation failed' {
            & $pythonExe -m orchestrator.report (Join-Path $resultsDir "$ResultName.json")
        }
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
