param(
    [string]$Repository = 'memoedu/GraspMemoEdu-Pages',
    [string]$ExportDirectory = 'D:\Artifacts\GraspMemoEdu\pages-ready',
    [string]$EvidenceDirectory = 'D:\Artifacts\GraspMemoEdu\publication',
    [string]$ExpectedParent = ''
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
$expected += @('review-directory.js','task-tree.js','catalog-outline.js','content-timer.js','self-assessment.js')
$expected += @('catalog-workspace.js')
$expected += @('memory-chunk.js')
$expected += @('academy-messages.js')
$expected += @('hyperskill.js')
$expected += @('topic-content.js','topic-content.css','topic-callouts.css')
$expected += @('student-dashboard.js','presence.js')
$expected += @('composite-input.js')
$expected += @('restore.html','migration-source.html','origin-transfer.js')
$expected += @('response-cache.js')
$expected += @('point-assessment.js','context-library.js','context-library.css','context-library-messages.js','training-audio.js')
$expected += @('download.html','install.css','pwa.css','pwa.js','sw.js','manifest.webmanifest','icon-180.png','icon-192.png','icon-512.png','icon-maskable-512.png')
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
$bookFont = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'scripts/book-font-assets.json') -Raw | ConvertFrom-Json -AsHashtable
foreach ($name in $bookFont.files.Keys) {
    if ($name -notmatch '^vendor/book-font/([a-f0-9]{16}\.woff2|font\.css|LICENSE|sources\.json)$') { throw 'Invalid book font asset path.' }
    $actual = (Get-FileHash -LiteralPath (Join-Path $PSScriptRoot ('web/' + $name)) -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actual -ne $bookFont.files[$name]) { throw ('Book font differs from pinned source: ' + $name) }
}
$expected += @($bookFont.files.Keys)
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
if ($LASTEXITCODE -ne 0 -or -not $login) { throw 'An authenticated GitHub identity is required.' }
$existing = & gh repo list $owner --limit 1000 --json nameWithOwner,isPrivate | ConvertFrom-Json
if ($LASTEXITCODE -ne 0) { throw 'Unable to inspect repository.' }
$repo = @($existing | Where-Object nameWithOwner -EQ $Repository)
$created = $repo.Count -eq 0
if ($created) {
    Write-Output 'Creating the authorized public frontend repository.'
    $createEndpoint = if ($owner -eq $login) { 'user/repos' } else { 'orgs/' + $owner + '/repos' }
    $info = Invoke-Api POST $createEndpoint @{name=$name;private=$false;auto_init=$true;description='GraspMemoEdu static frontend. Course data, answers and credentials remain local.'}
} else {
    if ($repo[0].isPrivate) { throw 'Refusing to change visibility of an existing private repository.' }
    # An intentional shutdown must not be undone by a routine frontend update.
    $null = & gh api ('repos/' + $Repository + '/pages') 2>$null
    if ($LASTEXITCODE -ne 0) { throw 'Pages is disabled or unavailable. Restore site availability before publishing.' }
    $info = Invoke-Api GET ('repos/' + $Repository)
}
$branch = $info.default_branch
if (-not $info.permissions.push) { throw 'The current GitHub identity needs write access to the publication repository.' }
$reference = Invoke-Api GET ('repos/' + $Repository + '/git/ref/heads/' + $branch)
$parent = $reference.object.sha
if ($ExpectedParent -and $parent -ne $ExpectedParent) { throw 'The public frontend changed during acceptance. Reconcile the new deployment before publishing.' }
$commit = Invoke-Api GET ('repos/' + $Repository + '/git/commits/' + $parent)
$priorTree = Invoke-Api GET ('repos/' + $Repository + '/git/trees/' + $commit.tree.sha + '?recursive=1')
$publicationDocs = @('README.md','AGENTS.md','CONTRIBUTING.md')
$allowedPrior = if ($created) { @('README.md') } else { $expected + $publicationDocs }
if ($priorTree.truncated -or @($priorTree.tree | Where-Object { ($_.type -eq 'blob' -and $_.path -notin $allowedPrior) -or ($_.type -eq 'tree' -and $_.path -notin @('vendor','vendor/mermaid','vendor/chat','vendor/chat/fonts','vendor/book-font')) -or $_.type -notin @('blob','tree') }).Count) {
    throw 'Existing repository contains files outside the frontend allowlist. Publication stopped.'
}
$priorBlobs = @{}
foreach ($item in $priorTree.tree | Where-Object type -EQ 'blob') { $priorBlobs[$item.path] = $item.sha }
$treeItems = @($files | ForEach-Object {
    $entry = @{path=[IO.Path]::GetRelativePath($ExportDirectory, $_.FullName).Replace('\','/');mode='100644';type='blob'}
    if ($_.Extension -in @('.woff2','.png')) {
        $bytes = [IO.File]::ReadAllBytes($_.FullName)
        $digest = [Security.Cryptography.IncrementalHash]::CreateHash([Security.Cryptography.HashAlgorithmName]::SHA1)
        try {
            $digest.AppendData([Text.Encoding]::ASCII.GetBytes('blob ' + $bytes.Length + [char]0))
            $digest.AppendData($bytes)
            $blobHash = [Convert]::ToHexString($digest.GetHashAndReset()).ToLowerInvariant()
        } finally { $digest.Dispose() }
        if ($priorBlobs[$entry.path] -eq $blobHash) { $entry.sha = $blobHash }
        else {
            # Blob writes are content-addressed. Reuse a verified object after
            # an interrupted upload, before retrying the same immutable bytes.
            for ($attempt = 1; $attempt -le 3; $attempt++) {
                & gh api ('repos/' + $Repository + '/git/blobs/' + $blobHash) --silent 2>$null
                if ($LASTEXITCODE -eq 0) { $entry.sha = $blobHash; break }
                try {
                    $blob = Invoke-Api POST ('repos/' + $Repository + '/git/blobs') @{content=[Convert]::ToBase64String($bytes);encoding='base64'}
                    if ($blob.sha -ne $blobHash) { throw 'Uploaded binary differs from its Git blob hash.' }
                    $entry.sha = $blob.sha; break
                } catch {
                    if ($attempt -eq 3) { throw }
                    Start-Sleep -Seconds (2 * $attempt)
                }
            }
        }
    } else { $entry.content = [IO.File]::ReadAllText($_.FullName) }
    $entry
})
foreach ($doc in $publicationDocs) {
    if ($priorBlobs.ContainsKey($doc)) { $treeItems += @{path=$doc;mode='100644';type='blob';sha=$priorBlobs[$doc]} }
}
$tree = Invoke-Api POST ('repos/' + $Repository + '/git/trees') @{tree=$treeItems}
$published = Invoke-Api POST ('repos/' + $Repository + '/git/commits') @{message='Publish verified learning frontend';tree=$tree.sha;parents=@($parent)}
$verifiedTree = Invoke-Api GET ('repos/' + $Repository + '/git/trees/' + $published.tree.sha + '?recursive=1')
$verifiedExpected = $expected + @($publicationDocs | Where-Object { $priorBlobs.ContainsKey($_) })
if ($verifiedTree.truncated -or @($verifiedTree.tree | Where-Object { $_.type -notin @('blob','tree') }).Count -or
    @(Compare-Object ($verifiedExpected | Sort-Object) (($verifiedTree.tree | Where-Object type -EQ 'blob').path | Sort-Object)).Count) {
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
