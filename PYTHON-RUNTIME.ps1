Set-StrictMode -Version 2.0

function Test-WindowsAppsPath {
    param([string]$Path)
    if ([string]::IsNullOrWhiteSpace($Path)) { return $false }
    return ($Path -match '(?i)\\Microsoft\\WindowsApps\\')
}

function Invoke-PythonProbe {
    param(
        [Parameter(Mandatory=$true)][string]$Exe,
        [string[]]$Prefix = @(),
        [switch]$RequireTkinter
    )

    if ([string]::IsNullOrWhiteSpace($Exe)) { return $false }
    if (-not (Test-Path -LiteralPath $Exe -PathType Leaf)) { return $false }
    if (Test-WindowsAppsPath -Path $Exe) { return $false }

    $probe = if ($RequireTkinter) {
        'import sys, tkinter; raise SystemExit(0 if sys.version_info >= (3,10) else 3)'
    } else {
        'import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 3)'
    }

    try {
        & $Exe @Prefix -c $probe *> $null
        return ($LASTEXITCODE -eq 0)
    } catch {
        return $false
    }
}

function Get-PythonRealExecutable {
    param(
        [Parameter(Mandatory=$true)][string]$Exe,
        [string[]]$Prefix = @()
    )
    try {
        $value = (& $Exe @Prefix -c 'import sys; print(sys.executable)' 2>$null | Select-Object -First 1)
        if ($LASTEXITCODE -ne 0) { return $null }
        $value = [string]$value
        if ([string]::IsNullOrWhiteSpace($value)) { return $null }
        return $value.Trim()
    } catch {
        return $null
    }
}

function Get-PyLauncherCandidates {
    $items = New-Object System.Collections.Generic.List[string]

    $cmd = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($cmd -and $cmd.Source) { $items.Add([string]$cmd.Source) }

    if ($env:LOCALAPPDATA) {
        $items.Add((Join-Path $env:LOCALAPPDATA 'Programs\Python\Launcher\py.exe'))
    }
    if ($env:WINDIR) {
        $items.Add((Join-Path $env:WINDIR 'py.exe'))
    }

    return @($items | Where-Object { $_ } | Select-Object -Unique)
}

function Get-PythonFromInstallRoot {
    param([string]$Root)

    if ([string]::IsNullOrWhiteSpace($Root)) { return @() }
    if (-not (Test-Path -LiteralPath $Root -PathType Container)) { return @() }

    $result = New-Object System.Collections.Generic.List[string]
    $dirs = @(Get-ChildItem -LiteralPath $Root -Directory -Filter 'Python*' -ErrorAction SilentlyContinue |
        Sort-Object Name -Descending)

    foreach ($dir in $dirs) {
        $candidate = Join-Path $dir.FullName 'python.exe'
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            $result.Add($candidate)
        }
    }
    return @($result)
}

function Find-VorticePython {
    # 1. Python Launcher (PATH + known locations). This remains the preferred runtime.
    foreach ($launcher in (Get-PyLauncherCandidates)) {
        if (Invoke-PythonProbe -Exe $launcher -Prefix @('-3') -RequireTkinter) {
            return [pscustomobject]@{
                Exe       = $launcher
                Prefix    = @('-3')
                RealExe   = (Get-PythonRealExecutable -Exe $launcher -Prefix @('-3'))
                Kind      = 'launcher'
            }
        }
    }

    # 2. Per-user official installs.
    if ($env:LOCALAPPDATA) {
        $userRoot = Join-Path $env:LOCALAPPDATA 'Programs\Python'
        foreach ($candidate in (Get-PythonFromInstallRoot -Root $userRoot)) {
            if (Invoke-PythonProbe -Exe $candidate -RequireTkinter) {
                return [pscustomobject]@{ Exe=$candidate; Prefix=@(); RealExe=$candidate; Kind='user' }
            }
        }
    }

    # 3. 64-bit system installs.
    if ($env:ProgramFiles) {
        foreach ($candidate in (Get-PythonFromInstallRoot -Root $env:ProgramFiles)) {
            if (Invoke-PythonProbe -Exe $candidate -RequireTkinter) {
                return [pscustomobject]@{ Exe=$candidate; Prefix=@(); RealExe=$candidate; Kind='machine64' }
            }
        }
    }

    # 4. 32-bit system installs.
    $pf86 = ${env:ProgramFiles(x86)}
    if ($pf86) {
        foreach ($candidate in (Get-PythonFromInstallRoot -Root $pf86)) {
            if (Invoke-PythonProbe -Exe $candidate -RequireTkinter) {
                return [pscustomobject]@{ Exe=$candidate; Prefix=@(); RealExe=$candidate; Kind='machine32' }
            }
        }
    }

    # 5. python.exe from PATH, rejecting Microsoft Store aliases.
    $python = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($python -and $python.Source -and -not (Test-WindowsAppsPath -Path $python.Source)) {
        if (Invoke-PythonProbe -Exe $python.Source -RequireTkinter) {
            return [pscustomobject]@{ Exe=$python.Source; Prefix=@(); RealExe=$python.Source; Kind='path-python' }
        }
    }

    # 6. python3.exe from PATH, with the same rejection.
    $python3 = Get-Command python3.exe -ErrorAction SilentlyContinue
    if ($python3 -and $python3.Source -and -not (Test-WindowsAppsPath -Path $python3.Source)) {
        if (Invoke-PythonProbe -Exe $python3.Source -RequireTkinter) {
            return [pscustomobject]@{ Exe=$python3.Source; Prefix=@(); RealExe=$python3.Source; Kind='path-python3' }
        }
    }

    return $null
}

function Test-Pip {
    param([Parameter(Mandatory=$true)]$Python)
    try {
        & $Python.Exe @($Python.Prefix) -m pip --version *> $null
        return ($LASTEXITCODE -eq 0)
    } catch {
        return $false
    }
}

function Ensure-Pip {
    param([Parameter(Mandatory=$true)]$Python)

    if (Test-Pip -Python $Python) { return $true }
    try {
        & $Python.Exe @($Python.Prefix) -m ensurepip --upgrade
        if ($LASTEXITCODE -ne 0) { return $false }
    } catch {
        return $false
    }
    return (Test-Pip -Python $Python)
}

function Get-PythonVersionText {
    param([Parameter(Mandatory=$true)]$Python)
    try {
        return ((& $Python.Exe @($Python.Prefix) --version 2>&1 | Select-Object -First 1) -as [string]).Trim()
    } catch {
        return 'Python desconhecido'
    }
}
