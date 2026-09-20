param(
    [string]$Python = "python"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root

Write-Host "[1/6] Self-test" -ForegroundColor Cyan
& $Python app.py --self-test
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "[2/6] PyInstaller" -ForegroundColor Cyan
& $Python -m pip install --upgrade pyinstaller
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Remove-Item build, dist, release -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force release | Out-Null

Write-Host "[3/6] Build onedir" -ForegroundColor Cyan
& $Python -m PyInstaller `
  --noconfirm --clean --onedir --windowed `
  --name Vortice `
  --icon "$Root\assets\app.ico" `
  --version-file "$Root\installer\version_info.txt" `
  --add-data "$Root\assets;assets" `
  "$Root\app.py"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "[4/6] Portable ZIP" -ForegroundColor Cyan
Copy-Item "$Root\README.txt", "$Root\LICENSE", "$Root\PRIVACY.md", "$Root\SECURITY.md" -Destination "$Root\dist\Vortice"
Compress-Archive -Path "$Root\dist\Vortice\*" -DestinationPath "$Root\release\Vortice-Portable.zip" -CompressionLevel Optimal

Write-Host "[5/6] Installer" -ForegroundColor Cyan
$IsccCandidates = @(
  "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
  "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
) | Where-Object { $_ -and (Test-Path $_) }
if (-not $IsccCandidates) {
  throw "Inno Setup 6 nao encontrado. Instale com: winget install JRSoftware.InnoSetup"
}
& $IsccCandidates[0] "$Root\installer\Vortice.iss"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "[6/6] SHA-256" -ForegroundColor Cyan
$files = @("$Root\release\Vortice-Setup.exe", "$Root\release\Vortice-Portable.zip")
$lines = foreach ($f in $files) {
  $h = Get-FileHash -Algorithm SHA256 $f
  "$($h.Hash.ToLower())  $([IO.Path]::GetFileName($f))"
}
$lines | Set-Content -Encoding ascii "$Root\release\SHA256SUMS.txt"

Write-Host "Pronto em $Root\release" -ForegroundColor Green
