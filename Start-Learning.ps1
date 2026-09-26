param(
    [string]$DataDirectory = 'D:\AppData\GraspMemoEdu',
    [int]$Port = 8767
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'Assert-LocalLearningData.ps1')
Assert-LocalLearningData -DataDirectory $DataDirectory
$pythonPath = 'D:\AppData\GraspMemoEdu\venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) { throw '请先按 README 安装运行环境。' }
if (-not (Test-Path -LiteralPath (Join-Path $DataDirectory 'courses\digits-to-letters.json') -PathType Leaf)) { throw '课程尚未导入，请按 README 完成课程导入。' }
$runtimeDirectory = Join-Path $DataDirectory 'runtime'
New-Item -ItemType Directory -Force -Path $runtimeDirectory | Out-Null
$pidFile = Join-Path $runtimeDirectory 'server.json'
if (Test-Path -LiteralPath $pidFile) {
    $prior = Get-Content -LiteralPath $pidFile -Raw | ConvertFrom-Json
    $existing = Get-Process -Id $prior.pid -ErrorAction SilentlyContinue
    if ($existing -and $existing.StartTime.ToUniversalTime() -eq ([datetime]$prior.startedAt).ToUniversalTime()) {
        if ($prior.port -ne $Port) { throw '已有服务端口不同，请先停止已有服务。' }
        try {
            $health = Invoke-RestMethod -Uri ('http://127.0.0.1:{0}/api/health' -f $Port) -TimeoutSec 5
            if ($health.service -eq 'GraspMemoEdu' -and $health.instance_id -eq $prior.instanceId) {
                Write-Output ('学习网站已启动：http://127.0.0.1:{0}' -f $prior.port)
                exit 0
            }
        } catch { }
        & pwsh -NoLogo -NoProfile -File (Join-Path $PSScriptRoot 'Stop-Learning.ps1') -DataDirectory $DataDirectory
        if ($LASTEXITCODE -ne 0) { throw '不健康的本机服务停止失败。' }
    }
}
$instanceId = [guid]::NewGuid().ToString('N')
$argsForServer = @('-m', 'graspmemoedu', '--data-dir', ('"{0}"' -f $DataDirectory), 'serve', '--port', $Port, '--instance-id', $instanceId)
$process = Start-Process -FilePath $pythonPath -ArgumentList $argsForServer -WorkingDirectory $PSScriptRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $runtimeDirectory 'server-output.log') -RedirectStandardError (Join-Path $runtimeDirectory 'server-error.log')
$record = @{ pid = $process.Id; startedAt = $process.StartTime.ToUniversalTime().ToString('o'); port = $Port; dataDirectory = $DataDirectory; instanceId = $instanceId }
$record | ConvertTo-Json | Set-Content -LiteralPath $pidFile -Encoding utf8
$ready = $false
for ($probe = 0; $probe -lt 30; $probe++) {
    $process.Refresh()
    if ($process.HasExited) { throw ('服务启动失败，请查看 {0}' -f (Join-Path $runtimeDirectory 'server-error.log')) }
    try {
        $response = Invoke-RestMethod -Uri ('http://127.0.0.1:{0}/api/health' -f $Port) -TimeoutSec 2
        if ($response.service -eq 'GraspMemoEdu' -and $response.instance_id -eq $instanceId) { $ready = $true; break }
    } catch { Start-Sleep -Milliseconds 300 }
}
if (-not $ready) { throw '服务仍未就绪，请检查本地运行日志。' }
Write-Output ('学习网站：http://127.0.0.1:{0}' -f $Port)
Write-Output ('学习记录：{0}' -f $DataDirectory)
