param([string]$DataDirectory = 'D:\AppData\MathLearningWeb')
$ErrorActionPreference = 'Stop'
$pidFile = Join-Path $DataDirectory 'runtime\server.json'
if (-not (Test-Path -LiteralPath $pidFile -PathType Leaf)) { Write-Output '没有此启动入口管理的运行实例。'; exit 0 }
$record = Get-Content -LiteralPath $pidFile -Raw | ConvertFrom-Json
$process = Get-Process -Id $record.pid -ErrorAction SilentlyContinue
if (-not $process) { Write-Output '学习服务已停止。'; exit 0 }
$details = Get-CimInstance Win32_Process -Filter ('ProcessId={0}' -f $record.pid)
if ($process.StartTime.ToUniversalTime() -ne ([datetime]$record.startedAt).ToUniversalTime() -or $details.CommandLine -notmatch '-m\s+math_learning\s') {
    throw '进程身份已变化，拒绝停止其他应用。'
}
$allProcesses = Get-CimInstance Win32_Process
$ownedIds = [System.Collections.Generic.List[int]]::new()
$ownedIds.Add([int]$record.pid)
for ($index = 0; $index -lt $ownedIds.Count; $index++) {
    foreach ($child in $allProcesses | Where-Object ParentProcessId -EQ $ownedIds[$index]) {
        if (-not $ownedIds.Contains([int]$child.ProcessId)) { $ownedIds.Add([int]$child.ProcessId) }
    }
}
for ($index = $ownedIds.Count - 1; $index -ge 0; $index--) {
    Stop-Process -Id $ownedIds[$index] -ErrorAction SilentlyContinue
}
Write-Output '学习服务已停止，进度仍保留在本地。'
