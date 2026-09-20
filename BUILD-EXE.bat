@echo off
setlocal
cd /d "%~dp0"
title Vortice - Gerar EXE

echo ==============================================
echo   VORTICE - GERANDO APLICATIVO WINDOWS

echo ==============================================
echo.

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0PYTHON-TOOLS.ps1" -Mode build
set "ERR=%ERRORLEVEL%"

if not "%ERR%"=="0" (
    echo.
    echo [ERRO] Nao foi possivel gerar o Vortice.exe.
    echo Codigo de saida: %ERR%
    echo.
    pause
    exit /b %ERR%
)

echo.
echo [OK] Vortice.exe gerado em:
echo %~dp0dist\Vortice.exe
echo.
pause
exit /b 0
