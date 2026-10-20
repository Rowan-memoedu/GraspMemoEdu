param(
    [string]$BuildDirectory = 'D:\Artifacts\GraspMemoEdu\academy-bridge\build\GraspMemoEduAcademy',
    [string]$PluginDirectory = 'D:\AppData\JetBrains\IntelliJIDEA\plugins\GraspMemoEduAcademy'
)
$ErrorActionPreference = 'Stop'
$source = Join-Path $BuildDirectory 'lib\graspmemoedu-academy.jar'
if (-not (Test-Path -LiteralPath $source -PathType Leaf)) { throw 'Build the plugin first.' }
if (Test-Path -LiteralPath $PluginDirectory) { throw 'Plugin directory already exists; inspect the installed version before updating.' }
New-Item -ItemType Directory -Path (Join-Path $PluginDirectory 'lib') | Out-Null
$target = Join-Path $PluginDirectory 'lib\graspmemoedu-academy.jar'
Copy-Item -LiteralPath $source -Destination $target
if ((Get-FileHash -LiteralPath $source).Hash -ne (Get-FileHash -LiteralPath $target).Hash) { throw 'Installed plugin hash mismatch' }
[pscustomobject]@{installed=$target;loaded=$false;restart_required=[bool](Get-Process idea64 -ErrorAction SilentlyContinue)} | ConvertTo-Json
