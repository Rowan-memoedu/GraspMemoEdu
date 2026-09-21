param([string]$DataDirectory = 'D:\AppData\MathLearningWeb', [int]$Port = 8767)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'Assert-LocalLearningData.ps1')
Assert-LocalLearningData -DataDirectory $DataDirectory
$mutex = [Threading.Mutex]::new($false, 'Local\GraspMemoEduMaintenance')
try {
    if (-not $mutex.WaitOne(0)) { exit 0 }
    try {
        while ($true) {
            & pwsh -NoLogo -NoProfile -File (Join-Path $PSScriptRoot 'Watch-Learning.ps1') -DataDirectory $DataDirectory -Port $Port
            Start-Sleep -Seconds 15
        }
    } finally { $mutex.ReleaseMutex() }
} finally { $mutex.Dispose() }
