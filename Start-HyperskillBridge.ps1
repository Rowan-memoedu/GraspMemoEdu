param([string]$ConfigPath = 'D:\AppData\GraspMemoEduHyperskillBridge\config.json')
$ErrorActionPreference = 'Stop'
$config = Get-Content -LiteralPath $ConfigPath -Raw | ConvertFrom-Json
if ($config.port -ne 18769) { throw 'Unexpected local bridge port.' }
if (Get-NetTCPConnection -State Listen -LocalPort $config.port -ErrorAction SilentlyContinue) {
    $health = Invoke-RestMethod -Uri 'http://127.0.0.1:18769/health' -Method Post -ContentType 'application/json' -Body '{}' -Headers @{Origin=$config.origins[0];'X-Grasp-Local'='1'}
    if ($health.ready -and $health.provider -eq 'hyperskill') { Write-Output 'Hyperskill bridge already ready'; exit 0 }
    throw 'The local port is used by another application.'
}
$node = (Get-Command node -CommandType Application | Select-Object -First 1).Source
$script = Join-Path $PSScriptRoot 'scripts/hyperskill_bridge.mjs'
$runtime = Split-Path -Parent $ConfigPath
$process = Start-Process -FilePath $node -ArgumentList @(('"' + $script + '"'),('"' + $ConfigPath + '"')) -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput (Join-Path $runtime 'bridge.log') -RedirectStandardError (Join-Path $runtime 'bridge-error.log')
$process.Id | Set-Content -LiteralPath (Join-Path $runtime 'bridge.pid')
Write-Output ('Hyperskill bridge starting; process ' + $process.Id)
