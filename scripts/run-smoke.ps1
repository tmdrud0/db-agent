param(
    [ValidateRange(1, 300)][int]$Limit = 1,
    [ValidateRange(1, 16)][int]$Concurrency = 10
)

# Original entry point: full Lite input, limited to the requested task count.
& (Join-Path $PSScriptRoot 'run-evaluation.ps1') -Limit $Limit -Concurrency $Concurrency
