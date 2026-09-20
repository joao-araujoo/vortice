param(
  [string]$RepoName = "vortice",
  [string]$Version = "v4.4.1"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root

function Resolve-Tool {
  param(
    [Parameter(Mandatory = $true)][string]$Name,
    [string[]]$Candidates = @()
  )

  $cmd = Get-Command $Name -ErrorAction SilentlyContinue
  if ($cmd -and $cmd.Source) { return [string]$cmd.Source }

  foreach ($candidate in $Candidates) {
    if ($candidate -and (Test-Path -LiteralPath $candidate -PathType Leaf)) {
      return [string]$candidate
    }
  }

  return $null
}

function Require-Winget {
  $winget = Get-Command "winget.exe" -ErrorAction SilentlyContinue
  if (-not $winget -or -not $winget.Source) {
    throw "winget was not found. Install App Installer from Microsoft Store, or install Git and GitHub CLI manually."
  }
  return [string]$winget.Source
}

function Run-Checked {
  param(
    [Parameter(Mandatory = $true)][string]$Exe,
    [Parameter(Mandatory = $true)][string[]]$Arguments,
    [Parameter(Mandatory = $true)][string]$ErrorMessage
  )

  & $Exe @Arguments
  if ($LASTEXITCODE -ne 0) {
    throw "$ErrorMessage (exit code $LASTEXITCODE)."
  }
}

function Get-GitOutput {
  param([Parameter(Mandatory = $true)][string[]]$Arguments)
  $output = (& $script:git @Arguments 2>$null | Out-String).Trim()
  return $output
}

Write-Host "=====================================================" -ForegroundColor DarkGray
Write-Host "  VORTICE - PUBLICAR NO GITHUB" -ForegroundColor Cyan
Write-Host "=====================================================" -ForegroundColor DarkGray
Write-Host ""

$gitCandidates = @(
  (Join-Path $env:ProgramFiles "Git\cmd\git.exe"),
  $(if (${env:ProgramFiles(x86)}) { Join-Path ${env:ProgramFiles(x86)} "Git\cmd\git.exe" } else { $null })
) | Where-Object { $_ }

$script:git = Resolve-Tool -Name "git.exe" -Candidates $gitCandidates
if (-not $script:git) {
  Write-Host "Git nao encontrado. Tentando instalar com winget..." -ForegroundColor Yellow
  $winget = Require-Winget
  Run-Checked -Exe $winget -Arguments @(
    "install", "--id", "Git.Git", "-e",
    "--accept-source-agreements", "--accept-package-agreements",
    "--disable-interactivity"
  ) -ErrorMessage "Falha ao instalar Git"
  $script:git = Resolve-Tool -Name "git.exe" -Candidates $gitCandidates
}
if (-not $script:git) {
  throw "Git nao foi localizado depois da instalacao. Feche e abra este script novamente, ou instale Git.Git manualmente."
}

$ghCandidates = @(
  (Join-Path $env:ProgramFiles "GitHub CLI\gh.exe"),
  $(if (${env:ProgramFiles(x86)}) { Join-Path ${env:ProgramFiles(x86)} "GitHub CLI\gh.exe" } else { $null })
) | Where-Object { $_ }

$gh = Resolve-Tool -Name "gh.exe" -Candidates $ghCandidates
if (-not $gh) {
  Write-Host "GitHub CLI nao encontrado. Tentando instalar com winget..." -ForegroundColor Yellow
  $winget = Require-Winget
  Run-Checked -Exe $winget -Arguments @(
    "install", "--id", "GitHub.cli", "-e",
    "--accept-source-agreements", "--accept-package-agreements",
    "--disable-interactivity"
  ) -ErrorMessage "Falha ao instalar GitHub CLI"
  $gh = Resolve-Tool -Name "gh.exe" -Candidates $ghCandidates
}
if (-not $gh) {
  throw "GitHub CLI nao foi localizado depois da instalacao. Feche e abra este script novamente, ou instale GitHub.cli manualmente."
}

$oldPreference = $ErrorActionPreference
$ErrorActionPreference = "SilentlyContinue"
& $gh auth status *> $null
$authOk = ($LASTEXITCODE -eq 0)
$ErrorActionPreference = $oldPreference
if (-not $authOk) {
  Write-Host "Abrindo login do GitHub..." -ForegroundColor Cyan
  Run-Checked -Exe $gh -Arguments @("auth", "login", "--web", "--git-protocol", "https") -ErrorMessage "Login do GitHub nao concluido"
}

# Make HTTPS pushes use the same authenticated GitHub CLI session.
$oldPreference = $ErrorActionPreference
$ErrorActionPreference = "SilentlyContinue"
& $gh auth setup-git *> $null
$setupGitOk = ($LASTEXITCODE -eq 0)
$ErrorActionPreference = $oldPreference
if (-not $setupGitOk) {
  Write-Host "Aviso: nao consegui configurar o helper do Git automaticamente. O push ainda sera tentado." -ForegroundColor Yellow
}

$owner = ((& $gh api user --jq ".login" 2>$null | Out-String).Trim())
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($owner)) {
  throw "Nao consegui descobrir o usuario autenticado no GitHub."
}
Write-Host "GitHub: $owner" -ForegroundColor Green


