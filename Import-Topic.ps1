param(
    [Parameter(Mandatory)][string]$Source,
    [Parameter(Mandatory)][string]$TopicId,
    [Parameter(Mandatory)][string]$Subject,
    [Parameter(Mandatory)][string]$CourseId,
    [string]$Unit,
    [string]$RunDirectory,
    [string]$DataDirectory = 'D:\AppData\MathLearningWeb',
    [switch]$PrepareOnly
)
$ErrorActionPreference = 'Stop'
$pythonPath = 'D:\AppData\MathLearningWeb\venv\Scripts\python.exe'
$operation = if ($PrepareOnly) { 'author-prepare' } else { 'author-import' }
$arguments = @('-m', 'math_learning', '--data-dir', $DataDirectory, $operation, '--source', $Source, '--topic-id', $TopicId)
if (-not $PrepareOnly) {
    $arguments += @('--subject', $Subject, '--course', $CourseId)
    if ($Unit) { $arguments += @('--unit', $Unit) }
}
if ($RunDirectory) { $arguments += @('--run-dir', $RunDirectory) }
Push-Location $PSScriptRoot
try { & $pythonPath @arguments; $resultCode = $LASTEXITCODE }
finally { Pop-Location }
exit $resultCode
