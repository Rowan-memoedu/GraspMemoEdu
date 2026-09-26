param([string]$DataDirectory = 'D:\AppData\GraspMemoEdu')
$ErrorActionPreference = 'Stop'
$pidFile = Join-Path $DataDirectory 'runtime\tunnel\tunnel.json'
if (-not (Test-Path -LiteralPath $pidFile)) { Write-Output '没有此入口管理的公网隧道。'; exit 0 }
$record = Get-Content -LiteralPath $pidFile -Raw | ConvertFrom-Json
$process = Get-Process -Id $record.pid -ErrorAction SilentlyContinue
if (-not $process) { Write-Output '公网隧道已经停止。'; exit 0 }
if ($process.Path -ne $record.executable -or $process.StartTime.ToUniversalTime() -ne ([datetime]$record.startedAt).ToUniversalTime()) { throw '进程身份不符，未停止任何进程。' }
Stop-Process -Id $process.Id
Write-Output '公网隧道已停止，本机学习服务和学习记录保留。'
