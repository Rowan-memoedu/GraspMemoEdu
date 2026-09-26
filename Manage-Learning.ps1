param(
    [Parameter(ValueFromRemainingArguments=$true)]
    [string[]]$Arguments
)
$ErrorActionPreference = 'Stop'
if ((Test-Path -LiteralPath 'D:\AppData\GraspMemoEdu\cloud-authoritative.json') -and '--data-dir' -notin $Arguments) {
    throw '正式数据已迁移腾讯云。请在服务器 /opt/graspmemoedu/app 使用 CLI；本地隔离操作必须显式指定 --data-dir。详见 docs/tencent-deployment.md。'
}
Push-Location $PSScriptRoot
try {
    & 'D:\AppData\GraspMemoEdu\venv\Scripts\python.exe' -m graspmemoedu @Arguments
    exit $LASTEXITCODE
} finally { Pop-Location }
