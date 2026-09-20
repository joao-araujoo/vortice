@echo off
setlocal
cd /d "%~dp0"
title Vortice - Publicar no GitHub
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\publish-github.ps1"
if errorlevel 1 (
  echo.
  echo [ERRO] Nao foi possivel publicar. Leia a mensagem acima.
  pause
  exit /b 1
)
echo.
echo [OK] Codigo enviado. Agora e so esperar o GitHub Actions terminar a Release.
pause
