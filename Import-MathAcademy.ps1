[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$TopicsZip,
    [Parameter(Mandatory=$true)][string]$GraphsZip,
    [string[]]$CourseIds = @('82','74','66','72','140'),
    [ValidateSet('local','production')][string]$Target = 'local',
    [string]$DataDir,
    [string]$OutputDirectory = 'D:/Artifacts/GraspMemoEdu/mathacademy-course-import/prepared',
    [string]$CacheDirectory = 'D:/Caches/GraspMemoEdu/mathacademy',
    [switch]$PlanOnly
)
$ErrorActionPreference = 'Stop'
if ($Target -eq 'local' -and -not $DataDir -and -not $PlanOnly) { throw 'Target local requires an explicit isolated DataDir.' }
$python = 'D:/AppData/GraspMemoEdu/venv/Scripts/python.exe'
$arguments = @('-m','graspmemoedu.mathacademy_import','--topics-zip',$TopicsZip,'--graphs-zip',$GraphsZip,
    '--target',$Target,'--output',$OutputDirectory,'--cache',$CacheDirectory)
if ($DataDir) { $arguments += @('--data-dir',$DataDir) }
if ($PlanOnly) { $arguments += '--plan-only' }
$arguments += '--course-ids'
foreach ($value in $CourseIds) { $arguments += $value.Split(',') }
Push-Location $PSScriptRoot
try { & $python @arguments; $result = $LASTEXITCODE } finally { Pop-Location }
exit $result
