@echo off
:: Start Mustang through the Supervisor in dev mode.

set "SCRIPT_DIR=%~dp0"
set "REPO_ROOT=%SCRIPT_DIR%.."

if "%MUSTANG_FLAGS_PATH%"=="" (
  > "%REPO_ROOT%\src\.mustang-dev-flags.yaml" echo transport:
  >> "%REPO_ROOT%\src\.mustang-dev-flags.yaml" echo   stack: acp
  set "MUSTANG_FLAGS_PATH=%REPO_ROOT%\src\.mustang-dev-flags.yaml"
)

cd /d "%REPO_ROOT%\src\kernel"
if "%~1"=="" (
  uv run python -m kernel.supervisor --access-port 8200 --dev
) else (
  uv run python -m kernel.supervisor %*
)
