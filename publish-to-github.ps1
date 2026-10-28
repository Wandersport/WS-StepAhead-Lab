# Explicit operator action: publish ONLY this StepAhead Lab source repository.
# This script never runs during the paper monitor and never publishes data/.
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$repo = 'https://github.com/Wandersport/WS-StepAhead-Lab.git'
$branch = 'main'
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    throw 'Git for Windows is required: https://git-scm.com/download/win'
}
if (-not (Test-Path -LiteralPath '.git')) {
    & git init -b $branch
    if ($LASTEXITCODE -ne 0) { throw 'git init failed' }
}
$remoteNames = @(& git remote)
if ($remoteNames -contains 'origin') {
    $origin = & git remote get-url origin
    if ($LASTEXITCODE -ne 0) { throw 'Cannot read GitHub origin' }
    if ($origin.Trim().ToLowerInvariant() -ne $repo.ToLowerInvariant()) {
        throw "Origin points to a different repository: $origin. No changes made."
    }
} else {
    & git remote add origin $repo
    if ($LASTEXITCODE -ne 0) { throw 'Failed to add GitHub origin' }
}
# Git requires an author identity for local commits. Do not force changes to
# the user's global Git configuration; use the verified owner as a local
# fallback only when no effective name/email is available.
$name = (& git config --get user.name | Out-String).Trim()
$email = (& git config --get user.email | Out-String).Trim()
if ([string]::IsNullOrWhiteSpace($name)) {
    & git config --local user.name 'Wandersport'
    if ($LASTEXITCODE -ne 0) { throw 'Unable to set repository-local Git author name' }
    $name = 'Wandersport'
}
if ([string]::IsNullOrWhiteSpace($email)) {
    # GitHub's documented numeric-ID noreply format; this id belongs to the
    # authenticated repository owner. This is only a commit author address.
    & git config --local user.email '83657128+Wandersport@users.noreply.github.com'
    if ($LASTEXITCODE -ne 0) { throw 'Unable to set repository-local Git author email' }
    $email = '83657128+Wandersport@users.noreply.github.com'
}
Write-Host "Git author for this repository: $name <$email>"
# Allowlist only source, documentation, tests, scripts, and workflow; never add data/, .venv/, secrets.
$paths = @('.gitattributes','.gitignore','.github','README.md','OPERATIONS.md','RESEARCH_HANDOFF.md',
           'REPO_AUDIT.md','START_HERE.txt','pyproject.toml','requirements.txt',
           'stepahead_lab','tests','start.bat','start.command','start.sh',
           'diagnose-clock.bat','publish-to-github.ps1','publish-to-github.bat')
& git add -- $paths
if ($LASTEXITCODE -ne 0) { throw 'git add failed' }
Write-Host 'Staged files (verify nothing sensitive):'
# Explicitly disable Git's interactive pager. Otherwise Windows can stop
# here showing a ':' pager prompt, leaving the repository uncommitted.
$staged = @(& git --no-pager diff --cached --name-only)
if ($LASTEXITCODE -ne 0) { throw 'Cannot inspect staged files' }
Write-Host "($($staged.Count)) staged source files"
$staged | ForEach-Object { Write-Host "  $_" }
# Reject unexpected pre-staged files (including private data/) rather than
# relying on .gitignore, which does not apply to files already staged.
foreach ($f in $staged) {
    $relative = ($f -replace '\\','/')
    $allowed = ($relative -in @('.gitattributes','.gitignore','README.md','OPERATIONS.md',
       'RESEARCH_HANDOFF.md','REPO_AUDIT.md','START_HERE.txt','pyproject.toml',
       'requirements.txt','start.bat','start.command','start.sh','diagnose-clock.bat',
       'publish-to-github.ps1','publish-to-github.bat')) -or
       $relative.StartsWith('.github/') -or $relative.StartsWith('stepahead_lab/') -or
       $relative.StartsWith('tests/')
    if (-not $allowed -or $relative -match '(^|/)(data|\.venv|__pycache__)/|(^|/)\.env|\.(pem|key)$') {
        throw "Unsafe or unexpected staged file: $relative. Unstage it before publishing."
    }
}
& git --no-pager diff --cached --quiet
$diffCode = $LASTEXITCODE
if ($diffCode -gt 1) { throw "git diff failed with code $diffCode" }
if ($diffCode -eq 1) {
    & git commit -m 'StepAhead Lab v1.1.2: noninteractive Git publication and Mac launcher'
    if ($LASTEXITCODE -ne 0) { throw 'git commit failed' }
} else {
    Write-Host 'No new source changes to commit.'
}
# Existing empty Windows repos may still be on 'master' because git init's
# --initial-branch option is ignored when .git already exists. Rename the
# LOCAL current branch only; never force the remote.
$currentBranch = (& git symbolic-ref --short HEAD | Out-String).Trim()
if ($LASTEXITCODE -ne 0) { throw 'Unable to determine local Git branch' }
if ($currentBranch -ne $branch) {
    Write-Host "Renaming local branch '$currentBranch' to '$branch'"
    & git branch -M $branch
    if ($LASTEXITCODE -ne 0) { throw 'Unable to rename local branch to main' }
}
Write-Host "Publishing to $repo (no force push; credentials may be requested)."
& git push -u origin $branch
if ($LASTEXITCODE -ne 0) { throw 'Push failed. Check Git Credential Manager / GitHub sign-in, and remote branch state.' }
Write-Host 'PUBLISHED: https://github.com/Wandersport/WS-StepAhead-Lab'
