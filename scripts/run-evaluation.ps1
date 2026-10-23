param(
    [ValidateRange(1, 300)][int]$Limit = 1,
    [ValidateRange(1, 16)][int]$Concurrency = 10,
    [string]$DataPath = '',
    [ValidatePattern('^[A-Za-z0-9_-]+$')][string]$ResultName = 'deepseek_v41_flash_two_phase',
    [ValidatePattern('^[A-Za-z0-9_-]+$')][string]$OracleResultName = 'oracle_smoke',
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
# Health values reported by the agent service (agent/server.py).
$agentName = 'deepseek-v4.1-flash-tool-selecting-two-phase'
# The simulator stays on the default model; AGENT_MODEL_ID only changes the agent.
$simulatorModelId = 'deepseek/deepseek-v4.1-flash'
$modelId = if ($env:AGENT_MODEL_ID) { $env:AGENT_MODEL_ID.Trim() } else { $simulatorModelId }
$agentUrl = 'http://127.0.0.1:6000'
$simulatorUrl = 'http://127.0.0.1:6001'
# The agent offers the nine official tools with their official prices.
# /health reports the real tool set, so a stale service that only shares the
# name (for example the previous seven-tool version) is rejected instead of reused.
$expectedTools = [ordered]@{
    get_schema = 1.0
    get_all_column_meanings = 1.0
    get_column_meaning = 0.5
    get_all_external_knowledge_names = 0.5
    get_knowledge_definition = 0.5
    get_all_knowledge_definitions = 1.0
    execute_sql = 1.0
    ask_user = 2.0
    submit_sql = 3.0
}

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

# AGENT_MODEL_PROVIDER=gms sends the agent's calls to the GMS proxy with
# GMS_KEY. The simulator still uses CMD_API_KEY.
function Import-GmsApiKey([string]$PythonExe, [string]$EnvPath) {
    if ([string]$env:AGENT_MODEL_PROVIDER -ne 'gms') { return }
    if (!$env:GMS_KEY -and (Test-Path -LiteralPath $EnvPath)) {
        $loadedKey = & $PythonExe -c "import sys; from dotenv import dotenv_values; sys.stdout.write(dotenv_values(sys.argv[1], encoding='utf-8-sig').get('GMS_KEY') or '')" $EnvPath
        if ($LASTEXITCODE -ne 0) { throw 'Could not read GMS_KEY from the project .env file.' }
        if ($loadedKey) { $env:GMS_KEY = $loadedKey }
    }
    if (!$env:GMS_KEY) {
        throw 'AGENT_MODEL_PROVIDER=gms needs GMS_KEY in the project .env file or the current PowerShell environment.'
    }
}

function Import-LangfuseSettings([string]$PythonExe, [string]$EnvPath) {
    if (!(Test-Path -LiteralPath $EnvPath)) { return }

    # Values already present in the environment win. The keys are captured
    # without being written to the console, the results or the trace.
    $json = & $PythonExe -c "import json, sys; from dotenv import dotenv_values; v = dotenv_values(sys.argv[1], encoding='utf-8-sig'); sys.stdout.write(json.dumps({k: v.get(k) or '' for k in ('LANGFUSE_PUBLIC_KEY', 'LANGFUSE_SECRET_KEY', 'LANGFUSE_BASE_URL')}))" $EnvPath
    if ($LASTEXITCODE -ne 0) { throw 'Could not read Langfuse settings from the project .env file.' }
    $settings = $json | ConvertFrom-Json
    if (!$env:LANGFUSE_PUBLIC_KEY -and $settings.LANGFUSE_PUBLIC_KEY) {
        $env:LANGFUSE_PUBLIC_KEY = $settings.LANGFUSE_PUBLIC_KEY
    }
    if (!$env:LANGFUSE_SECRET_KEY -and $settings.LANGFUSE_SECRET_KEY) {
        $env:LANGFUSE_SECRET_KEY = $settings.LANGFUSE_SECRET_KEY
    }
    if (!$env:LANGFUSE_BASE_URL -and $settings.LANGFUSE_BASE_URL) {
        $env:LANGFUSE_BASE_URL = $settings.LANGFUSE_BASE_URL
    }
}

# One evaluation run is one Langfuse session. The runner supplies both the
# grouping identifier and the code version so traces can be compared by SHA.
function Set-TraceMetadata([string]$ProjectRoot, [string]$ResultName) {
    $sha = ''
    $shaOutput = & git -C $ProjectRoot rev-parse HEAD 2>$null
    if ($LASTEXITCODE -eq 0) { $sha = ([string]$shaOutput).Trim() }
    $dirty = $false
    $changes = & git -C $ProjectRoot status --porcelain --untracked-files=no 2>$null
    if ($LASTEXITCODE -eq 0 -and ($changes | Where-Object { $_ })) { $dirty = $true }
    if ($sha) {
        if ($dirty) { $env:AGENT_CODE_VERSION = "$sha-dirty" }
        else { $env:AGENT_CODE_VERSION = $sha }
    }
    $stamp = [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssZ')
    $env:AGENT_TRACE_SESSION_ID = "$ResultName-$stamp"
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

# A leftover AGENT_MODE from an older runner must fail with its value and a
# removal instruction instead of surfacing only a service startup timeout.
function Assert-AgentModeEnvironment {
    $mode = [string]$env:AGENT_MODE
    if ($mode -and $mode.Trim().ToLowerInvariant() -ne 'tools') {
        throw "AGENT_MODE='$mode' is set, but only the tool-selecting agent remains. Run 'Remove-Item Env:AGENT_MODE' and retry."
    }
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

# A reused or freshly started agent must be the tool-selecting two-phase
# agent, not a stale server with a different model or tool set.
function Assert-BaselineAgent([string]$Url) {
    $info = Invoke-RestMethod -Uri "$Url/health" -TimeoutSec 5
    if ($info.service -ne 'system_agent' -or $info.mode -ne 'tools' -or
        $info.agent -ne $agentName -or $info.model -ne $modelId) {
        throw "Port 6000 is not the tool-selecting two-phase agent ($agentName / $modelId). Stop that service before evaluation."
    }
    $reported = @{}
    foreach ($tool in @($info.tools)) { $reported[[string]$tool.name] = [double]$tool.cost }
    if ($reported.Count -ne $expectedTools.Count) {
        throw "The agent offers $($reported.Count) tool(s) instead of $($expectedTools.Count). Restart port 6000 with the current code."
    }
    foreach ($name in $expectedTools.Keys) {
        if (!$reported.ContainsKey($name) -or $reported[$name] -ne [double]$expectedTools[$name]) {
            throw "The agent does not offer '$name' at the official price. Restart port 6000 with the current code."
        }
    }
}

# The agent asks the simulator real questions, so the running simulator
# must report the Command Code connection instead of the vendor's default
# Anthropic model without a key.
function Assert-Simulator([string]$Url) {
    $info = Invoke-RestMethod -Uri "$Url/health" -TimeoutSec 5
    if ($info.service -ne 'user_simulator_command_code' -or $info.model -ne $simulatorModelId -or
        $info.prompt_version -ne 'v2' -or [int]$info.max_retries -ne 0 -or
        [double]$info.max_output_tokens -ne 16384 -or !$info.api_key_present) {
        throw "The user simulator on port 6001 is not the Command Code v2 connection (service=$($info.service), model=$($info.model), prompt_version=$($info.prompt_version), api_key_present=$($info.api_key_present))."
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

# Fail before containers or an agent start when an old runner left a removed
# mode in this shell.
Assert-AgentModeEnvironment

# The simulator container receives CMD_API_KEY through the compose
# environment, so the key is loaded before the stack starts. The agent process
# started below uses the same value.
Import-AgentApiKey -PythonExe $pythonExe -EnvPath (Join-Path $projectRoot '.env')
Import-GmsApiKey -PythonExe $pythonExe -EnvPath (Join-Path $projectRoot '.env')
# Langfuse keys and the run-level trace metadata only reach the host processes;
# tracing turns into a no-op when either key is missing.
Import-LangfuseSettings -PythonExe $pythonExe -EnvPath (Join-Path $projectRoot '.env')
Set-TraceMetadata -ProjectRoot $projectRoot -ResultName $ResultName
if ($env:LANGFUSE_PUBLIC_KEY -and $env:LANGFUSE_SECRET_KEY) {
    # The run identifier is not a credential and lets the trace be found later.
    Write-Output "Langfuse trace session: $($env:AGENT_TRACE_SESSION_ID)"
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
    Assert-Simulator $simulatorUrl

    # Reuse a running agent only when it reports the current tool set.
    $existingAgent = $null
    try { $existingAgent = Invoke-RestMethod "$agentUrl/health" -TimeoutSec 2 } catch { }
    if ($existingAgent) {
        Assert-BaselineAgent $agentUrl
    } else {
        if (-not $env:CMD_API_KEY) {
            throw 'CMD_API_KEY is missing. Set it in the project .env file or the current PowerShell environment.'
        }
        $agentProcess = Start-Process -FilePath $pythonExe -WindowStyle Hidden -PassThru `
            -ArgumentList '-m', 'uvicorn', 'agent.server:app', '--host', '127.0.0.1', '--port', '6000' `
            -WorkingDirectory $projectRoot `
            -RedirectStandardOutput (Join-Path $runtimeDir 'agent.stdout.log') `
            -RedirectStandardError (Join-Path $runtimeDir 'agent.stderr.log')
        Wait-Service "$agentUrl/health"
        Assert-BaselineAgent $agentUrl
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
