param(
    [string]$IdeaHome = 'D:\Applications\IntelliJIDEA',
    [string]$AcademyLib = 'D:\AppData\JetBrains\IntelliJIDEA\plugins\JetBrainsAcademy\lib',
    [string]$Jdk = 'C:\Users\fy\.jdks\ms-25.0.4.1',
    [string]$Output = 'D:\Artifacts\GraspMemoEdu\academy-bridge\build'
)
$ErrorActionPreference = 'Stop'
$classes = Join-Path $Output 'classes'
New-Item -ItemType Directory -Force -Path $classes | Out-Null
$sources = Get-ChildItem -LiteralPath (Join-Path $PSScriptRoot 'src') -Filter '*.java' -Recurse | ForEach-Object FullName
& (Join-Path $Jdk 'bin\javac.exe') -encoding UTF-8 -classpath "$IdeaHome\lib\*;$AcademyLib\*" -d $classes $sources
if ($LASTEXITCODE -ne 0) { throw 'Plugin compilation failed' }
$lib = Join-Path $Output 'GraspMemoEduAcademy\lib'
New-Item -ItemType Directory -Force -Path $lib | Out-Null
& (Join-Path $Jdk 'bin\jar.exe') --create --file (Join-Path $lib 'graspmemoedu-academy.jar') -C $classes . -C (Join-Path $PSScriptRoot 'resources') .
if ($LASTEXITCODE -ne 0) { throw 'Plugin packaging failed' }
Write-Output (Join-Path $Output 'GraspMemoEduAcademy')
