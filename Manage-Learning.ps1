param(
    [Parameter(ValueFromRemainingArguments=$true)]
    [string[]]$Arguments
)
$ErrorActionPreference = 'Stop'
Push-Location $PSScriptRoot
try {
    & 'D:\AppData\MathLearningWeb\venv\Scripts\python.exe' -m math_learning @Arguments
    exit $LASTEXITCODE
} finally { Pop-Location }
