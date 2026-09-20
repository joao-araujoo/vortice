param(
    [ValidateSet('test','debug','build')]
    [string]$Mode = 'test'
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Runtime = Join-Path $Root 'PYTHON-RUNTIME.ps1'
if (-not (Test-Path -LiteralPath $Runtime -PathType Leaf)) { throw 'PYTHON-RUNTIME.ps1 nao encontrado.' }
. $Runtime

$Python = Find-VorticePython
if (-not $Python) {
    $Installer = Join-Path $Root 'INSTALL-PYTHON.ps1'
    $install = Start-Process -FilePath 'powershell.exe' -ArgumentList @(
        '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', ('"' + $Installer + '"'), '-Scope', 'machine'
    ) -Wait -PassThru
    if ($install.ExitCode -ne 0) { exit $install.ExitCode }
    $Python = Find-VorticePython
}
if (-not $Python) { throw 'Python 3.10+ real com Tkinter nao encontrado.' }

$App = Join-Path $Root 'app.py'
function Invoke-Python {
    param(
        [Parameter(Mandatory=$true)]
        [string[]]$PythonArgs
    )

    # Never use a parameter named $Args here: $args is an automatic
    # PowerShell variable and can swallow the arguments, causing `py -3`
    # to start the interactive REPL (>>>). Build one explicit argv array.
    $AllArgs = @()
    if ($Python.Prefix) { $AllArgs += @($Python.Prefix) }
    $AllArgs += @($PythonArgs)

    & $Python.Exe @AllArgs
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

switch ($Mode) {
    'test' {
        Invoke-Python -PythonArgs @($App, '--self-test')
    }
    'debug' {
        Invoke-Python -PythonArgs @($App)
    }
    'build' {
        Write-Host '[1/3] Validando o Vortice...'
        Invoke-Python -PythonArgs @($App, '--self-test')
        if (-not (Ensure-Pip -Python $Python)) { throw 'pip nao esta disponivel.' }
        Write-Host '[2/3] Instalando/atualizando PyInstaller...'
        Invoke-Python -PythonArgs @('-m', 'pip', 'install', '--upgrade', 'pyinstaller')
        Write-Host '[3/3] Gerando Vortice.exe...'
        Invoke-Python -PythonArgs @(
            '-m', 'PyInstaller', '--noconfirm', '--clean', '--onefile', '--windowed',
            '--name', 'Vortice', '--icon', (Join-Path $Root 'assets\app.ico'), '--add-data', ((Join-Path $Root 'assets') + ';assets'), $App
        )
        $Dist = Join-Path $Root 'dist'
        Write-Host "Pronto: $Dist\Vortice.exe" -ForegroundColor Green
        Start-Process -FilePath 'explorer.exe' -ArgumentList ('"' + $Dist + '"')
    }
}
