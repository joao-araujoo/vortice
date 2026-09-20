@echo off
setlocal EnableExtensions
cls
echo =====================================================
echo   VORTICE - CONFIGURAR CODEX CLI OFICIAL
echo =====================================================
echo.

where codex >nul 2>nul
if %errorlevel%==0 goto :verificar

echo Instalando a Codex CLI standalone oficial da OpenAI...
echo Nao e necessario instalar Node ou npm para este metodo.
echo.
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; $ProgressPreference='SilentlyContinue'; $env:CODEX_NON_INTERACTIVE='1'; irm https://chatgpt.com/codex/install.ps1 ^| iex"
if errorlevel 1 goto :falha

:verificar
echo.
echo Verificando...
set "CODEX_EXE=%LOCALAPPDATA%\Programs\OpenAI\Codex\bin\codex.exe"
if exist "%CODEX_EXE%" (
  "%CODEX_EXE%" --version
  echo.
  "%CODEX_EXE%" login status >nul 2>nul
  if errorlevel 1 goto :login
  echo Codex pronto e autenticado.
  pause
  exit /b 0
)

where codex >nul 2>nul
if errorlevel 1 goto :naoencontrado
codex --version
codex login status >nul 2>nul
if errorlevel 1 goto :login
echo Codex pronto e autenticado.
pause
exit /b 0

:login
echo.
echo Falta entrar com a sua conta do ChatGPT.
echo Uma janela do Codex sera aberta agora.
echo.
if exist "%CODEX_EXE%" (
  start "" "%CODEX_EXE%" login
) else (
  start "" cmd /k codex login
)
echo Depois de terminar o login, abra o Vortice e clique em Verificar.
pause
exit /b 0

:naoencontrado
echo.
echo O instalador terminou, mas codex.exe ainda nao foi localizado.
echo Abra o Vortice ^> Config ^> Dependencias ^> Configurar / reparar Codex.
echo La voce tambem pode localizar codex.exe manualmente.
pause
exit /b 1

:falha
echo.
echo A instalacao oficial do Codex falhou. Veja a mensagem acima.
echo Documentacao: https://developers.openai.com/codex/cli
pause
exit /b 1
