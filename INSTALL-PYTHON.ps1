param(
    [ValidateSet('user','machine')]
    [string]$Scope = 'user'
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Runtime = Join-Path $Root 'PYTHON-RUNTIME.ps1'

if (-not (Test-Path -LiteralPath $Runtime -PathType Leaf)) {
    Write-Error 'PYTHON-RUNTIME.ps1 nao foi encontrado.'
    exit 20
}
. $Runtime

function Test-IsAdministrator {
    try {
        $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
        $principal = New-Object Security.Principal.WindowsPrincipal($identity)
        return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    } catch { return $false }
}

function Show-Status {
    param([string]$Label, [string]$Value, [ConsoleColor]$Color = [ConsoleColor]::Gray)
    Write-Host ($Label.PadRight(14) + $Value) -ForegroundColor $Color
}

try {
    Write-Host '====================================================='
    Write-Host '  VORTICE - PYTHON SETUP'
    Write-Host '====================================================='
    Write-Host ''

    $Python = Find-VorticePython
    if ($Python) {
        if (-not (Ensure-Pip -Python $Python)) {
            Write-Error 'Python foi encontrado, mas pip nao pode ser validado nem recuperado com ensurepip.'
            exit 21
        }

        Show-Status 'PYTHON:' (Get-PythonVersionText -Python $Python) Green
        Show-Status 'EXECUTAVEL:' ([string]$Python.RealExe) Green
        Show-Status 'PY LAUNCHER:' ($(if ($Python.Kind -eq 'launcher') { 'disponivel' } else { 'nao utilizado' })) Green
        Show-Status 'TKINTER:' 'OK' Green
        Show-Status 'PIP:' ((& $Python.Exe @($Python.Prefix) -m pip --version 2>&1 | Select-Object -First 1) -as [string]) Green
        exit 0
    }

    $winget = Get-Command winget.exe -ErrorAction SilentlyContinue
    if (-not $winget -or -not $winget.Source) {
        Write-Error 'winget nao foi encontrado. Nao e possivel instalar Python 3.13 automaticamente.'
        exit 22
    }

    if ($Scope -eq 'machine' -and -not (Test-IsAdministrator)) {
        Write-Host 'A instalacao global requer permissao de administrador. Solicitando elevacao...' -ForegroundColor Yellow
        $self = $MyInvocation.MyCommand.Path
        $args = '-NoProfile -ExecutionPolicy Bypass -File "' + $self + '" -Scope machine'
        $elevated = Start-Process -FilePath 'powershell.exe' -ArgumentList $args -Verb RunAs -Wait -PassThru
        exit $elevated.ExitCode
    }

    Write-Host ("Python real nao encontrado. Instalando Python 3.13 x64 (escopo: $Scope)...") -ForegroundColor Yellow

    $wingetArgs = @(
        'install',
        '--id', 'Python.Python.3.13',
        '-e',
        '--architecture', 'x64',
        '--scope', $Scope,
        '--source', 'winget',
        '--accept-package-agreements',
        '--accept-source-agreements',
        '--silent',
        '--disable-interactivity'
    )

    & $winget.Source @wingetArgs
    $installCode = $LASTEXITCODE
    if ($installCode -ne 0) {
        # Winget may report an already-installed/up-to-date package with a nonzero code.
        # Do not trust that status alone: the filesystem re-scan below is authoritative.
        Write-Host "winget retornou codigo $installCode; validando o runtime instalado..." -ForegroundColor Yellow
    }

    $Python = $null
    for ($i = 0; $i -lt 20 -and -not $Python; $i++) {
        Start-Sleep -Milliseconds 500
        $Python = Find-VorticePython
    }

    if (-not $Python) {
        Write-Error 'Python 3.13 nao foi localizado apos a instalacao. Nenhum alias WindowsApps foi aceito.'
        exit 23
    }

    if (-not (Ensure-Pip -Python $Python)) {
        Write-Error 'Python/Tkinter estao funcionais, mas pip nao pode ser preparado com ensurepip.'
        exit 24
    }

    # Final validation is real execution, including Tkinter and the minimum version.
    if (-not (Invoke-PythonProbe -Exe $Python.Exe -Prefix @($Python.Prefix) -RequireTkinter)) {
        Write-Error 'A validacao final de Python + Tkinter falhou.'
        exit 25
    }

    $pipText = ((& $Python.Exe @($Python.Prefix) -m pip --version 2>&1 | Select-Object -First 1) -as [string])
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($pipText)) {
        Write-Error 'A validacao final do pip falhou.'
        exit 26
    }

    Write-Host ''
    Show-Status 'PYTHON:' (Get-PythonVersionText -Python $Python) Green
    Show-Status 'EXECUTAVEL:' ([string]$Python.RealExe) Green
    Show-Status 'PY LAUNCHER:' ($(if ($Python.Kind -eq 'launcher') { 'disponivel' } else { 'nao utilizado' })) Green
    Show-Status 'TKINTER:' 'OK' Green
    Show-Status 'PIP:' $pipText Green
    exit 0
} catch {
    Write-Error $_.Exception.Message
    exit 99
}
