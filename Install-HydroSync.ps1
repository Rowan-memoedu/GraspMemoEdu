#Requires -Version 7.0
param([int]$IntervalMinutes = 60, [string]$Config = 'D:\AppData\GraspMemoEduHydro\sync\config.json')
$ErrorActionPreference = 'Stop'
if ($IntervalMinutes -lt 15) { throw 'Synchronization interval must be at least 15 minutes.' }
if (-not (Test-Path -LiteralPath $Config)) { throw 'Complete the account binding before scheduling.' }
$taskName = 'SUSTech-CS109-OJ-Sync'
$script = Join-Path $PSScriptRoot 'Sync-Hydro.ps1'
$existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($existing -and $existing.Actions.Arguments -notlike ('*' + $script + '*')) { throw 'The existing task belongs to another application.' }
$pwsh = (Get-Command pwsh.exe -ErrorAction Stop).Source
$arguments = '-NoLogo -NoProfile -WindowStyle Hidden -File "{0}" -Config "{1}"' -f $script,$Config
$action = New-ScheduledTaskAction -Execute $pwsh -Argument $arguments -WorkingDirectory $PSScriptRoot
$user = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$triggers = @((New-ScheduledTaskTrigger -AtLogOn -User $user), (New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(2) -RepetitionInterval (New-TimeSpan -Minutes $IntervalMinutes)))
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 45) -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -RestartCount 2 -RestartInterval (New-TimeSpan -Minutes 5)
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $triggers -Settings $settings -Principal $principal -Description 'Read the CS109 OJ problem bank and this user submission/AC status, preserve learner source files, and synchronize the existing IDEA/GraspMemoEdu OJ catalog.' -Force | Out-Null
Write-Output ('Installed Windows scheduled task: ' + $taskName + '; every ' + $IntervalMinutes + ' minutes and at logon.')
