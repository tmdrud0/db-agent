param(
    [string]$ConfigPath = ''
)

# Runs the private calibration verification scenarios (meaning checks and
# counterexamples) against the calibrated DB environment.

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonExe = Join-Path $projectRoot '.venv\Scripts\python.exe'
$configFile = Join-Path $projectRoot 'private\calibration\TASK-002.calibration.json'
if ($ConfigPath) { $configFile = (Resolve-Path -LiteralPath $ConfigPath).Path }
$env:PYTHONUTF8 = '1'

if (!(Test-Path -LiteralPath $pythonExe) -or !(Test-Path -LiteralPath $configFile)) {
    throw 'Python environment or calibration config is missing. See docs/setup.md.'
}

Push-Location $projectRoot
try {
    & docker compose -p db-agent-smoke -f compose.benchmark.yml -f compose.benchmark.calibrated.yml `
        up --build -d --wait --wait-timeout 900
    if ($LASTEXITCODE -ne 0) { throw 'The calibrated benchmark containers could not start.' }

    & $pythonExe (Join-Path $PSScriptRoot 'build_calibrated_set.py')
    if ($LASTEXITCODE -ne 0) { throw 'Building the calibrated input failed.' }

    & $pythonExe (Join-Path $PSScriptRoot 'verify_calibration.py') --config $configFile
    if ($LASTEXITCODE -ne 0) { throw 'Calibration verification found unexpected results.' }
    Write-Output 'Calibration verification passed.'
} finally {
    Pop-Location
}
