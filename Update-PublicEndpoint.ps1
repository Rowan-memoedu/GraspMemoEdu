param(
    [Parameter(Mandatory)][string]$Origin,
    [string]$Repository = 'amymiller76717-sudo/GraspMemoEdu-Pages'
)
$ErrorActionPreference = 'Stop'
if ($Origin -notmatch '^https://[a-z0-9-]+\.trycloudflare\.com$') { throw '公网地址格式不正确。' }
$pageUrl = 'https://{0}.github.io/{1}/deployment.json' -f $Repository.Split('/')[0], $Repository.Split('/')[1]
try {
    $live = Invoke-RestMethod -Uri ($pageUrl + '?health=' + [datetime]::UtcNow.Ticks) -TimeoutSec 10
    if ($live.api_origin -eq $Origin) { Write-Output '公开网页地址已同步。'; exit 0 }
} catch { }
$raw = & gh api ('repos/{0}/contents/deployment.json' -f $Repository)
if ($LASTEXITCODE -ne 0) { throw '读取已发布配置失败。' }
$file = $raw | ConvertFrom-Json
$current = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($file.content)) | ConvertFrom-Json
if ($current.api_origin -ne $Origin) {
    $content = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes((@{api_origin=$Origin} | ConvertTo-Json)))
    @{message='Restore public API endpoint';sha=$file.sha;content=$content} | ConvertTo-Json | & gh api --method PUT ('repos/{0}/contents/deployment.json' -f $Repository) --input - --silent
    if ($LASTEXITCODE -ne 0) { throw '更新已发布配置失败。' }
}
for ($attempt=0; $attempt -lt 45; $attempt++) {
    try {
        $live = Invoke-RestMethod -Uri ($pageUrl + '?health=' + [datetime]::UtcNow.Ticks) -TimeoutSec 10
        if ($live.api_origin -eq $Origin) { Write-Output '公开网页地址已同步。'; exit 0 }
    } catch { }
    Start-Sleep -Seconds 4
}
throw 'GitHub Pages 配置仍在更新，维护任务稍后重试。'