$full = "$owner/$RepoName"
$expectedOrigin = "https://github.com/$full.git"

# Discover/create the remote repository before creating the local commit. This is
# important when publishing an update from a freshly extracted GitHubReady ZIP:
# the folder has no .git history, while the GitHub repository already has main.
$oldPreference = $ErrorActionPreference
$ErrorActionPreference = "SilentlyContinue"
& $gh repo view $full --json name *> $null
$repoExists = ($LASTEXITCODE -eq 0)
$ErrorActionPreference = $oldPreference

if (-not $repoExists) {
  Write-Host "Criando repositorio publico $full..." -ForegroundColor Cyan
  Run-Checked -Exe $gh -Arguments @(
    "repo", "create", $RepoName,
    "--public",
    "--description", "Vortice - desktop local-first para desenvolvimento com Tasks, contexto, backups e ChatGPT."
  ) -ErrorMessage "Falha ao criar o repositorio no GitHub"
} else {
  Write-Host "Repositorio $full ja existe. Vou atualizar preservando o historico." -ForegroundColor Yellow
}

if (-not (Test-Path -LiteralPath ".git" -PathType Container)) {
  Run-Checked -Exe $script:git -Arguments @("init") -ErrorMessage "Falha ao inicializar o Git"
}
Run-Checked -Exe $script:git -Arguments @("branch", "-M", "main") -ErrorMessage "Falha ao definir a branch main"

# Keep commit identity and line-ending behavior local to this repository.
$name = Get-GitOutput -Arguments @("config", "--get", "user.name")
$email = Get-GitOutput -Arguments @("config", "--get", "user.email")
if ([string]::IsNullOrWhiteSpace($name)) {
  Run-Checked -Exe $script:git -Arguments @("config", "user.name", $owner) -ErrorMessage "Falha ao configurar git user.name"
}
if ([string]::IsNullOrWhiteSpace($email)) {
  Run-Checked -Exe $script:git -Arguments @("config", "user.email", "$owner@users.noreply.github.com") -ErrorMessage "Falha ao configurar git user.email"
}
Run-Checked -Exe $script:git -Arguments @("config", "core.autocrlf", "false") -ErrorMessage "Falha ao configurar line endings do repositorio"

# Repair origin safely. Never query origin before checking whether it exists.
$remoteNamesText = Get-GitOutput -Arguments @("remote")
$remoteNames = @()
if (-not [string]::IsNullOrWhiteSpace($remoteNamesText)) {
  $remoteNames = @($remoteNamesText -split "`r?`n" | ForEach-Object { $_.Trim() } | Where-Object { $_ })
}
if ($remoteNames -notcontains "origin") {
  Write-Host "Remote origin ausente. Adicionando $expectedOrigin" -ForegroundColor Cyan
  Run-Checked -Exe $script:git -Arguments @("remote", "add", "origin", $expectedOrigin) -ErrorMessage "Falha ao adicionar o remote origin"
} else {
  $origin = Get-GitOutput -Arguments @("remote", "get-url", "origin")
  if ([string]::IsNullOrWhiteSpace($origin)) {
    Run-Checked -Exe $script:git -Arguments @("remote", "set-url", "origin", $expectedOrigin) -ErrorMessage "Falha ao reparar o remote origin"
  } elseif ($origin -ne $expectedOrigin -and $origin -notmatch [regex]::Escape("github.com/$full")) {
    Write-Host "Atualizando remote origin para $expectedOrigin" -ForegroundColor Yellow
    Run-Checked -Exe $script:git -Arguments @("remote", "set-url", "origin", $expectedOrigin) -ErrorMessage "Falha ao atualizar o remote origin"
  }
}

