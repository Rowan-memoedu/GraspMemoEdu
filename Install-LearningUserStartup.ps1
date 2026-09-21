param([string]$DataDirectory = 'D:\AppData\MathLearningWeb', [int]$Port = 8767)
$ErrorActionPreference = 'Stop'
$script = Join-Path $PSScriptRoot 'Run-LearningMaintenance.ps1'
$pwsh = (Get-Command pwsh).Source
$arguments = '-NoLogo -NoProfile -WindowStyle Hidden -File "{0}" -DataDirectory "{1}" -Port {2}' -f $script,$DataDirectory,$Port
$startup = [Environment]::GetFolderPath('Startup')
$shortcutPath = Join-Path $startup 'GraspMemoEdu Maintenance.lnk'
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
if ((Test-Path -LiteralPath $shortcutPath) -and $shortcut.Arguments -notlike ('*' + $script + '*')) { throw '同名启动项不属于本项目，未覆盖。' }
$shortcut.TargetPath = $pwsh
$shortcut.Arguments = $arguments
$shortcut.WorkingDirectory = $PSScriptRoot
$shortcut.WindowStyle = 7
$shortcut.Description = 'GraspMemoEdu local service health maintenance'
$shortcut.Save()
Start-Process -FilePath $pwsh -ArgumentList $arguments -WindowStyle Hidden
Write-Output ('当前用户登录启动项已安装：' + $shortcutPath)
