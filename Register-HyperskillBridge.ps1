param(
    [string]$ConfigPath = 'D:\AppData\GraspMemoEduHyperskillBridge\config.json',
    [switch]$Preview,
    [switch]$NoStart
)
$ErrorActionPreference = 'Stop'
$ConfigPath = (Resolve-Path -LiteralPath $ConfigPath).Path
$config = Get-Content -LiteralPath $ConfigPath -Raw | ConvertFrom-Json
if ($config.port -ne 18769) { throw 'Unexpected local bridge port.' }
$taskName = 'GraspMemoEdu-HyperskillBridge'
$pythonw = 'D:\AppData\GraspMemoEdu\venv\Scripts\pythonw.exe'
$hiddenRunner = 'D:\Projects\DeveloperGovernance\run-hidden.pyw'
$starter = Join-Path $PSScriptRoot 'Start-HyperskillBridge.ps1'
$pwsh = (Get-Command pwsh -CommandType Application | Select-Object -First 1).Source
foreach ($file in @($pythonw, $hiddenRunner, $starter, $pwsh)) {
    if (-not (Test-Path -LiteralPath $file -PathType Leaf)) { throw ('Missing task runtime: ' + $file) }
    if ($file.Contains('"')) { throw 'Task runtime path contains an invalid quote.' }
}
if ($ConfigPath.Contains('"')) { throw 'Configuration path contains an invalid quote.' }
$arguments = '"' + $hiddenRunner + '" --timeout 90 --keep-children-on-success -- "' + $pwsh + '" -NoLogo -NoProfile -NonInteractive -WindowStyle Hidden -File "' + $starter + '" -ConfigPath "' + $ConfigPath + '"'
$account = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$action = New-ScheduledTaskAction -Execute $pythonw -Argument $arguments -WorkingDirectory $PSScriptRoot
$triggers = @(
    (New-ScheduledTaskTrigger -AtLogOn -User $account),
    (New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 1))
)
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
    -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 2)
$principal = New-ScheduledTaskPrincipal -UserId $account -LogonType Interactive -RunLevel Limited
$definition = New-ScheduledTask -Action $action -Trigger $triggers -Settings $settings -Principal $principal `
    -Description 'Keep the local Hyperskill bridge available after sign-in and recover a stopped bridge without opening a console.'
if ($Preview) {
    @{task_name=$taskName;action=@{execute=$pythonw;arguments=$arguments};logon_trigger=$true;
      interval=$triggers[1].Repetition.Interval;duration=$triggers[1].Repetition.Duration;
      logon_type=$principal.LogonType;multiple_instances=$settings.MultipleInstances;
      battery_allowed=(-not $settings.DisallowStartIfOnBatteries);stop_on_battery=$settings.StopIfGoingOnBatteries;
      execution_time_limit=$settings.ExecutionTimeLimit} | ConvertTo-Json -Depth 4
    return
}
$existing = Get-ScheduledTask -TaskName $taskName -TaskPath '\' -ErrorAction SilentlyContinue
if ($existing -and ($existing.Actions.Count -ne 1 -or -not $existing.Actions[0].Arguments.Contains($starter))) {
    throw 'An unrelated task uses the requested name; it was not changed.'
}
Register-ScheduledTask -TaskName $taskName -TaskPath '\' -InputObject $definition -Force | Out-Null
if (-not $NoStart) { Start-ScheduledTask -TaskName $taskName -TaskPath '\' }
Write-Output 'Hyperskill bridge task registered: current-user sign-in and one-minute recovery checks.'
