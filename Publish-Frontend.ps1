param(
    [string]$Repository = 'Rowan-memoedu/GraspMemoEdu-Pages',
    [string]$ExportDirectory = 'D:\Artifacts\GraspMemoEdu\pages-ready',
    [string]$EvidenceDirectory = 'D:\Artifacts\GraspMemoEdu\publication'
)
$ErrorActionPreference = 'Stop'
& python (Join-Path $PSScriptRoot 'scripts/version_web_assets.py')
if ($LASTEXITCODE -ne 0) { throw 'Static asset version check failed. Refresh asset URLs before export and publication.' }
if ($Repository -notmatch '^[A-Za-z0-9-]+/[A-Za-z0-9_.-]+$') { throw 'Invalid repository name.' }
$expected = @('.nojekyll','app.js','deployment.json','favicon.svg','index.html','portal.js','review.js','atomic.js','learning-cache.js','question-input.js','content-report.js','help-feedback.js','catalog-picker.js','portal.css','subjects.js','subjects.css','styles.css','i18n.js','reader-messages.js','portal-messages.js','static-messages.js','course-graph.js','course-graph.css')
$files = @(Get-ChildItem -LiteralPath $ExportDirectory -File -Force -Recurse)
$expected += @('training.js','training.css','training-messages.js','learning-ui.js')
$expected += @('math-presentation.js')
$expected += @('review-papers.js','review-paper-messages.js')
$expected += @('review-directory.js','task-tree.js','content-timer.js','self-assessment.js')
$expected += @('topic-content.js','topic-content.css','topic-callouts.css')
$expected += @('student-dashboard.js')
$vendor = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'scripts/topic-presentation-assets.json') -Raw | ConvertFrom-Json -AsHashtable
foreach ($name in $vendor.files.Keys) {
    if ($name -notmatch '^vendor/(mermaid/[A-Za-z0-9_.-]+\.js|document-backlinks\.mjs)$') { throw 'Invalid vendor asset path.' }
    $actual = (Get-FileHash -LiteralPath (Join-Path $PSScriptRoot ('web/' + $name)) -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actual -ne $vendor.files[$name]) { throw ('Vendor source differs from pinned build: ' + $name) }
}
$expected += @($vendor.files.Keys)
$chat = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'scripts/chat-assets.json') -Raw | ConvertFrom-Json -AsHashtable
foreach ($name in $chat.files.Keys) {
    if ($name -notmatch '^vendor/chat/(chat\.js(?:\.LEGAL\.txt)?|LICENSES\.txt|katex\.min\.css|fonts/KaTeX_[A-Za-z0-9-]+\.woff2)$') { throw 'Invalid chat asset path.' }
    $actual = (Get-FileHash -LiteralPath (Join-Path $PSScriptRoot ('web/' + $name)) -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actual -ne $chat.files[$name]) { throw ('Chat source differs from pinned build: ' + $name) }
}
$expected += @($chat.files.Keys)
$relativeFiles = @($files | ForEach-Object { [IO.Path]::GetRelativePath($ExportDirectory, $_.FullName).Replace('\','/') })
if (@(Compare-Object ($expected | Sort-Object) ($relativeFiles | Sort-Object)).Count -ne 0) { throw 'Unexpected files in static publication directory.' }
foreach ($name in $expected | Where-Object { $_ -notin @('.nojekyll', 'deployment.json') }) {
    $sourceHash = (Get-FileHash -LiteralPath (Join-Path $PSScriptRoot ('web\' + $name)) -Algorithm SHA256).Hash
    $exportHash = (Get-FileHash -LiteralPath (Join-Path $ExportDirectory $name) -Algorithm SHA256).Hash
    if ($sourceHash -ne $exportHash) { throw ('Static export is stale: ' + $name) }
}
$deployment = Get-Content -LiteralPath (Join-Path $ExportDirectory 'deployment.json') -Raw | ConvertFrom-Json -AsHashtable
if (@(Compare-Object @('api_origin') @($deployment.Keys)).Count -ne 0) { throw 'Deployment configuration must contain only api_origin.' }
$apiUri = $null
if (-not [uri]::TryCreate($deployment.api_origin, [UriKind]::Absolute, [ref]$apiUri) -or
    $apiUri.Scheme -ne 'https' -or $apiUri.UserInfo -or $apiUri.Query -or $apiUri.Fragment -or
    $deployment.api_origin -ne $apiUri.GetLeftPart([UriPartial]::Authority) -or
    $deployment.api_origin -match '[\s*\\]') { throw 'Publication requires one exact HTTPS API origin without credentials or a path.' }
New-Item -Path $EvidenceDirectory -ItemType Directory -Force | Out-Null
function Invoke-Api([string]$Method, [string]$Endpoint, $Body = $null) {
    $arguments = @('api','--method',$Method,$Endpoint,'-H','Accept: application/vnd.github+json','-H','X-GitHub-Api-Version: 2022-11-28')
    if ($null -ne $Body) {
        $requestPath = Join-Path $EvidenceDirectory 'request.json'
        $Body | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $requestPath -Encoding utf8
        $arguments += @('--input',$requestPath)
    }
    $raw = & gh @arguments
    if ($LASTEXITCODE -ne 0) { throw ('GitHub API failed: ' + $Method + ' ' + $Endpoint) }
    if ($raw) { return ($raw | ConvertFrom-Json) }
}
$owner,$name = $Repository.Split('/')
$login = (& gh api user --jq .login).Trim()
if ($LASTEXITCODE -ne 0 -or $owner -ne $login) { throw 'Publication is restricted to the authenticated owner.' }
$existing = & gh repo list $owner --limit 1000 --json nameWithOwner,isPrivate | ConvertFrom-Json
if ($LASTEXITCODE -ne 0) { throw 'Unable to inspect repository.' }
$repo = @($existing | Where-Object nameWithOwner -EQ $Repository)
$created = $repo.Count -eq 0
if ($created) {
    Write-Output 'Creating the authorized public frontend repository.'
    $info = Invoke-Api POST 'user/repos' @{name=$name;private=$false;auto_init=$true;description='GraspMemoEdu static frontend. Course data, answers and credentials remain local.'}
} else {
    if ($repo[0].isPrivate) { throw 'Refusing to change visibility of an existing private repository.' }
    # An intentional shutdown must not be undone by a routine frontend update.
    $null = & gh api ('repos/' + $Repository + '/pages') 2>$null
    if ($LASTEXITCODE -ne 0) { throw 'Pages is disabled or unavailable. Restore site availability before publishing.' }
    $info = Invoke-Api GET ('repos/' + $Repository)
}
$branch = $info.default_branch
$reference = Invoke-Api GET ('repos/' + $Repository + '/git/ref/heads/' + $branch)
$parent = $reference.object.sha
$commit = Invoke-Api GET ('repos/' + $Repository + '/git/commits/' + $parent)
$priorTree = Invoke-Api GET ('repos/' + $Repository + '/git/trees/' + $commit.tree.sha + '?recursive=1')
$allowedPrior = if ($created) { @('README.md') } else { $expected }
if ($priorTree.truncated -or @($priorTree.tree | Where-Object { ($_.type -eq 'blob' -and $_.path -notin $allowedPrior) -or ($_.type -eq 'tree' -and $_.path -notin @('vendor','vendor/mermaid','vendor/chat','vendor/chat/fonts')) -or $_.type -notin @('blob','tree') }).Count) {
    throw 'Existing repository contains files outside the frontend allowlist. Publication stopped.'
}
$treeItems = @($files | ForEach-Object {
    $entry = @{path=[IO.Path]::GetRelativePath($ExportDirectory, $_.FullName).Replace('\','/');mode='100644';type='blob'}
    if ($_.Extension -eq '.woff2') {
        $blob = Invoke-Api POST ('repos/' + $Repository + '/git/blobs') @{content=[Convert]::ToBase64String([IO.File]::ReadAllBytes($_.FullName));encoding='base64'}
        $entry.sha = $blob.sha
    } else { $entry.content = [IO.File]::ReadAllText($_.FullName) }
    $entry
})
$tree = Invoke-Api POST ('repos/' + $Repository + '/git/trees') @{tree=$treeItems}
$published = Invoke-Api POST ('repos/' + $Repository + '/git/commits') @{message='Publish verified learning frontend';tree=$tree.sha;parents=@($parent)}
$verifiedTree = Invoke-Api GET ('repos/' + $Repository + '/git/trees/' + $published.tree.sha + '?recursive=1')
if ($verifiedTree.truncated -or @($verifiedTree.tree | Where-Object { $_.type -notin @('blob','tree') }).Count -or
    @(Compare-Object ($expected | Sort-Object) (($verifiedTree.tree | Where-Object type -EQ 'blob').path | Sort-Object)).Count) {
    throw 'Remote publication tree failed the exact frontend allowlist.'
}
$null = Invoke-Api PATCH ('repos/' + $Repository + '/git/refs/heads/' + $branch) @{sha=$published.sha;force=$false}
Write-Output 'Frontend files are committed. Enabling GitHub Pages.'
$pageRaw = & gh api ('repos/' + $Repository + '/pages') 2>$null
$pageExit = $LASTEXITCODE
if ($pageExit -eq 0) {
    $pages = $pageRaw | ConvertFrom-Json
    $null = Invoke-Api PUT ('repos/' + $Repository + '/pages') @{source=@{branch=$branch;path='/'};build_type='legacy'}
} else {
    if (-not $created) { throw 'Pages became unavailable during publication. Refusing to recreate a disabled site.' }
    if (($pageRaw -join '') -notmatch '"status"\s*:\s*"404"|"message"\s*:\s*"Not Found"') { throw 'Unable to inspect Pages state.' }
    $pages = Invoke-Api POST ('repos/' + $Repository + '/pages') @{source=@{branch=$branch;path='/'};build_type='legacy'}
}
$receipt = @{repository=$Repository;visibility='public';branch=$branch;commit=$published.sha;url=$pages.html_url;api_origin=$deployment.api_origin;status='submitted';files=$expected;includes_course_data=$false}
$receipt | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $EvidenceDirectory 'deployment.json') -Encoding utf8
$receipt | ConvertTo-Json -Depth 5
