@echo off
setlocal
cd /d "%~dp0"
title Vortice - Build Public Release
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\build-release.ps1"
if errorlevel 1 (
  echo.
  echo [ERRO] Build de release falhou.
  pause
  exit /b 1
)
echo.
echo [OK] Release pronta em .\release
pause
