@echo off
setlocal EnableExtensions
cls
echo =====================================================
echo   VORTICE - INSTALAR CODEX CLI OFICIAL
echo =====================================================
echo.

where codex >nul 2>nul
if %errorlevel%==0 (
  echo Codex ja foi encontrado neste computador.
  codex --version
  echo.
  echo Status da conta:
  codex login status
  echo.
  pause
  exit /b 0
)

echo O instalador oficial da OpenAI sera executado pelo PowerShell.
echo.
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "irm https://chatgpt.com/codex/install.ps1 ^| iex"
if errorlevel 1 goto :falha

echo.
echo Instalacao concluida.
echo.
echo Feche e abra um novo PowerShell e execute:
echo.
echo   codex
echo.
echo Depois escolha Sign in with ChatGPT.
echo.
pause
exit /b 0

:falha
echo.
echo A instalacao do Codex falhou. Veja a mensagem acima.
echo Como alternativa oficial, se voce ja tiver npm:
echo.
echo   npm install -g @openai/codex
echo.
pause
exit /b 1
