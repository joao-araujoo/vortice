@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0INSTALL-PYTHON.ps1" -Scope machine
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" pause
exit /b %RC%
