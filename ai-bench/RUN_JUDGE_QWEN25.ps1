[CmdletBinding()]
param(
    [string]$PythonPath = (Join-Path $PSScriptRoot '.venv/Scripts/python.exe'),
    [string]$RunRoot = (Join-Path $PSScriptRoot 'runs'),
    [string]$Source,
    [string]$JudgeRoot,
    [ValidateRange(4096, 32768)]
    [int]$NumCtx = 16384,
    [ValidateRange(1, 32768)]
    [int]$MaxOutput = 4096,
    [string]$Url = 'http://localhost:11434',
    [switch]$Resume
)

$ErrorActionPreference = 'Stop'
Push-Location -LiteralPath $PSScriptRoot
try {
    $resolvedPython = (Resolve-Path -LiteralPath $PythonPath).Path
    $judgeArgs = @('--run-root', $RunRoot, '--num-ctx', "$NumCtx",
                   '--max-output', "$MaxOutput", '--url', $Url)
    if ($Source) { $judgeArgs += @('--source', $Source) }
    if ($JudgeRoot) { $judgeArgs += @('--judge-root', $JudgeRoot) }
    if ($Resume) { $judgeArgs += '--resume' }
    & $resolvedPython 'scripts/run_judge_qwen25.py' @judgeArgs
    if ($LASTEXITCODE -ne 0) {
        throw "Judge run failed (exit $LASTEXITCODE)."
    }
}
finally {
    Pop-Location
}
