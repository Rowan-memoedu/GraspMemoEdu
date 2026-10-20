param(
    [string]$BuildDirectory = 'D:\Artifacts\GraspMemoEdu\academy-bridge\build\GraspMemoEduAcademy',
    [string]$PluginDirectory = 'D:\AppData\JetBrains\IntelliJIDEA\plugins\GraspMemoEduAcademy',
    [string]$IdeaExecutable = 'D:\Applications\IntelliJIDEA\bin\idea64.exe',
    [string]$ExpectedInstalledSha256
)
$ErrorActionPreference = 'Stop'
$source = Join-Path $BuildDirectory 'lib\graspmemoedu-academy.jar'
if (-not (Test-Path -LiteralPath $source -PathType Leaf)) { throw 'Build the plugin first.' }
$target = Join-Path $PluginDirectory 'lib\graspmemoedu-academy.jar'
if ((Test-Path -LiteralPath $PluginDirectory) -and (
    -not $ExpectedInstalledSha256 -or -not (Test-Path -LiteralPath $target) -or
    (Get-FileHash -LiteralPath $target).Hash -ne $ExpectedInstalledSha256)) {
    throw 'Installed plugin differs; inspect its version before updating.'
}
if (-not (Test-Path -LiteralPath $IdeaExecutable -PathType Leaf)) { throw 'IDEA executable is missing.' }
$scheme = 'HKCU:\Software\Classes\graspmemoedu'
$command = '"' + $IdeaExecutable + '" "%1"'
if (Test-Path -LiteralPath $scheme) {
    $oldCommand = (Get-Item -LiteralPath "$scheme\shell\open\command" -ErrorAction Stop).GetValue('')
    if ($oldCommand -ne $command) { throw 'The GraspMemoEdu URL scheme belongs to another application.' }
}
New-Item -ItemType Directory -Force -Path (Join-Path $PluginDirectory 'lib') | Out-Null
$binaryChanged = -not (Test-Path -LiteralPath $target) -or
    (Get-FileHash -LiteralPath $source).Hash -ne (Get-FileHash -LiteralPath $target).Hash
if ($binaryChanged) {
    Copy-Item -LiteralPath $source -Destination $target
}
if ((Get-FileHash -LiteralPath $source).Hash -ne (Get-FileHash -LiteralPath $target).Hash) { throw 'Installed plugin hash mismatch' }
New-Item -Path "$scheme\shell\open\command" -Force | Out-Null
Set-Item -LiteralPath $scheme -Value 'URL:GraspMemoEdu Academy'
New-ItemProperty -LiteralPath $scheme -Name 'URL Protocol' -Value '' -PropertyType String -Force | Out-Null
Set-Item -LiteralPath "$scheme\shell\open\command" -Value $command
if (-not ('GraspMemoEduAssociationRefresh' -as [type])) {
    Add-Type -TypeDefinition @'
using System;
using System.Text;
using System.Runtime.InteropServices;
public static class GraspMemoEduAssociationRefresh {
    [DllImport("shell32.dll")]
    public static extern void SHChangeNotify(uint eventId, uint flags, IntPtr first, IntPtr second);
    [DllImport("Shlwapi.dll", CharSet=CharSet.Unicode)]
    public static extern uint AssocQueryString(uint flags, uint str, string assoc, string extra, StringBuilder output, ref uint size);
}
'@
}
[GraspMemoEduAssociationRefresh]::SHChangeNotify(0x08000000, 0, [IntPtr]::Zero, [IntPtr]::Zero)
$resolvedCommand = [System.Text.StringBuilder]::new(2048)
[uint32]$resolvedSize = 2048
$associationResult = [GraspMemoEduAssociationRefresh]::AssocQueryString(0x1000, 1, 'graspmemoedu', 'open', $resolvedCommand, [ref]$resolvedSize)
if ($associationResult -ne 0 -or $resolvedCommand.ToString() -ne $command) {
    throw 'Windows has not resolved the registered GraspMemoEdu handler; do not launch links yet.'
}
[pscustomobject]@{installed=$target;binary_changed=$binaryChanged;protocol_verified=$true;
    restart_required=($binaryChanged -and [bool](Get-Process idea64 -ErrorAction SilentlyContinue))} | ConvertTo-Json
