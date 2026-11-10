#Requires -Version 7.0
param([string]$Config = 'D:\AppData\GraspMemoEduHydro\sync\config.json')
$ErrorActionPreference = 'Stop'
$OutputEncoding = [Text.UTF8Encoding]::new($false)
$runtime = 'D:\AppData\GraspMemoEdu\venv\Scripts\python.exe'
$script = Join-Path $PSScriptRoot 'scripts\sync_hydro.py'
& $runtime $script --config $Config
$syncExit = $LASTEXITCODE
exit $syncExit
