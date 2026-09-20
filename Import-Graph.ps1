param(
    [Parameter(Mandatory)][string]$Source,
    [string]$DataDirectory = 'D:\AppData\MathLearningWeb',
    [string]$PreviewHtml,
    [string]$ExpectedRevision,
    [switch]$Apply
)
$ErrorActionPreference = 'Stop'
$pythonPath = 'D:\AppData\MathLearningWeb\venv\Scripts\python.exe'
$arguments = @('-m', 'math_learning', '--data-dir', $DataDirectory, 'graph-import', '--source', $Source)
if ($Apply) { $arguments += '--apply' }
if ($PreviewHtml) { $arguments += @('--preview-html', $PreviewHtml) }
if ($ExpectedRevision) { $arguments += @('--expected-revision', $ExpectedRevision) }
Push-Location $PSScriptRoot
try { & $pythonPath @arguments; $resultCode = $LASTEXITCODE }
finally { Pop-Location }
exit $resultCode
