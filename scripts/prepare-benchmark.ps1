param(
    [string]$GroundTruthPath = '',
    [switch]$SkipInstall
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$benchmarkRef = '451fe2c3518ee1cf908d8139e2913483bd519381'
$datasetRef = 'f7881a9c2b9630cc4fc13b0c39279740b0a2fd87'
$benchmarkRoot = Join-Path $projectRoot 'vendor\BIRD-Interact\BIRD-Interact-ADK'
$dataRoot = Join-Path $benchmarkRoot 'bird-interact-lite'
$pythonExe = Join-Path $projectRoot '.venv\Scripts\python.exe'
$runtimeDir = Join-Path $projectRoot '.runtime-tmp'
if (!$GroundTruthPath) {
    $GroundTruthPath = Join-Path $projectRoot 'bird_interact_gt_kg_testcases_1008.jsonl'
}
$GroundTruthPath = (Resolve-Path -LiteralPath $GroundTruthPath).Path

function Ensure-Repository([string]$Url, [string]$Path, [string]$Ref) {
    if (!(Test-Path -LiteralPath $Path)) {
        & git -c core.autocrlf=false clone --depth 1 $Url $Path
        if ($LASTEXITCODE -ne 0) { throw "Clone failed: $Url" }
        $head = & git -C $Path rev-parse HEAD
        if ($head -ne $Ref) {
            & git -C $Path fetch --depth 1 origin $Ref
            if ($LASTEXITCODE -ne 0) { throw "Fetching pinned revision failed: $Ref" }
            & git -C $Path checkout --detach $Ref
            if ($LASTEXITCODE -ne 0) { throw "Checkout failed: $Ref" }
        }
    }
    $head = & git -C $Path rev-parse HEAD
    if ($LASTEXITCODE -ne 0 -or $head -ne $Ref) {
        throw "Expected revision $Ref in $Path. Existing files were left unchanged."
    }
}

$oldTemp = $env:TEMP
$oldTmp = $env:TMP
$oldUtf8 = $env:PYTHONUTF8
$oldLfs = $env:GIT_LFS_SKIP_SMUDGE
Push-Location $projectRoot
try {
    New-Item -ItemType Directory -Force $runtimeDir | Out-Null
    $env:TEMP = $runtimeDir
    $env:TMP = $runtimeDir
    $env:PYTHONUTF8 = '1'
    $env:GIT_LFS_SKIP_SMUDGE = '1'
    if (!(Test-Path -LiteralPath $pythonExe)) {
        & python -m venv .venv
        if ($LASTEXITCODE -ne 0) { throw 'Creating the Python environment failed.' }
    }
    if (!$SkipInstall) {
        & $pythonExe -m pip install -r requirements.benchmark.lock.txt
        if ($LASTEXITCODE -ne 0) { throw 'Installing benchmark dependencies failed.' }
    }
    Ensure-Repository 'https://github.com/bird-bench/BIRD-Interact.git' `
        (Join-Path $projectRoot 'vendor\BIRD-Interact') $benchmarkRef
    Ensure-Repository 'https://huggingface.co/datasets/birdsql/bird-interact-lite' $dataRoot $datasetRef
    $publicFile = Join-Path $dataRoot 'bird_interact_data.jsonl'
    $combinedFile = Join-Path $dataRoot 'bird_interact_data.combined.jsonl'
    & $pythonExe (Join-Path $benchmarkRoot 'scripts\combine_public_with_gt.py') `
        $publicFile $GroundTruthPath $combinedFile
    if ($LASTEXITCODE -ne 0) { throw 'Combining public data with ground truth failed.' }
    & $pythonExe (Join-Path $PSScriptRoot 'select_dev_set.py')
    if ($LASTEXITCODE -ne 0) { throw 'Validating the fixed 30-task selection failed.' }
    Write-Output 'Prepared. Start Docker Desktop, then run .\scripts\run-dev30.ps1.'
} finally {
    $env:TEMP = $oldTemp
    $env:TMP = $oldTmp
    $env:PYTHONUTF8 = $oldUtf8
    $env:GIT_LFS_SKIP_SMUDGE = $oldLfs
    Pop-Location
}
