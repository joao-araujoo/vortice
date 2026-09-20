@echo off
setlocal EnableExtensions
cls
echo =====================================================
echo   VORTICE - CONFIGURAR CODEX CLI OFICIAL
echo =====================================================
echo.

set "CODEX_EXE=%LOCALAPPDATA%\Programs\OpenAI\Codex\bin\codex.exe"
set "INSTALL_RC=0"

call :verificar_existente
if defined CODEX_CMD goto :validar

echo Codex ainda nao foi localizado.
echo Instalando a CLI standalone oficial da OpenAI...
echo Nao e necessario instalar Node ou npm para este metodo.
echo.
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; $ProgressPreference='SilentlyContinue'; $env:CODEX_NON_INTERACTIVE='1'; $env:CODEX_INSTALL_DIR=Join-Path $env:LOCALAPPDATA 'Programs\OpenAI\Codex\bin'; New-Item -ItemType Directory -Force -Path $env:CODEX_INSTALL_DIR ^| Out-Null; irm https://chatgpt.com/codex/install.ps1 ^| iex"
set "INSTALL_RC=%ERRORLEVEL%"

rem IMPORTANTE: algumas versoes do instalador podem retornar erro somente na
rem verificacao final, depois de codex.exe ja ter sido instalado. Sempre teste
rem o executavel real antes de declarar falha.
call :verificar_existente
if defined CODEX_CMD goto :validar

echo.
echo O instalador terminou, mas nenhuma CLI funcional foi localizada.
if not "%INSTALL_RC%"=="0" echo O instalador oficial retornou codigo %INSTALL_RC%.
echo.
echo Abra o Vortice ^> Config ^> Dependencias ^> Configurar / reparar Codex.
echo La voce tambem pode usar "Localizar codex.exe".
echo Documentacao: https://developers.openai.com/codex/cli
pause
exit /b 1

:verificar_existente
set "CODEX_CMD="
if exist "%CODEX_EXE%" (
  "%CODEX_EXE%" --version >nul 2>nul
  if not errorlevel 1 set "CODEX_CMD=%CODEX_EXE%"
)
if defined CODEX_CMD exit /b 0
where codex >nul 2>nul
if errorlevel 1 exit /b 0
codex --version >nul 2>nul
if errorlevel 1 exit /b 0
set "CODEX_CMD=codex"
exit /b 0

:validar
echo.
echo Codex encontrado. Validando...
if /i "%CODEX_CMD%"=="codex" (
  codex --version
  if errorlevel 1 goto :falha_execucao
  codex login status >nul 2>nul
  if errorlevel 1 goto :login
) else (
  "%CODEX_CMD%" --version
  if errorlevel 1 goto :falha_execucao
  "%CODEX_CMD%" login status >nul 2>nul
  if errorlevel 1 goto :login
)
echo.
echo Codex pronto e autenticado.
pause
exit /b 0

:login
echo.
echo Codex instalado e funcional. Falta apenas entrar com sua conta do ChatGPT.
echo Uma janela sera aberta agora.
echo.
if /i "%CODEX_CMD%"=="codex" (
  start "" cmd /k codex login
) else (
  start "" "%CODEX_CMD%" login
)
echo Depois de terminar o login, abra o Vortice e clique em Verificar.
pause
exit /b 0

:falha_execucao
echo.
echo Encontrei o Codex, mas ele nao respondeu a codex --version.
echo Use Config ^> Dependencias ^> Configurar / reparar Codex.
pause
exit /b 1