# If main already exists on GitHub, make that commit the parent of this package
# WITHOUT checking it out over the extracted files. `reset --mixed` moves HEAD
# to origin/main but deliberately keeps the current package in the working tree.
# This turns a fresh GitHubReady ZIP into a normal fast-forward update and also
# repairs folders left in the unrelated-root state by older publisher versions.
$remoteMain = Get-GitOutput -Arguments @("ls-remote", "--heads", "origin", "refs/heads/main")
if (-not [string]::IsNullOrWhiteSpace($remoteMain)) {
  Write-Host "Sincronizando com origin/main sem sobrescrever os arquivos desta versao..." -ForegroundColor Cyan
  Run-Checked -Exe $script:git -Arguments @("fetch", "origin", "main") -ErrorMessage "Falha ao buscar origin/main"
  Run-Checked -Exe $script:git -Arguments @("reset", "--mixed", "origin/main") -ErrorMessage "Falha ao alinhar o historico local com origin/main"
}

Run-Checked -Exe $script:git -Arguments @("add", "-A") -ErrorMessage "Falha ao preparar os arquivos para commit"
& $script:git diff --cached --quiet
$hasChanges = ($LASTEXITCODE -ne 0)
if ($hasChanges) {
  Run-Checked -Exe $script:git -Arguments @("commit", "-m", "chore: Vortice 4.4.1 Public Beta") -ErrorMessage "Falha ao criar o commit"
} else {
  Write-Host "Nenhuma alteracao de arquivos para commitar. Continuando com o HEAD atual." -ForegroundColor DarkGray
}

$head = Get-GitOutput -Arguments @("rev-parse", "--verify", "HEAD")
if ([string]::IsNullOrWhiteSpace($head)) {
  throw "Nenhum commit local foi encontrado. Verifique se a pasta contem os arquivos do Vortice."
}

Write-Host "Enviando main..." -ForegroundColor Cyan
Run-Checked -Exe $script:git -Arguments @("push", "-u", "origin", "main") -ErrorMessage "Falha ao enviar a branch main"

# Repository metadata is useful but must not block publication.
& $gh repo edit $full `
  --enable-issues=true `
  --enable-wiki=false `
  --add-topic vortice `
  --add-topic developer-tools `
  --add-topic chatgpt `
  --add-topic codex `
  --add-topic windows `
  --add-topic productivity *> $null
if ($LASTEXITCODE -ne 0) {
  Write-Host "Aviso: nao consegui atualizar todos os topics. A publicacao vai continuar." -ForegroundColor Yellow
}

$remoteTagText = Get-GitOutput -Arguments @("ls-remote", "--tags", "origin", "refs/tags/$Version")
$localTagText = Get-GitOutput -Arguments @("tag", "--list", $Version)

if (-not [string]::IsNullOrWhiteSpace($remoteTagText)) {
  Write-Host "A tag $Version ja existe no GitHub. Nao vou sobrescrever uma release publicada." -ForegroundColor Yellow
} else {
  if (-not [string]::IsNullOrWhiteSpace($localTagText)) {
    $tagCommit = Get-GitOutput -Arguments @("rev-list", "-n", "1", $Version)
    $currentHead = Get-GitOutput -Arguments @("rev-parse", "HEAD")
    if ($tagCommit -ne $currentHead) {
      Write-Host "Atualizando a tag local $Version para o commit atual (ela ainda nao foi publicada)." -ForegroundColor Yellow
      Run-Checked -Exe $script:git -Arguments @("tag", "-d", $Version) -ErrorMessage "Falha ao remover a tag local antiga"
      $localTagText = ""
    }
  }

  if ([string]::IsNullOrWhiteSpace($localTagText)) {
    Run-Checked -Exe $script:git -Arguments @("tag", "-a", $Version, "-m", "Vortice 4.4.1 Public Beta") -ErrorMessage "Falha ao criar a tag $Version"
  }
  Run-Checked -Exe $script:git -Arguments @("push", "origin", $Version) -ErrorMessage "Falha ao enviar a tag $Version"
}

$actionsUrl = "https://github.com/$full/actions"
$releasesUrl = "https://github.com/$full/releases"

Write-Host ""
Write-Host "Pronto. O GitHub Actions vai montar o instalador e a versao portatil." -ForegroundColor Green
Write-Host "Actions:  $actionsUrl"
Write-Host "Releases: $releasesUrl"
Write-Host ""
Write-Host "Pode fechar esta janela. A compilacao acontece no GitHub." -ForegroundColor DarkGray

try {
  Start-Process $actionsUrl | Out-Null
} catch {
  Write-Host "Nao consegui abrir o navegador automaticamente. Abra o link de Actions acima." -ForegroundColor Yellow
}
