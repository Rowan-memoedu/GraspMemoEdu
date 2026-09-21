param([string]$DataDirectory = 'D:\AppData\MathLearningWeb', [int]$Port = 8767)
$ErrorActionPreference = 'Stop'
$name = 'GraspMemoEdu-ServiceMaintenance'
$script = Join-Path $PSScriptRoot 'Watch-Learning.ps1'
$existing = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
if ($existing -and $existing.Actions.Arguments -notlike ('*' + $script + '*')) { throw '同名任务不属于本项目，未覆盖。' }
$pwsh = (Get-Command pwsh).Source
$arguments = '-NoLogo -NoProfile -WindowStyle Hidden -File "{0}" -DataDirectory "{1}" -Port {2}' -f $script,$DataDirectory,$Port
$action = New-ScheduledTaskAction -Execute $pwsh -Argument $arguments -WorkingDirectory $PSScriptRoot
$triggers = @((New-ScheduledTaskTrigger -AtLogOn), (New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 1)))
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit ([timespan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$principal = New-ScheduledTaskPrincipal -UserId ([Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $name -Action $action -Trigger $triggers -Settings $settings -Principal $principal -Description 'Keep the local learning backend and public endpoint healthy while the user session is available; never changes power settings.' -Force -ErrorAction Stop | Out-Null
Start-ScheduledTask -TaskName $name -ErrorAction Stop
Write-Output ('已启用维护任务：' + $name)
