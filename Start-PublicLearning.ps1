param(
    [string]$DataDirectory = 'D:\AppData\MathLearningWeb',
    [int]$Port = 8767,
    [string]$CloudflaredPath = 'D:\Applications\cloudflared\cloudflared.exe',
    [string]$Repository = 'amymiller76717-sudo/MathLearningWeb-Pages',
    [string]$ArtifactDirectory = 'D:\Artifacts\MathLearningWeb\public-internet',
    [switch]$SkipPublish,
    [switch]$RestartBackend
)
$ErrorActionPreference = 'Stop'
function Read-SharedLog([string]$Path) {
    $stream = [IO.File]::Open($Path, 'Open', 'Read', 'ReadWrite')
    $reader = [IO.StreamReader]::new($stream)
    try { return $reader.ReadToEnd() } finally { $reader.Dispose() }
}
$pythonPath = 'D:\AppData\MathLearningWeb\venv\Scripts\python.exe'
$CloudflaredPath = (Resolve-Path -LiteralPath $CloudflaredPath).Path
$settingsPath = Join-Path $DataDirectory 'settings.json'
if (-not (Test-Path -LiteralPath $settingsPath -PathType Leaf)) { throw '请先完成本机学习服务配置。' }
$runtimeDirectory = Join-Path $DataDirectory 'runtime\tunnel'
New-Item -ItemType Directory -Force -Path $runtimeDirectory | Out-Null
$launchLock = [IO.File]::Open((Join-Path $runtimeDirectory 'launch.lock'), 'OpenOrCreate', 'ReadWrite', 'None')
try {
    $pidFile = Join-Path $runtimeDirectory 'tunnel.json'
    $record = $null
    if (Test-Path -LiteralPath $pidFile) {
        $candidate = Get-Content -LiteralPath $pidFile -Raw | ConvertFrom-Json
        $priorProcess = Get-Process -Id $candidate.pid -ErrorAction SilentlyContinue
        if ($priorProcess -and $priorProcess.Path -eq $CloudflaredPath -and
            $priorProcess.StartTime.ToUniversalTime() -eq ([datetime]$candidate.startedAt).ToUniversalTime()) {
            if ($candidate.port -ne $Port) { throw '已有公开入口使用另一个端口，请先运行 Stop-PublicLearning.ps1。' }
            $record = $candidate
            if (Test-Path -LiteralPath $candidate.stderr -PathType Leaf) {
                $priorLog = Read-SharedLog $candidate.stderr
                $lostAt = $priorLog.LastIndexOf('Unauthorized: Tunnel not found', [StringComparison]::Ordinal)
                $connectedAt = $priorLog.LastIndexOf('Registered tunnel connection', [StringComparison]::Ordinal)
                if ($lostAt -ge 0 -and $lostAt -gt $connectedAt) {
                    Write-Output '旧临时隧道已被服务商移除，正在重建公网入口……'
                    & pwsh -NoLogo -NoProfile -File (Join-Path $PSScriptRoot 'Stop-PublicLearning.ps1') -DataDirectory $DataDirectory
                    if ($LASTEXITCODE -ne 0) { throw '失效隧道停止失败。' }
                    $record = $null
                }
            }
        }
    }
    if (-not $record) {
        $runDirectory = Join-Path $runtimeDirectory (Get-Date -Format 'yyyyMMdd-HHmmss-fff')
        New-Item -ItemType Directory -Path $runDirectory | Out-Null
        $stdout = Join-Path $runDirectory 'output.log'
        $stderr = Join-Path $runDirectory 'error.log'
        $arguments = @('tunnel', '--no-autoupdate', '--protocol', 'http2', '--metrics', '127.0.0.1:0',
            '--url', ('http://127.0.0.1:{0}' -f $Port), '--loglevel', 'info')
        # Run this launcher directly: test timeout Job objects terminate detached children.
        $tunnelProcess = Start-Process -FilePath $CloudflaredPath -ArgumentList $arguments -WorkingDirectory $runtimeDirectory -WindowStyle Hidden -PassThru -RedirectStandardOutput $stdout -RedirectStandardError $stderr
        $record = [pscustomobject]@{pid=$tunnelProcess.Id; startedAt=$tunnelProcess.StartTime.ToUniversalTime().ToString('o');
            executable=$CloudflaredPath; port=$Port; origin=$null; stdout=$stdout; stderr=$stderr}
        $record | ConvertTo-Json | Set-Content -LiteralPath $pidFile -Encoding utf8
        Write-Output '正在建立免费公网入口……'
    }
    if (-not $record.origin) {
        for ($attempt = 0; $attempt -lt 90; $attempt++) {
            $process = Get-Process -Id $record.pid -ErrorAction SilentlyContinue
            if (-not $process -or $process.StartTime.ToUniversalTime() -ne ([datetime]$record.startedAt).ToUniversalTime()) { throw ('公网入口启动失败，请检查：' + $record.stderr) }
            $log = (Read-SharedLog $record.stderr) + (Read-SharedLog $record.stdout)
            $match = [regex]::Match($log, 'https://[a-z0-9-]+\.trycloudflare\.com')
            if ($match.Success) {
                $record.origin = $match.Value
                $record | ConvertTo-Json | Set-Content -LiteralPath $pidFile -Encoding utf8
                break
            }
            Start-Sleep -Seconds 1
        }
    }
    if (-not $record.origin) { throw ('尚未取得公网入口，请检查：' + $record.stderr) }
    $settings = Get-Content -LiteralPath $settingsPath -Raw | ConvertFrom-Json -AsHashtable
    $originChanged = $settings.public_api_origin -ne $record.origin -or $settings.port -ne $Port
    if ($originChanged) {
        $settings.public_api_origin = $record.origin
        $settings.port = $Port
        $settings.allowed_origins = @(@($settings.allowed_origins) + 'https://amymiller76717-sudo.github.io' | Where-Object { $_ } | Sort-Object -Unique)
        $settings | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath ($settingsPath + '.pending') -Encoding utf8NoBOM
        Move-Item -LiteralPath ($settingsPath + '.pending') -Destination $settingsPath -Force
    }
    if ($originChanged -or $RestartBackend) {
        & pwsh -NoLogo -NoProfile -File (Join-Path $PSScriptRoot 'Stop-Learning.ps1') -DataDirectory $DataDirectory
        if ($LASTEXITCODE -ne 0) { throw '本机服务停止失败。' }
    }
    & pwsh -NoLogo -NoProfile -File (Join-Path $PSScriptRoot 'Start-Learning.ps1') -DataDirectory $DataDirectory -Port $Port
    if ($LASTEXITCODE -ne 0) { throw '本机服务启动失败。' }
    $ready = $false
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        try {
            $health = Invoke-RestMethod -Uri ($record.origin + '/api/health') -TimeoutSec 8
            if ($health.service -eq 'MathLearningWeb' -and -not $health.instance_id) { $ready = $true; break }
        } catch { }
        Start-Sleep -Seconds 2
    }
    if (-not $ready) { throw ('公网健康检查未通过；服务与隧道仍保留运行，可检查日志后重试：' + $record.stderr) }
    Write-Output ('公网后端已就绪：' + $record.origin)
    if (-not $SkipPublish) {
        $releaseDirectory = Join-Path $ArtifactDirectory ('release-' + (Get-Date -Format 'yyyyMMdd-HHmmss-fff'))
        $exportDirectory = Join-Path $releaseDirectory 'web'
        $evidenceDirectory = Join-Path $releaseDirectory 'publication'
        Push-Location $PSScriptRoot
        try {
            & $pythonPath -m math_learning --data-dir $DataDirectory export-web --output $exportDirectory --api-origin $record.origin
            if ($LASTEXITCODE -ne 0) { throw '公开网页导出失败。' }
            & pwsh -NoLogo -NoProfile -File (Join-Path $PSScriptRoot 'Publish-Frontend.ps1') -Repository $Repository -ExportDirectory $exportDirectory -EvidenceDirectory $evidenceDirectory
            if ($LASTEXITCODE -ne 0) { throw '公开网页发布失败；公网后端仍在运行，可重试发布。' }
        } finally { Pop-Location }
        $receiptPath = Join-Path $evidenceDirectory 'deployment.json'
        $receipt = Get-Content -LiteralPath $receiptPath -Raw | ConvertFrom-Json -AsHashtable
        Write-Output '正在等待公开网页更新……'
        $published = $false
        for ($attempt = 0; $attempt -lt 40; $attempt++) {
            try {
                $allMatch = $true
                foreach ($asset in @('deployment.json', 'app.js', 'index.html', 'styles.css', 'portal.js', 'review.js', 'portal.css', 'subjects.js', 'subjects.css', 'favicon.svg', 'i18n.js', 'reader-messages.js', 'portal-messages.js', 'static-messages.js')) {
                    $response = Invoke-WebRequest -Uri ($receipt.url + $asset + '?release=' + $receipt.commit) -TimeoutSec 10
                    $remoteBytes = if ($response.Content -is [byte[]]) { $response.Content } else { [Text.Encoding]::UTF8.GetBytes($response.Content) }
                    $remoteHash = [Convert]::ToHexString([Security.Cryptography.SHA256]::HashData($remoteBytes))
                    $localHash = (Get-FileHash -LiteralPath (Join-Path $exportDirectory $asset) -Algorithm SHA256).Hash
                    if ($remoteHash -ne $localHash) { $allMatch = $false; break }
                }
                if ($allMatch) { $published = $true; break }
            } catch { }
            Start-Sleep -Seconds 4
        }
        if (-not $published) { throw ('发布已提交，网页缓存尚未完成更新；请稍后重试此启动入口。发布记录：' + $receiptPath) }
        $receipt.status = 'verified'
        $receipt.verified_at = [datetime]::UtcNow.ToString('o')
        $receipt | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $receiptPath -Encoding utf8
        Write-Output ('学习网页：' + $receipt.url)
        Write-Output ('发布验证：' + $receiptPath)
    }
    Write-Output '请保持此电脑联网、服务运行。临时隧道重启后，重新运行此入口会自动更新公开网页。'
} finally { $launchLock.Dispose() }
