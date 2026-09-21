param([string]$DataDirectory = 'D:\AppData\MathLearningWeb', [int]$Port = 8767, [switch]$Once)
$ErrorActionPreference = 'Stop'
$directory = Join-Path $DataDirectory 'runtime\maintenance'
New-Item -ItemType Directory -Path $directory -Force | Out-Null
try { $lock = [IO.File]::Open((Join-Path $directory 'watch.lock'), 'OpenOrCreate', 'ReadWrite', 'None') } catch { exit 0 }
$failures = 0
try {
    do {
        $status = @{checked_at=[datetime]::UtcNow.ToString('o');pid=$PID;state='checking'}
        $outputLog = Join-Path $directory 'latest-repair.log'
        try {
            # Start-Learning verifies the owned instance, not only its PID.
            & pwsh -NoLogo -NoProfile -File (Join-Path $PSScriptRoot 'Start-Learning.ps1') -DataDirectory $DataDirectory -Port $Port *> $outputLog
            if ($LASTEXITCODE -ne 0) { throw '本机服务恢复失败。' }
            & pwsh -NoLogo -NoProfile -File (Join-Path $PSScriptRoot 'Start-PublicLearning.ps1') -DataDirectory $DataDirectory -Port $Port -RepairOnly *>> $outputLog
            if ($LASTEXITCODE -ne 0) { throw '公网恢复尚未成功，将自动重试。' }
            $failures = 0
            $status.state = 'healthy'
        } catch {
            $failures++
            $status.state = 'retrying'
            $status.error = $_.Exception.Message
        }
        $delay = [int][Math]::Min(300, 30 * [Math]::Pow(2, [Math]::Min($failures, 4)))
        $status.next_check_seconds = $delay
        $status.completed_at = [datetime]::UtcNow.ToString('o')
        $status | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $directory 'status.json') -Encoding utf8
        $log = Join-Path $directory 'events.jsonl'
        if ((Test-Path $log) -and (Get-Item $log).Length -gt 5MB) { Move-Item -LiteralPath $log -Destination ($log + '.previous') -Force }
        $status | ConvertTo-Json -Compress | Add-Content -LiteralPath $log -Encoding utf8
        if (-not $Once) { Start-Sleep -Seconds $delay }
    } while (-not $Once)
} finally { $lock.Dispose() }
