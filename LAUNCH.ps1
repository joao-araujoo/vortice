$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Runtime = Join-Path $Root 'PYTHON-RUNTIME.ps1'

function Show-VorticeError {
    param([string]$Message)
    try {
        Add-Type -AssemblyName System.Windows.Forms
        [System.Windows.Forms.MessageBox]::Show(
            $Message,
            'Vortice',
            [System.Windows.Forms.MessageBoxButtons]::OK,
            [System.Windows.Forms.MessageBoxIcon]::Error
        ) | Out-Null
    } catch {}
}

try {
    if (-not (Test-Path -LiteralPath $Runtime -PathType Leaf)) {
        throw 'Arquivo PYTHON-RUNTIME.ps1 nao foi encontrado.'
    }
    . $Runtime

    $Python = Find-VorticePython
    if (-not $Python) {
        $Installer = Join-Path $Root 'INSTALL-PYTHON.ps1'
        if (-not (Test-Path -LiteralPath $Installer -PathType Leaf)) {
            throw 'Python 3.10+ com Tkinter nao foi encontrado e o instalador esta ausente.'
        }

        # Vortice requests the machine-wide install so a freshly installed runtime
        # can be reused by every project. The installer elevates itself if needed.
        $install = Start-Process -FilePath 'powershell.exe' -ArgumentList @(
            '-NoProfile',
            '-ExecutionPolicy', 'Bypass',
            '-File', ('"' + $Installer + '"'),
            '-Scope', 'machine'
        ) -Wait -PassThru

        if ($install.ExitCode -ne 0) {
            throw "A instalacao do Python falhou (codigo $($install.ExitCode))."
        }

        # Re-scan the filesystem directly. No PATH refresh/reboot/new terminal is required.
        $Python = Find-VorticePython
    }

    if (-not $Python) {
        throw 'Python 3.10+ real com Tkinter nao foi localizado. O alias da Microsoft Store nao e aceito.'
    }

    $App = Join-Path $Root 'app.py'
    if (-not (Test-Path -LiteralPath $App -PathType Leaf)) {
        throw 'app.py nao foi encontrado.'
    }

    # Start-Process joins ArgumentList into one command line. Quote app.py explicitly
    # so directories containing spaces remain valid. The launcher remains preferred.
    $parts = New-Object System.Collections.Generic.List[string]
    foreach ($prefix in @($Python.Prefix)) { $parts.Add($prefix) }
    $parts.Add(('"' + $App.Replace('"', '\"') + '"'))
    $argumentLine = ($parts -join ' ')

    Start-Process -FilePath $Python.Exe `
        -ArgumentList $argumentLine `
        -WorkingDirectory $Root `
        -WindowStyle Hidden | Out-Null
    exit 0
} catch {
    Show-VorticeError -Message ("Nao foi possivel iniciar o Vortice.`r`n`r`n" + $_.Exception.Message)
    exit 1
}
