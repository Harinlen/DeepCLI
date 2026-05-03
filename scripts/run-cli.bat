@echo off
setlocal EnableExtensions EnableDelayedExpansion
rem Start DeepCLI CLI (ACP terminal client), ensuring the dev kernel is ready.

set "SCRIPT_DIR=%~dp0"
set "REPO_ROOT=%SCRIPT_DIR%.."
set "ORIGINAL_ARGS=%*"
set "KERNEL_PORT_VALUE=%KERNEL_PORT%"
if "%KERNEL_PORT_VALUE%"=="" set "KERNEL_PORT_VALUE=8200"
set "EXPLICIT_KERNEL_URL=%KERNEL_URL%"

set "INDEX=0"
:parse_args
if "%~1"=="" goto after_parse
set /a INDEX+=1
if "%~1"=="--help" goto cli_direct
if "%~1"=="-h" goto cli_direct
if "%~1"=="help" goto cli_direct
if "%~1"=="--port" (
  if not "%~2"=="" set "KERNEL_PORT_VALUE=%~2"
)
if "%~1"=="--kernel" (
  if not "%~2"=="" set "EXPLICIT_KERNEL_URL=%~2"
)
shift /1
goto parse_args

:cli_direct
cd /d "%REPO_ROOT%\src\cli"
bun run src/main.ts %ORIGINAL_ARGS%
exit /b %ERRORLEVEL%

:after_parse
if not "%EXPLICIT_KERNEL_URL%"=="" goto run_cli

set "READINESS_URL=http://127.0.0.1:%KERNEL_PORT_VALUE%/access/readiness"
powershell -NoProfile -ExecutionPolicy Bypass -Command "try { $r = Invoke-RestMethod -TimeoutSec 1 '%READINESS_URL%'; if ($r.default_route_ready -eq $true) { exit 0 } } catch {}; exit 1"
if "%ERRORLEVEL%"=="0" goto run_cli

set "LOG_PATH=%REPO_ROOT%\src\.run-kernel.log"
start "DeepCLI Kernel" /B cmd /C ""%SCRIPT_DIR%run-kernel.bat" --access-port %KERNEL_PORT_VALUE% --dev > "%LOG_PATH%" 2>&1"

set "READY=0"
for /L %%I in (1,1,160) do (
  powershell -NoProfile -ExecutionPolicy Bypass -Command "try { $r = Invoke-RestMethod -TimeoutSec 1 '%READINESS_URL%'; if ($r.default_route_ready -eq $true) { exit 0 } } catch {}; exit 1"
  if "!ERRORLEVEL!"=="0" (
    set "READY=1"
    goto ready_done
  )
  powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Sleep -Milliseconds 250"
)

:ready_done
if not "%READY%"=="1" (
  echo Kernel did not become ready. See %LOG_PATH% 1>&2
  exit /b 1
)

:run_cli
cd /d "%REPO_ROOT%\src\cli"
bun run src/main.ts %ORIGINAL_ARGS%
exit /b %ERRORLEVEL%
