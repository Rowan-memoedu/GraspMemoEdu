param([string]$ConfigPath = 'D:\AppData\GraspMemoEduHyperskillBridge\config.json')
$ErrorActionPreference = 'Stop'
$ConfigPath = (Resolve-Path -LiteralPath $ConfigPath).Path
$config = Get-Content -LiteralPath $ConfigPath -Raw | ConvertFrom-Json
if ($config.port -ne 18769) { throw 'Unexpected local bridge port.' }
$node = (Get-Command node -CommandType Application | Select-Object -First 1).Source
$script = Join-Path $PSScriptRoot 'scripts/hyperskill_bridge.mjs'
$runtime = Split-Path -Parent $ConfigPath
function Read-BridgeHealth {
    try {
        return Invoke-RestMethod -Uri 'http://127.0.0.1:18769/health' -Method Post -ContentType 'application/json' -Body '{}' `
            -Headers @{Origin=$config.origins[0];'X-Grasp-Local'='1'} -TimeoutSec 3
    } catch { return $null }
}
function Confirm-BridgeIdentity($health) {
    if (-not $health.ready -or $health.provider -ne 'hyperskill' -or $health.instance_id -notmatch '^[a-f0-9]{32}$' -or -not $health.pid) {
        throw 'The local port does not expose the expected bridge instance.'
    }
    $running = Get-CimInstance Win32_Process -Filter ('ProcessId=' + [int]$health.pid)
    if (-not $running -or $running.ExecutablePath -ne $node -or -not $running.CommandLine.Contains($script) -or -not $running.CommandLine.Contains($ConfigPath)) {
        throw 'The bridge process identity does not match the configured service.'
    }
    $instance = @{pid=[int]$health.pid;started_at=$running.CreationDate.ToUniversalTime().ToString('o');instance_id=$health.instance_id}
    $instance | ConvertTo-Json -Compress | Set-Content -LiteralPath (Join-Path $runtime 'bridge-instance.json') -Encoding utf8
    $instance.pid | Set-Content -LiteralPath (Join-Path $runtime 'bridge.pid')
}
$mutex = [Threading.Mutex]::new($false, 'Local\GraspMemoEdu-HyperskillBridge-Startup')
$owned = $false
try {
    try { $owned = $mutex.WaitOne(0) } catch [Threading.AbandonedMutexException] { $owned = $true }
    if (-not $owned) { Write-Output 'Hyperskill bridge startup is already running'; return }
    $health = Read-BridgeHealth
    if ($health) { Confirm-BridgeIdentity $health; Write-Output 'Hyperskill bridge already ready'; return }
    if (Get-NetTCPConnection -State Listen -LocalPort $config.port -ErrorAction SilentlyContinue) {
        throw 'The local port is occupied but its bridge health check failed; no process was stopped.'
    }
    $process = Start-Process -FilePath $node -ArgumentList @(('"' + $script + '"'),('"' + $ConfigPath + '"')) -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput (Join-Path $runtime 'bridge.log') -RedirectStandardError (Join-Path $runtime 'bridge-error.log')
    $deadline = [Diagnostics.Stopwatch]::StartNew()
    do {
        if ($process.HasExited) { throw 'Hyperskill bridge exited during startup; check bridge-error.log.' }
        $health = Read-BridgeHealth
        if ($health) {
            if ($health.pid -ne $process.Id) { throw 'Another process answered the bridge health check.' }
            Confirm-BridgeIdentity $health
            Write-Output ('Hyperskill bridge ready; process ' + $process.Id)
            return
        }
        Start-Sleep -Milliseconds 500
    } while ($deadline.Elapsed.TotalSeconds -lt 65)
    throw 'Hyperskill bridge did not become ready within the startup window.'
} finally {
    if ($owned) { $mutex.ReleaseMutex() }
    $mutex.Dispose()
}
